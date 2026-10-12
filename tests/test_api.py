"""API + scheduler tests against a throwaway database (the app's lifespan/scheduler isn't started)."""

import dataclasses
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app import api, db, google_auth, scheduler
from app.bills import dashboard, manual, service
from app.config import settings
from app.main import app
from app.models import Bill, BillEmail, ManualCost, SyncRun


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(service, "current_month", lambda: "2026-10")
    monkeypatch.setattr(dashboard, "today", lambda: date(2026, 10, 11))
    monkeypatch.setattr(google_auth, "connected_account", lambda: "me@example.com")
    monkeypatch.setattr(service, "refresh_payment_methods", lambda: 0)
    test_settings = dataclasses.replace(settings, data_dir=tmp_path, bill_senders_file=tmp_path / "none.toml")
    monkeypatch.setattr(api, "settings", test_settings)

    def add(biller, category, amount, due, month=None):
        with db.session_scope() as s:
            email = BillEmail(gmail_message_id=f"{biller}{due}", thread_id="t", sender_email="x@y.z",
                              subject=f"{biller} bill", received_at=datetime(2026, 9, 25))
            s.add(email)
            s.flush()
            s.add(Bill(email_id=email.id, biller=biller, category=category, amount=Decimal(amount),
                       due_date=date.fromisoformat(due), month=month or due[:7], status="parsed"))

    add("Ottawa Water", "utilities", "153.78", "2026-10-08")
    add("Bell Internet", "phone_internet", "55.50", "2026-10-09")
    add("Hydro Ottawa", "utilities", "121.83", "2026-10-19")
    add("Triangle Mastercard", "credit_card", "92.41", "2026-10-15")
    add("Bell Internet", "phone_internet", "56.50", "2026-09-08")
    add("Ottawa Property Tax", "housing", "2427.37", "2026-06-18")
    return TestClient(app, base_url="http://localhost")


def test_dashboard_month(client):
    d = client.get("/api/dashboard?month=2026-10").json()
    assert d["month"] == "2026-10" and d["next_month"] is None
    assert d["total"] == pytest.approx(331.11) and d["count"] == 3
    assert d["remaining"] == pytest.approx(121.83)
    assert d["next_bill"]["biller"] == "Hydro Ottawa"
    assert [b["biller"] for b in d["bills"]] == ["Ottawa Water", "Bell Internet", "Hydro Ottawa"]
    assert [b["upcoming"] for b in d["bills"]] == [False, False, True]
    assert d["statements"] == {"count": 1, "total": pytest.approx(92.41)}
    assert d["categories"][0] == {"category": "utilities", "total": pytest.approx(275.61)}


def test_dashboard_never_shows_future_months(client):
    d = client.get("/api/dashboard?month=2027-01").json()
    assert d["month"] == "2026-10"
    assert client.get("/api/dashboard?month=garbage").json()["month"] == "2026-10"
    history = [h["month"] for h in d["history"]]
    assert len(history) == 12 and history[-1] == "2026-10" and max(history) == "2026-10"

    past = client.get("/api/dashboard?month=2026-09").json()
    assert past["next_month"] == "2026-10"
    assert past["remaining"] == 0 and past["next_bill"] is None
    assert [h["month"] for h in past["history"]][-1] == "2026-10"  # trend still runs to today


def test_edit_bill(client):
    bill_id = client.get("/api/dashboard").json()["bills"][0]["id"]
    assert client.patch(f"/api/bills/{bill_id}", json={"amount": "$160.00", "due_date": "2026-10-08"}).status_code == 200
    bill = client.get("/api/dashboard").json()["bills"][0]
    assert (bill["amount"], bill["status"]) == (160.0, "manual")
    assert client.patch(f"/api/bills/{bill_id}", json={"amount": "abc"}).status_code == 422


def test_settings_reports_missing_senders_and_failed_sync(client, monkeypatch):
    def not_connected(month, force=False):
        raise google_auth.GmailNotConnected("Gmail access expired. Connect Gmail again.")

    monkeypatch.setattr(service, "sync_month", not_connected)
    run = client.post("/api/sync", json={}).json()
    assert run["ok"] is False and "expired" in run["summary"]

    s = client.get("/api/settings").json()
    assert s["gmail_account"] == "me@example.com"
    assert s["billers"] == [] and "No bill senders" in s["config_error"]
    assert s["sync"]["last_run"]["summary"] == run["summary"]
    assert s["sync"]["interval_hours"] == scheduler.INTERVAL.total_seconds() / 3600


def test_scheduler_runs_when_due_and_one_at_a_time(client, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "sync_month", lambda m, force=False: calls.append(m) or service.SyncResult(m))
    assert scheduler.is_due()
    run = scheduler.run_sync("auto")
    assert run.ok and calls == ["2026-10", "2026-11"] and run.summary.startswith("No new bills")
    assert not scheduler.is_due()
    assert scheduler.is_due(now=datetime.now() + scheduler.INTERVAL + timedelta(minutes=1))

    scheduler._lock.acquire()
    try:
        with pytest.raises(scheduler.SyncInProgress):
            scheduler.run_sync("manual")
        assert client.post("/api/sync", json={}).status_code == 409
    finally:
        scheduler._lock.release()


def test_spa_routes_do_not_swallow_api_404s(client):
    assert client.get("/api/nope").status_code == 404
    assert client.get("/settings").status_code in (200, 503)  # index.html, or "not built yet"


def test_fixed_costs_spread_irregular_bills_per_month(client):
    with db.session_scope() as s:
        for month in ["2025-11", "2026-01", "2026-03", "2026-05", "2026-07", "2026-09"]:
            email = BillEmail(gmail_message_id=f"w{month}", thread_id="t", sender_email="w@o.ca",
                              subject="Water bill", received_at=datetime(2025, 10, 1))
            s.add(email)
            s.flush()
            s.add(Bill(email_id=email.id, biller="Ottawa Water", category="utilities", amount=Decimal("150.00"),
                       due_date=date.fromisoformat(f"{month}-10"), month=month, status="parsed"))
    fixed = client.get("/api/dashboard").json()["fixed"]
    assert (fixed["from_month"], fixed["to_month"], fixed["months"]) == ("2025-11", "2026-10", 12)
    items = {i["biller"]: i for i in fixed["items"]}
    water = items["Ottawa Water"]  # 6 bills of $150 + the Oct one ($153.78) in 12 months
    assert (water["bills"], water["cadence"]) == (7, "every 2 months")
    assert water["monthly"] == pytest.approx((6 * 150 + 153.78) / 12)
    tax = items["Ottawa Property Tax"]
    assert tax["cadence"] == "once a year" and tax["monthly"] == pytest.approx(2427.37 / 12)
    assert items["Bell Internet"]["last_amount"] == 55.50
    assert fixed["monthly"] == pytest.approx(sum(i["monthly"] for i in fixed["items"]))
    assert "Triangle Mastercard" not in items  # card statements aren't fixed costs
    assert fixed["items"][0]["biller"] == "Ottawa Property Tax"  # biggest per month first


def test_payment_method_precedence(client):
    from app.models import BillerPayment

    from sqlalchemy import select

    with db.session_scope() as s:
        s.scalars(select(Bill).where(Bill.biller == "Hydro Ottawa")).one().payment_method = "bank_auto"
        s.add(BillerPayment(biller="Bell Internet", email_method="bank_auto", email_detail="RBC ••321",
                            email_date=datetime(2026, 8, 19), email_subject="Confirmation - Change pre-authorized payments"))

    d = client.get("/api/dashboard").json()
    pay = {b["biller"]: b["payment"] for b in d["bills"]}
    assert pay["Hydro Ottawa"]["method"] == "bank_auto" and pay["Hydro Ottawa"]["source"] == "email"
    assert pay["Bell Internet"] == {"method": "bank_auto", "detail": "RBC ••321", "source": "email",
                                    "source_date": "2026-08-19",
                                    "source_subject": "Confirmation - Change pre-authorized payments"}
    assert pay["Ottawa Water"] is None
    assert d["next_bill"]["payment"]["method"] == "bank_auto"  # Hydro is next

    # your own setting wins, and clearing it goes back to the email
    r = client.put("/api/billers/Bell Internet/payment", json={"method": "card_auto", "detail": "Visa ••4242"})
    assert r.json()["payment"]["source"] == "you"
    assert {b["biller"]: b["payment"] for b in client.get("/api/dashboard").json()["bills"]}["Bell Internet"]["detail"] == "Visa ••4242"
    client.put("/api/billers/Bell Internet/payment", json={"method": None})
    assert {b["biller"]: b["payment"] for b in client.get("/api/dashboard").json()["bills"]}["Bell Internet"]["detail"] == "RBC ••321"
    assert client.put("/api/billers/Bell Internet/payment", json={"method": "cash"}).status_code == 422


def test_new_columns_are_added_to_an_existing_database(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    db.Base.metadata.create_all(engine)
    with engine.begin() as conn:  # roll the bills table back to before payment_method existed
        conn.execute(text("ALTER TABLE bills DROP COLUMN payment_method"))
        conn.execute(text("ALTER TABLE bills DROP COLUMN payment_detail"))
    db.add_missing_columns(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("bills")}
    assert {"payment_method", "payment_detail"} <= columns


def test_manual_costs_crud(client):
    listing = client.get("/api/manual-costs").json()
    assert listing["items"] == []
    assert listing["frequencies"] == ["monthly", "every 2 months", "quarterly", "twice a year", "yearly"]
    assert "utilities" in listing["categories"] and "credit_card" not in listing["categories"]

    gym = {"name": " Anytime Fitness ", "amount": "$28.25", "category": "subscriptions", "due_day": 15}
    res = client.post("/api/manual-costs", json=gym)
    assert res.status_code == 201
    created = res.json()
    assert created["name"] == "Anytime Fitness" and created["frequency"] == "monthly"
    assert (created["amount"], created["monthly"], created["due_day"], created["active"]) == (28.25, 28.25, 15, True)

    heater = {"name": "Reliance water heater", "amount": "1,000", "frequency": "every 2 months",
              "category": "utilities", "next_due_date": "2026-11-20"}
    heater_id = client.post("/api/manual-costs", json=heater).json()["id"]
    heater.update(amount="90.5", next_due_date="2026-08-20", active=False)
    updated = client.put(f"/api/manual-costs/{heater_id}", json=heater).json()
    assert (updated["amount"], updated["monthly"], updated["active"]) == (90.5, pytest.approx(45.25), False)
    assert updated["next_due_date"] == "2026-10-20"  # moved forward past today by the frequency

    names = [c["name"] for c in client.get("/api/manual-costs").json()["items"]]
    assert names == ["Anytime Fitness", "Reliance water heater"]

    for bad in [{"amount": "abc"}, {"amount": "0"}, {"amount": "NaN"}, {"name": "  "}, {"frequency": "weekly"},
                {"category": "credit_card"}, {"due_day": 32}, {"next_due_date": "next week"}]:
        res = client.post("/api/manual-costs", json={**gym, **bad})
        assert res.status_code == 422 and isinstance(res.json()["detail"], str), bad

    assert client.put("/api/manual-costs/999", json=gym).status_code == 404
    assert client.delete(f"/api/manual-costs/{created['id']}").json() == {"ok": True}
    assert client.delete(f"/api/manual-costs/{created['id']}").status_code == 404
    assert [c["id"] for c in client.get("/api/manual-costs").json()["items"]] == [heater_id]


def test_manual_costs_add_to_fixed_bills(client):
    before = client.get("/api/dashboard").json()["fixed"]
    cost = {"name": "My Ontario account", "amount": "120", "frequency": "yearly", "next_due_date": "2026-03-01"}
    cost_id = client.post("/api/manual-costs", json=cost).json()["id"]

    fixed = client.get("/api/dashboard").json()["fixed"]
    assert fixed["monthly"] == pytest.approx(before["monthly"] + 10)
    assert fixed["yearly"] == pytest.approx(before["yearly"] + 120)
    assert [i for i in fixed["items"] if i["source"] == "email"] == before["items"]  # emailed bills untouched
    item = next(i for i in fixed["items"] if i["source"] == "manual")
    assert item == {"biller": "My Ontario account", "category": "other", "monthly": pytest.approx(10),
                    "yearly": 120.0, "cadence": "once a year", "id": cost_id, "amount": 120.0,
                    "next_due_date": "2027-03-01", "source": "manual"}

    client.put(f"/api/manual-costs/{cost_id}", json={**cost, "active": False})  # paused: not counted
    assert client.get("/api/dashboard").json()["fixed"] == before


def test_manual_costs_alone_still_give_fixed_bills(client):
    with db.session_scope() as s:
        s.execute(delete(Bill))
    assert client.get("/api/dashboard").json()["fixed"] is None
    client.post("/api/manual-costs", json={"name": "Rogers Business Mobile", "amount": "65",
                                           "category": "phone_internet", "due_day": 3})
    fixed = client.get("/api/dashboard").json()["fixed"]
    assert (fixed["monthly"], fixed["from_month"], fixed["months"]) == (65.0, "2026-10", 1)
    assert fixed["items"][0]["next_due_date"] == "2026-11-03"  # the 3rd has passed this month


def test_manual_cost_next_due_date():
    def due(on, **kw):
        return manual.next_due(ManualCost(**{"frequency": "monthly", **kw}), on)

    assert due(date(2026, 10, 11), due_day=11) == date(2026, 10, 11)
    assert due(date(2026, 11, 2), due_day=31) == date(2026, 11, 30)  # short month: last day
    assert due(date(2026, 11, 2), due_day=31, frequency="quarterly") is None  # which month is unknown
    quarterly = {"frequency": "quarterly", "next_due_date": date(2026, 1, 31)}
    assert due(date(2026, 4, 1), **quarterly) == date(2026, 4, 30)
    assert due(date(2026, 5, 1), **quarterly) == date(2026, 7, 31)  # counted from Jan 31, not Apr 30
    assert due(date(2025, 6, 1), **quarterly) == date(2026, 1, 31)  # already in the future: as entered
