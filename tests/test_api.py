"""API + scheduler tests against a throwaway database (the app's lifespan/scheduler isn't started)."""

import dataclasses
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import api, db, google_auth, scheduler
from app.bills import dashboard, service
from app.config import settings
from app.main import app
from app.models import Bill, BillEmail, SyncRun


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(service, "current_month", lambda: "2026-10")
    monkeypatch.setattr(dashboard, "today", lambda: date(2026, 10, 11))
    monkeypatch.setattr(google_auth, "connected_account", lambda: "me@example.com")
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
    return TestClient(app)


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
