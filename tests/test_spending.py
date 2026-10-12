"""Spending from bank transactions, and matching emailed bills to the payments that settled them."""

import dataclasses
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import banks, db, google_auth, scheduler, spending
from app.bills import dashboard, service
from app.config import settings
from app.main import app
from app.models import Bill, BillEmail, PlaidAccount, PlaidItem, PlaidTransaction

ON = date(2026, 10, 20)


@pytest.fixture
def bank(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    use_env(monkeypatch, "production")
    monkeypatch.setattr(service, "current_month", lambda: "2026-10")
    monkeypatch.setattr(dashboard, "today", lambda: ON)

    with db.session_scope() as s:
        s.add(PlaidItem(item_id="rbc", institution_name="RBC", access_token="x", environment="production",
                        last_synced_at=datetime(2026, 10, 20, 9)))
        s.add(PlaidItem(item_id="old-sandbox", institution_name="Test", access_token="x", environment="sandbox"))
        for account_id, item, name, mask, kind in [
            ("chq", "rbc", "Chequing", "321", "depository"),
            ("visa", "rbc", "Visa", "4242", "credit"),
            ("mortgage", "rbc", "Mortgage", "8888", "loan"),
            ("tfsa", "rbc", "TFSA", "5555", "investment"),
            ("sb", "old-sandbox", "Sandbox chequing", "0000", "depository"),
        ]:
            s.add(PlaidAccount(account_id=account_id, item_id=item, name=name, mask=mask, type=kind))
    return engine


def use_env(monkeypatch, env):
    for module in (spending, banks):
        monkeypatch.setattr(module, "settings", dataclasses.replace(settings, plaid_env=env))


def add_txn(tid, account, day, amount, category, detailed=None, name=None, month=10):
    with db.session_scope() as s:
        s.add(PlaidTransaction(transaction_id=tid, account_id=account, item_id="rbc", transaction_date=date(2026, month, day),
                               name=name or tid, amount=Decimal(str(amount)), category=category,
                               category_detailed=detailed or f"{category}_OTHER"))


def add_bill(biller, amount, due):
    with db.session_scope() as s:
        email = BillEmail(gmail_message_id=f"{biller}{due}", thread_id="t", sender_email="x@y.z", subject="bill",
                          received_at=datetime(2026, 9, 25))
        s.add(email)
        s.flush()
        bill = Bill(email_id=email.id, biller=biller, category="utilities", amount=Decimal(amount),
                    due_date=date.fromisoformat(due), month=due[:7], status="parsed")
        s.add(bill)
        s.flush()
        return bill.id


def test_month_spending_counts_purchases_once(bank):
    add_txn("groceries", "visa", 3, 84.20, "FOOD_AND_DRINK")
    add_txn("refund", "visa", 4, -20.00, "GENERAL_MERCHANDISE")      # refunds are shown separately
    add_txn("hydro", "chq", 19, 121.83, "RENT_AND_UTILITIES")
    add_txn("mortgage-pmt", "chq", 1, 1500.00, "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")  # real money out
    add_txn("card-payoff", "chq", 15, 410.00, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")  # card purchases already count
    add_txn("to-savings", "chq", 5, 300.00, "TRANSFER_OUT")
    add_txn("salary", "chq", 15, -3200.00, "INCOME")
    add_txn("on-loan", "mortgage", 1, 999.00, "LOAN_PAYMENTS")       # loan/investment accounts aren't spending
    add_txn("on-tfsa", "tfsa", 2, 500.00, "TRANSFER_IN")
    add_txn("last-month", "visa", 30, 50.00, "FOOD_AND_DRINK", month=9)
    add_txn("sandbox", "sb", 6, 77.00, "FOOD_AND_DRINK")              # other environment

    s = spending.month_spending("2026-10", ON)
    assert s["total"] == pytest.approx(84.20 + 121.83 + 1500)
    assert (s["refunds"], s["income"]) == (pytest.approx(20), pytest.approx(3200))
    assert s["count"] == 3 and s["accounts"] == 2 and s["is_test_data"] is False
    # categories are purchases only, so a category with just a refund doesn't appear
    assert [c["category"] for c in s["by_category"]] == ["LOAN_PAYMENTS", "RENT_AND_UTILITIES", "FOOD_AND_DRINK"]
    assert [t["name"] for t in s["recent"]] == ["hydro", "refund", "groceries", "mortgage-pmt"]
    assert s["recent"][0]["account"] == "Chequing ••321"


def test_no_bank_connected_means_no_bank_section(bank, monkeypatch):
    use_env(monkeypatch, "development")
    assert spending.month_spending("2026-10", ON) is None
    assert spending.match_bill_payments([]) == {}


def test_sandbox_data_is_labelled_as_test_data(bank, monkeypatch):
    use_env(monkeypatch, "sandbox")
    add_txn("sandbox-coffee", "sb", 6, 4.50, "FOOD_AND_DRINK")
    with db.session_scope() as s:
        s.get(PlaidTransaction, "sandbox-coffee").item_id = "old-sandbox"
    result = spending.month_spending("2026-10", ON)
    assert result["is_test_data"] is True and result["total"] == pytest.approx(4.50)


def test_bills_are_matched_to_the_payment_that_settled_them(bank):
    hydro = add_bill("Hydro Ottawa", "121.83", "2026-10-19")
    water = add_bill("Ottawa Water", "153.78", "2026-10-08")
    bell = add_bill("Bell Internet", "55.50", "2026-10-09")
    tax = add_bill("Ottawa Property Tax", "2427.37", "2026-06-18")
    add_txn("stranger", "chq", 18, 121.83, "GENERAL_MERCHANDISE", name="AMAZON")        # same amount, closer date
    add_txn("hydro-pmt", "chq", 21, 121.83, "RENT_AND_UTILITIES", name="HYDRO OTTAWA PAD")  # names the biller
    add_txn("water-pmt", "chq", 9, 153.78, "RENT_AND_UTILITIES", name="CITY OF OTTAWA")
    add_txn("bell-other", "visa", 9, 55.00, "GENERAL_SERVICES", name="BELL")               # wrong amount
    add_txn("too-late", "chq", 30, 2427.37, "GOVERNMENT_AND_NON_PROFIT", month=6)          # 12 days after due

    with db.session_scope() as s:
        bills = list(s.scalars(select(Bill)))
    matches = spending.match_bill_payments(bills)
    assert matches[hydro]["date"] == "2026-10-21" and matches[hydro]["name"] == "HYDRO OTTAWA PAD"
    assert matches[water] == {"date": "2026-10-09", "account": "Chequing ••321", "name": "CITY OF OTTAWA",
                              "pending": False}
    assert bell not in matches and tax not in matches


def test_dashboard_includes_bank_spending_and_paid_bills(bank):
    hydro = add_bill("Hydro Ottawa", "121.83", "2026-10-19")
    add_txn("hydro-pmt", "chq", 19, 121.83, "RENT_AND_UTILITIES", name="HYDRO OTTAWA")
    d = TestClient(app, base_url="http://localhost").get("/api/dashboard").json()
    assert d["bank"]["total"] == pytest.approx(121.83)
    [bill] = d["bills"]
    assert bill["id"] == hydro and bill["paid"]["account"] == "Chequing ••321"


def test_automatic_fetch_syncs_banks_even_without_gmail(bank, monkeypatch):
    monkeypatch.setattr(google_auth, "connected_account", lambda: None)
    monkeypatch.setattr(service, "sync_month", lambda m, force=False: pytest.fail("Gmail isn't connected"))
    monkeypatch.setattr(banks, "sync_all", lambda: [{"item_id": "rbc", "added": 3}])
    run = scheduler.run_sync("auto")
    assert run.ok and run.summary == "Banks: 3 new transactions"

    monkeypatch.setattr(google_auth, "connected_account", lambda: "me@example.com")
    monkeypatch.setattr(service, "sync_month", lambda m, force=False: service.SyncResult(m))
    monkeypatch.setattr(service, "refresh_payment_methods", lambda: 0)
    monkeypatch.setattr(banks, "sync_all", lambda: [{"item_id": "rbc", "error": "RBC: login required"}])
    run = scheduler.run_sync("auto")
    assert not run.ok and "No new bills" in run.summary and "RBC: login required" in run.summary
