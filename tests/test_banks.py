"""Bank connections through Plaid, with Plaid itself replaced by fakes (no network)."""

import dataclasses
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import banks, db, plaid_client
from app.config import settings
from app.main import app
from app.models import PlaidItem, PlaidTransaction

ACCOUNTS = [
    {"account_id": "chq", "name": "Chequing", "mask": "0000", "type": "depository", "subtype": "checking",
     "balances": {"current": 110.0, "available": 100.0, "iso_currency_code": "CAD", "limit": None}},
    {"account_id": "visa", "name": "Visa", "mask": "3333", "type": "credit", "subtype": "credit card",
     "balances": {"current": 410.0, "available": None, "iso_currency_code": "CAD", "limit": 2000.0}},
]


def txn(tid, amount, day=9, account="chq"):
    return {"transaction_id": tid, "account_id": account, "date": date(2026, 10, day), "name": f"Shop {tid}",
            "merchant_name": None, "amount": amount, "iso_currency_code": "CAD", "pending": False,
            "payment_channel": "in store", "personal_finance_category": {"primary": "GENERAL_MERCHANDISE",
                                                                        "detailed": "GENERAL_MERCHANDISE_OTHER"}}


@pytest.fixture
def fake_plaid(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(banks, "settings", dataclasses.replace(settings, plaid_env="sandbox"))

    state = {"pages": [([txn("t1", 25.0), txn("t2", -1000.0)], [], [], "cursor-1")], "cursors": [], "removed": []}
    monkeypatch.setattr(plaid_client, "exchange_public_token", lambda public_token: ("access-sandbox-secret", "item-1"))
    monkeypatch.setattr(plaid_client, "get_accounts", lambda access_token: ACCOUNTS)

    def sync(access_token, cursor):
        assert access_token.startswith("access-")  # decrypted again before use
        state["cursors"].append(cursor)
        return state["pages"].pop(0) if state["pages"] else ([], [], [], cursor)

    monkeypatch.setattr(plaid_client, "sync_transactions", sync)
    monkeypatch.setattr(plaid_client, "remove_item", lambda access_token: state["removed"].append(access_token))
    monkeypatch.setattr(plaid_client, "get_item_products", lambda access_token: ["liabilities", "transactions"])
    monkeypatch.setattr(plaid_client, "get_credit_card_liabilities", lambda access_token: [
        {"account_id": "visa", "last_statement_balance": 385.12, "last_statement_issue_date": date(2026, 10, 2),
         "minimum_payment_amount": 10.0, "next_payment_due_date": date(2026, 10, 27), "is_overdue": False}])
    return state


def test_connect_stores_encrypted_token_accounts_and_transactions(fake_plaid):
    result = banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")
    assert result == {"item_id": "item-1", "accounts": 2, "added": 2, "modified": 0, "removed": 0}

    with db.session_scope() as s:
        item = s.scalar(select(PlaidItem))
        assert item.access_token != "access-sandbox-secret" and item.environment == "sandbox"
        assert item.transactions_cursor == "cursor-1"
        assert sorted(t.amount for t in s.scalars(select(PlaidTransaction))) == [-1000, 25]

    [listed] = banks.list_items()
    assert (listed["institution_name"], listed["transaction_count"]) == ("Tartan Bank", 2)
    assert [(a["name"], a["current_balance"], a["credit_limit"]) for a in listed["accounts"]] == [
        ("Visa", 410.0, 2000.0), ("Chequing", 110.0, None)]


def test_sync_continues_from_cursor_and_applies_changes(fake_plaid):
    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")
    fake_plaid["pages"].append(([txn("t3", 12.5)], [txn("t1", 30.0)], ["t2"], "cursor-2"))
    assert banks.sync_item("item-1") == {"item_id": "item-1", "accounts": 2, "added": 1, "modified": 1, "removed": 1}
    assert fake_plaid["cursors"] == [None, "cursor-1"]
    with db.session_scope() as s:
        amounts = {t.transaction_id: t.amount for t in s.scalars(select(PlaidTransaction))}
    assert amounts == {"t1": 30, "t3": 12.5}


def test_other_environment_connections_are_hidden(fake_plaid, monkeypatch):
    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")
    monkeypatch.setattr(banks, "settings", dataclasses.replace(settings, plaid_env="production"))
    assert banks.list_items() == []  # sandbox test data never shows up next to real accounts


def test_remove_revokes_at_plaid_and_deletes_local_data(fake_plaid, monkeypatch):
    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")
    banks.remove("item-1")
    assert fake_plaid["removed"] == ["access-sandbox-secret"]
    assert banks.list_items() == []
    with db.session_scope() as s:
        assert s.scalar(select(PlaidTransaction)) is None

    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")

    def already_gone(access_token):
        raise plaid_client.PlaidError("gone", "ITEM_NOT_FOUND")

    monkeypatch.setattr(plaid_client, "remove_item", already_gone)
    banks.remove("item-1")  # Plaid already forgot it: still cleaned up here
    assert banks.list_items() == []


def test_plaid_api_endpoints(fake_plaid, monkeypatch):
    client = TestClient(app, base_url="http://localhost")
    monkeypatch.setattr(plaid_client, "create_link_token", lambda: "link-sandbox-abc")
    assert client.post("/api/plaid/link-token").json() == {"link_token": "link-sandbox-abc"}

    r = client.post("/api/plaid/connect", json={"public_token": "public-sandbox-x", "institution_name": "Tartan Bank"})
    assert r.status_code == 200 and r.json()["added"] == 2
    assert [i["item_id"] for i in client.get("/api/plaid").json()["items"]] == ["item-1"]
    assert client.post("/api/plaid/items/nope/sync").status_code == 404

    def failing(access_token, cursor):
        raise plaid_client.PlaidError("the login details of this item have changed", "ITEM_LOGIN_REQUIRED")

    monkeypatch.setattr(plaid_client, "sync_transactions", failing)
    r = client.post("/api/plaid/items/item-1/sync")
    assert r.status_code == 502 and "login details" in r.json()["detail"]
    assert "login details" in client.get("/api/plaid").json()["items"][0]["last_error"]

    assert client.delete("/api/plaid/items/item-1").json() == {"ok": True}
    assert client.get("/api/plaid").json()["items"] == []


def test_link_is_allowed_by_the_content_security_policy(fake_plaid):
    client = TestClient(app, base_url="http://localhost")
    csp = client.get("/api/plaid").headers["content-security-policy"]
    assert "script-src 'self' https://cdn.plaid.com/link/v2/stable/link-initialize.js" in csp
    assert "frame-src https://cdn.plaid.com/" in csp


def test_card_statements_and_products_are_stored(fake_plaid):
    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")
    [item] = banks.list_items()
    assert item["products"] == ["liabilities", "transactions"] and item["needs_reconnect"] is False
    visa = next(a for a in item["accounts"] if a["name"] == "Visa")
    assert (visa["statement_balance"], visa["minimum_payment"], visa["payment_due_date"], visa["is_overdue"]) == (
        385.12, 10.0, "2026-10-27", False)
    # a later sync without statement data keeps the statement details
    fake_plaid["pages"].append(([], [], [], "cursor-2"))
    banks.sync_item("item-1")
    assert next(a for a in banks.list_items()[0]["accounts"] if a["name"] == "Visa")["statement_balance"] == 385.12


def test_same_login_twice_is_refused_and_removed_at_plaid(fake_plaid, monkeypatch):
    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")
    monkeypatch.setattr(plaid_client, "exchange_public_token", lambda public_token: ("access-second", "item-2"))
    with pytest.raises(banks.DuplicateConnection, match="already connected"):
        banks.connect("public-sandbox-y", "Tartan Bank", "ins_43")
    assert fake_plaid["removed"] == ["access-second"]  # not kept, not billed
    assert [i["item_id"] for i in banks.list_items()] == ["item-1"]


def test_another_family_members_login_at_the_same_bank_is_fine(fake_plaid, monkeypatch):
    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")
    monkeypatch.setattr(plaid_client, "exchange_public_token", lambda public_token: ("access-second", "item-2"))
    monkeypatch.setattr(plaid_client, "get_accounts", lambda access_token: [
        {**ACCOUNTS[0], "account_id": "chq-2", "mask": "9876"}])
    banks.connect("public-sandbox-y", "Tartan Bank", "ins_43")
    assert [i["item_id"] for i in banks.list_items()] == ["item-1", "item-2"]


def test_login_required_offers_reconnect_in_update_mode(fake_plaid, monkeypatch):
    banks.connect("public-sandbox-x", "Tartan Bank", "ins_43")

    def login_required(access_token, cursor):
        raise plaid_client.PlaidError("the login details of this item have changed", "ITEM_LOGIN_REQUIRED")

    monkeypatch.setattr(plaid_client, "sync_transactions", login_required)
    with pytest.raises(plaid_client.PlaidError):
        banks.sync_item("item-1")
    assert banks.list_items()[0]["needs_reconnect"] is True

    seen = []
    monkeypatch.setattr(plaid_client, "create_update_link_token", lambda access_token: seen.append(access_token) or "link-update")
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/api/plaid/items/item-1/link-token").json() == {"link_token": "link-update"}
    assert seen == ["access-sandbox-secret"]


def test_no_api_response_contains_an_access_token(fake_plaid, monkeypatch):
    client = TestClient(app, base_url="http://localhost")
    client.post("/api/plaid/connect", json={"public_token": "public-sandbox-x", "institution_name": "Tartan Bank"})
    for path in ["/api/plaid", "/api/dashboard", "/api/settings"]:
        assert "access-sandbox-secret" not in client.get(path).text, path


def test_link_token_requests_read_only_products_and_two_years(monkeypatch):
    sent = []
    monkeypatch.setattr(plaid_client, "_call", lambda fn: sent.append(fn) or type("R", (), {"link_token": "t"})())
    plaid_client.create_link_token()

    class Capture:
        def link_token_create(self, request):
            self.request = request

    capture = Capture()
    sent[0](capture)
    request = capture.request
    assert [p.value for p in request.products] == ["transactions"]
    assert [p.value for p in request.optional_products] == ["liabilities", "investments"]
    assert request.transactions.days_requested == 730
    # nothing that can move money
    requested = {p.value for p in request.products} | {p.value for p in request.optional_products}
    assert not requested & {"auth", "transfer", "payment_initiation", "signal"}
