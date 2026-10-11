"""JSON API used by the React frontend (frontend/)."""

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from app import google_auth, scheduler
from app.bills import dashboard, discover, service
from app.bills.senders import (
    EXAMPLE_SENDERS,
    Biller,
    BillerConfigError,
    add_billers,
    configured_senders,
    load_billers,
)
from app.config import settings
from app.gmail_client import friendly_error
from app.models import SyncRun

router = APIRouter(prefix="/api")


@router.get("/dashboard")
def get_dashboard(month: str | None = None):
    summary = dashboard.month_summary(month)
    summary["sync"] = _sync_status()
    return summary


class BillUpdate(BaseModel):
    amount: str | None = None
    due_date: str | None = None
    ignored: bool = False


@router.patch("/bills/{bill_id}")
def update_bill(bill_id: int, body: BillUpdate):
    try:
        amount = Decimal(body.amount.replace("$", "").replace(",", "").strip()) if body.amount else None
        due = date.fromisoformat(body.due_date) if body.due_date else None
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"Couldn't read amount {body.amount!r} or date {body.due_date!r}")
    try:
        service.update_bill(bill_id, amount, due, ignored=body.ignored)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@router.get("/settings")
def get_settings():
    try:
        billers = load_billers(settings.bill_senders_file)
        config_error = None
    except BillerConfigError as exc:
        billers, config_error = [], str(exc)
    return {
        "gmail_account": google_auth.connected_account(),
        "sync": {
            **_sync_status(),
            "interval_hours": scheduler.INTERVAL.total_seconds() / 3600,
            "next_run_at": _iso(scheduler.next_run_at()),
            "runs": [_run_json(r) for r in scheduler.recent_runs(10)],
        },
        "billers": [
            {"name": b.name, "senders": list(b.senders), "category": b.category,
             "subject_includes": list(b.subject_includes)}
            for b in billers
        ],
        "config_error": config_error,
    }


class SyncRequest(BaseModel):
    months_back: int | None = None  # None = this month and next, like the automatic fetch
    force: bool = False


@router.post("/sync")
def sync_now(body: SyncRequest):
    months = None
    if body.months_back:
        current = service.current_month()
        months = [service.shift_month(current, -i) for i in reversed(range(min(body.months_back, 36)))]
    try:
        run = scheduler.run_sync("manual", months, force=body.force)
    except scheduler.SyncInProgress as exc:
        raise HTTPException(409, str(exc))
    return _run_json(run)


@router.post("/reparse")
def reparse():
    try:
        return {"summary": service.reparse_all().summary()}
    except BillerConfigError as exc:
        raise HTTPException(400, str(exc))


@router.post("/gmail/disconnect")
def gmail_disconnect():
    google_auth.disconnect()
    return {"ok": True}


@router.get("/senders/scan")
def scan_senders(days: int = discover.DEFAULT_DAYS, refresh: bool = False):
    try:
        result = discover.discover_senders(days, refresh=refresh)
    except google_auth.GmailNotConnected as exc:
        raise HTTPException(400, str(exc))
    except HttpError as exc:
        raise HTTPException(503, friendly_error(exc))
    configured = configured_senders(settings.bill_senders_file) - EXAMPLE_SENDERS
    return {
        "days": days,
        "categories": discover.CATEGORIES,
        "groups": {
            name: [_candidate_json(c, configured) for c in group]
            for name, group in [
                ("bills", result.bills),
                ("periodic_bills", result.periodic_bills),
                ("card_statements", result.card_statements),
                ("not_bills", result.not_bills),
            ]
        },
        "skipped": result.skipped,
    }


class NewSender(BaseModel):
    email: str
    name: str
    category: str = "other"
    subject_filter: str = ""
    whole_domain: bool = False


class AddSenders(BaseModel):
    senders: list[NewSender]


@router.post("/senders")
def add_senders(body: AddSenders):
    if not body.senders:
        raise HTTPException(422, "Tick at least one sender first.")
    new = []
    for s in body.senders:
        email = s.email.strip().lower()
        sender = "@" + email.rsplit("@", 1)[-1] if s.whole_domain else email
        word = s.subject_filter.strip().lower()
        new.append(Biller(name=s.name.strip() or email, senders=(sender,), category=s.category,
                          subject_includes=(word,) if word else ()))
    add_billers(settings.bill_senders_file, new, header_from=settings.bill_senders_file.parent / "bill_senders.example.toml")
    return {"added": len(new)}


def _sync_status() -> dict:
    last = scheduler.last_run()
    return {
        "gmail_connected": google_auth.connected_account() is not None,
        "running": scheduler.is_running(),
        "last_run": _run_json(last) if last else None,
    }


def _run_json(run: SyncRun) -> dict:
    return {
        "trigger": run.trigger,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "ok": run.ok,
        "summary": run.summary,
    }


def _candidate_json(c: discover.SenderCandidate, configured: set[str]) -> dict:
    return {
        "email": c.email,
        "domain": c.domain,
        "name": c.name,
        "category": c.category,
        "kind": c.kind,
        "kind_label": c.kind_label,
        "cadence": c.cadence,
        "cadence_label": c.cadence_label,
        "count": c.count,
        "content": c.content,
        "sample_amount": float(c.sample_amount) if c.sample_amount is not None else None,
        "latest_subject": c.latest_subject,
        "latest_at": c.latest_at.isoformat(),
        "duplicate_of": c.duplicate_of,
        "subject_filter": c.subject_filter,
        "recommended": c.recommended,
        "already": c.email in configured or f"@{c.domain}" in configured,
    }


def _iso(value) -> str | None:
    return value.isoformat() if value else None
