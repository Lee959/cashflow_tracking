"""Net worth: daily snapshots of bank accounts and hand-entered items."""

import dataclasses
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import db, networth
from app.config import settings
from app.main import app
from app.models import BalanceSnapshot, PlaidAccount, PlaidItem

DAY = date(2026, 10, 12)


@pytest.fixture
def bank(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(networth, "settings", dataclasses.replace(settings, plaid_env="production"))
    monkeypatch.setattr(networth, "today", lambda: DAY)
    with db.session_scope() as s:
        s.add(PlaidItem(item_id="td", institution_name="TD", access_token="x", environment="production"))
        s.add(PlaidItem(item_id="test", institution_name="Test", access_token="x", environment="sandbox"))
        for account_id, item, name, kind, subtype, balance in [
            ("chq", "td", "Chequing", "depository", "checking", "2500.00"),
            ("tfsa", "td", "TFSA", "investment", "tfsa", "12000.00"),
            ("visa", "td", "Visa", "credit", "credit card", "800.00"),
            ("mortgage", "td", "Mortgage", "loan", "mortgage", "350000.00"),
            ("car", "td", "Car loan", "loan", "auto", "9000.00"),
            ("pending", "td", "New account", "depository", "savings", None),  # no balance yet: skipped
            ("fake", "test", "Sandbox chequing", "depository", "checking", "99999.00"),  # other environment
        ]:
            s.add(PlaidAccount(account_id=account_id, item_id=item, name=name, type=kind, subtype=subtype,
                               current_balance=Decimal(balance) if balance else None))


def set_balance(account_id, value):
    with db.session_scope() as s:
        s.get(PlaidAccount, account_id).current_balance = Decimal(value)


def test_snapshot_puts_each_account_on_the_right_side(bank):
    assert networth.take_snapshot() == 5
    summary = networth.summary()
    assert summary["assets"] == 14500 and summary["debts"] == 359800
    assert summary["net"] == pytest.approx(14500 - 359800)
    groups = {g["group"]: (g["is_debt"], g["total"]) for g in summary["groups"]}
    assert groups == {"cash": (False, 2500), "investments": (False, 12000), "credit_cards": (True, 800),
                      "mortgage": (True, 350000), "loans": (True, 9000)}
    assert [g["group"] for g in summary["groups"]] == ["investments", "cash", "mortgage", "loans", "credit_cards"]
    assert summary["is_test_data"] is False and summary["change_30d"] is None


def test_manual_items_count_and_update_today(bank):
    house = networth.save_asset(None, name="House", kind="asset", category="property", value=Decimal("650000"))
    networth.take_snapshot()
    assert networth.summary()["net"] == pytest.approx(650000 + 14500 - 359800)

    networth.save_asset(house.id, name="House", kind="asset", category="property", value=Decimal("640000"))
    assert networth.summary()["net"] == pytest.approx(640000 + 14500 - 359800)
    networth.delete_asset(house.id)
    assert networth.summary()["net"] == pytest.approx(14500 - 359800)


def test_history_has_one_point_per_day_and_a_30_day_change(bank, monkeypatch):
    networth.take_snapshot(DAY - timedelta(days=40))
    set_balance("chq", "3000.00")
    networth.take_snapshot(DAY - timedelta(days=30))
    set_balance("chq", "4000.00")
    networth.take_snapshot(DAY)
    networth.take_snapshot(DAY)  # a second sync the same day replaces, not duplicates

    summary = networth.summary()
    assert [p["date"] for p in summary["history"]] == ["2026-09-02", "2026-09-12", "2026-10-12"]
    assert summary["change_30d"] == pytest.approx(1000) and summary["since"] == "2026-09-12"
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(BalanceSnapshot).where(BalanceSnapshot.snapshot_date == DAY)) == 5


def test_a_removed_account_leaves_today_but_not_the_past(bank):
    networth.take_snapshot(DAY - timedelta(days=1))
    with db.session_scope() as s:
        s.delete(s.get(PlaidAccount, "visa"))
    networth.take_snapshot(DAY)
    history = networth.summary()["history"]
    assert history[0]["net"] == pytest.approx(14500 - 359800)  # yesterday still had the card
    assert history[1]["net"] == pytest.approx(14500 - 359000)


def test_sandbox_balances_are_labelled_as_test_data(bank, monkeypatch):
    monkeypatch.setattr(networth, "settings", dataclasses.replace(settings, plaid_env="sandbox"))
    networth.take_snapshot()
    summary = networth.summary()
    assert summary["net"] == 99999 and summary["is_test_data"] is True


def test_nothing_recorded_yet_means_no_net_worth(bank):
    assert networth.summary() is None


def test_assets_api(bank):
    client = TestClient(app, base_url="http://localhost")
    assert client.get("/api/networth").json() is None
    listing = client.get("/api/assets").json()
    assert listing["items"] == [] and "property" in listing["categories"]["asset"]

    house = client.post("/api/assets", json={"name": "House", "kind": "asset", "category": "property", "value": "$650,000"})
    assert house.status_code == 201
    assert client.get("/api/networth").json()["net"] == pytest.approx(650000 + 14500 - 359800)

    for bad in [{"name": " "}, {"kind": "maybe"}, {"category": "mortgage"}, {"value": "a lot"}, {"value": "-5"}]:
        r = client.post("/api/assets", json={"name": "Car", "kind": "asset", "category": "vehicle", "value": "20000", **bad})
        assert r.status_code == 422, bad

    assert client.put("/api/assets/999", json={"name": "X", "kind": "debt", "category": "loans", "value": "1"}).status_code == 404
    assert client.delete(f"/api/assets/{house.json()['id']}").json() == {"ok": True}
    assert client.get("/api/assets").json()["items"] == []
