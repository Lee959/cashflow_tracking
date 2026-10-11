"""End-to-end sync against a fake Gmail inbox and a throwaway database."""

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db
from app.bills import service
from app.config import settings
from app.gmail_client import FetchedAttachment, FetchedMessage

SENDERS_TOML = """
[[biller]]
name = "Hydro"
senders = ["ebills@hydro.example"]
category = "utilities"
subject_excludes = ["tips"]

[[biller]]
name = "Insurer"
senders = ["@insure.example"]
category = "insurance"
"""

INBOX = {
    "m1": FetchedMessage(
        id="m1", thread_id="t1", sender_email="ebills@hydro.example", sender_name="Hydro",
        subject="Your October bill", received_at=datetime(2026, 10, 3, 14, tzinfo=timezone.utc),
        snippet="", text="Amount due: $120.50 Due date: October 24, 2026",
        attachments=[FetchedAttachment("bill.pdf", "application/pdf", b"not really a pdf")],
    ),
    "m2": FetchedMessage(
        id="m2", thread_id="t2", sender_email="no-reply@insure.example", sender_name="Insurer",
        subject="Your policy documents", received_at=datetime(2026, 10, 9, 14, tzinfo=timezone.utc),
        snippet="", text="Your documents are ready. Sign in to view.",
    ),
    "m3": FetchedMessage(
        id="m3", thread_id="t3", sender_email="ebills@hydro.example", sender_name="Hydro",
        subject="Energy saving tips", received_at=datetime(2026, 10, 12, 14, tzinfo=timezone.utc),
        snippet="", text="Save $25.00 this winter",
    ),
    "m4": FetchedMessage(
        id="m4", thread_id="t4", sender_email="no-reply@insure.example", sender_name="Insurer",
        subject="Your eStatement is ready", received_at=datetime(2026, 10, 15, 14, tzinfo=timezone.utc),
        snippet="", text="Sign in to view your statement.",
    ),
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    senders = tmp_path / "bill_senders.toml"
    senders.write_text(SENDERS_TOML)
    test_settings = dataclasses.replace(settings, data_dir=tmp_path, bill_senders_file=senders)
    monkeypatch.setattr(service, "settings", test_settings)

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))

    queries = []
    monkeypatch.setattr(service.google_auth, "get_credentials", lambda: object())
    monkeypatch.setattr(service.gmail_client, "build_service", lambda creds: object())
    monkeypatch.setattr(service.gmail_client, "search_message_ids",
                        lambda svc, q: queries.append(q) or list(INBOX))
    monkeypatch.setattr(service.gmail_client, "fetch_message", lambda svc, mid: INBOX[mid])
    return tmp_path, queries


def test_sync_parse_edit_and_reparse(env):
    tmp_path, queries = env

    result = service.sync_month("2026-10")
    assert (result.found, result.new, result.parsed, result.statements, result.needs_review, result.ignored) == (
        4, 4, 1, 1, 1, 1)
    assert "from:(ebills@hydro.example OR insure.example)" in queries[0]

    bills = {b.email.gmail_message_id: b for b in service.bills_for_month("2026-10")}
    hydro = bills["m1"]
    assert (hydro.amount, hydro.due_date, hydro.status) == (Decimal("120.50"), date(2026, 10, 24), "parsed")
    assert hydro.email.attachments[0].filename == "bill.pdf"
    assert (tmp_path / hydro.email.attachments[0].path).read_bytes() == b"not really a pdf"
    assert bills["m2"].status == "needs_review"
    assert bills["m3"].status == "ignored"
    assert bills["m4"].status == "statement"

    # Second sync skips what's stored
    assert service.sync_month("2026-10").skipped == 4

    # A hand edit survives both re-parse and a forced re-download
    service.update_bill(bills["m2"].id, Decimal("89.99"), date(2026, 10, 30))
    service.reparse_all()
    assert service.sync_month("2026-10", force=True).skipped == 1
    bills = {b.email.gmail_message_id: b for b in service.bills_for_month("2026-10")}
    assert (bills["m2"].amount, bills["m2"].status) == (Decimal("89.99"), "manual")

    totals = dict(service.monthly_totals("2026-10", months=3))
    assert totals == {"2026-08": 0, "2026-09": 0, "2026-10": Decimal("210.49")}


def test_month_helpers():
    assert service.shift_month("2026-01", -1) == "2025-12"
    assert service.shift_month("2026-12", 1) == "2027-01"
    start, end = service.month_bounds("2026-10")
    assert (start.month, end.month, end.year) == (10, 11, 2026)


def test_bills_count_in_their_due_month_and_sync_reads_previous_month(env):
    tmp_path, queries = env
    late = FetchedMessage(
        id="m9", thread_id="t9", sender_email="ebills@hydro.example", sender_name="Hydro",
        subject="Your bill", received_at=datetime(2026, 9, 27, 14, tzinfo=timezone.utc),
        snippet="", text="Amount Due : $121.83 Automated Payment Withdrawal Date : 2026-10-19",
    )
    INBOX["m9"] = late
    try:
        service.sync_month("2026-10")
    finally:
        del INBOX["m9"]
    start, _ = service.month_bounds("2026-09")
    assert f"after:{int(start.timestamp())}" in queries[0]
    bills = {b.email.gmail_message_id: b for b in service.bills_for_month("2026-10")}
    assert bills["m9"].amount == Decimal("121.83")  # received in September, due in October
