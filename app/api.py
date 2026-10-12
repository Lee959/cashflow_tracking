"""JSON API used by the React frontend (frontend/)."""

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from app import banks, google_auth, networth, plaid_client, scheduler
from app.bills import dashboard, discover, manual, service
from app.bills.parser import PAYMENT_METHODS
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
from app.models import ManualCost, SyncRun

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
        amount = _amount(body.amount)
        due = date.fromisoformat(body.due_date) if body.due_date else None
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"Couldn't read amount {body.amount!r} or date {body.due_date!r}")
    try:
        service.update_bill(bill_id, amount, due, ignored=body.ignored)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


class PaymentSetting(BaseModel):
    method: str | None = None  # None = use what the emails say
    detail: str | None = None


@router.put("/billers/{biller}/payment")
def set_payment(biller: str, body: PaymentSetting):
    if body.method is not None and body.method not in PAYMENT_METHODS:
        raise HTTPException(422, f"method must be one of {', '.join(PAYMENT_METHODS)}")
    service.set_payment_method(biller, body.method, body.detail)
    return {"ok": True, "payment": dashboard.payment_methods().get(biller)}


class ManualCostBody(BaseModel):
    name: str
    amount: str
    frequency: str = "monthly"
    category: str = "other"
    due_day: int | None = None
    next_due_date: str | None = None
    active: bool = True


@router.get("/manual-costs")
def list_manual_costs():
    on = dashboard.today()
    return {
        "items": [_manual_cost_json(c, on) for c in manual.list_costs()],
        "frequencies": list(manual.PER_YEAR),
        "categories": manual.CATEGORIES,
    }


@router.post("/manual-costs", status_code=201)
def add_manual_cost(body: ManualCostBody):
    cost = manual.save_cost(None, **_manual_cost_fields(body))
    return _manual_cost_json(cost, dashboard.today())


@router.put("/manual-costs/{cost_id}")
def update_manual_cost(cost_id: int, body: ManualCostBody):
    try:
        cost = manual.save_cost(cost_id, **_manual_cost_fields(body))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return _manual_cost_json(cost, dashboard.today())


@router.delete("/manual-costs/{cost_id}")
def delete_manual_cost(cost_id: int):
    try:
        manual.delete_cost(cost_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@router.get("/networth")
def get_networth():
    return networth.summary()


class AssetBody(BaseModel):
    name: str
    kind: str  # asset / debt
    category: str
    value: str


@router.get("/assets")
def list_assets():
    return {
        "items": [{"id": a.id, "name": a.name, "kind": a.kind, "category": a.category, "value": float(a.value),
                   "updated_at": _iso(a.updated_at)} for a in networth.list_assets()],
        "categories": networth.MANUAL_CATEGORIES,
        "labels": networth.GROUP_LABELS,
    }


@router.post("/assets", status_code=201)
def add_asset(body: AssetBody):
    a = networth.save_asset(None, **_asset_fields(body))
    return {"id": a.id}


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, body: AssetBody):
    try:
        networth.save_asset(asset_id, **_asset_fields(body))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int):
    try:
        networth.delete_asset(asset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


def _asset_fields(body: AssetBody) -> dict:
    if not body.name.strip():
        raise HTTPException(422, "Give it a name.")
    if body.kind not in networth.MANUAL_CATEGORIES:
        raise HTTPException(422, "Choose whether it's something you own or owe.")
    if body.category not in networth.MANUAL_CATEGORIES[body.kind]:
        raise HTTPException(422, f"Unknown category {body.category!r}")
    try:
        value = _amount(body.value)
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite() or not 0 <= value < 10**10:
        raise HTTPException(422, "Enter the value, like 650000.")
    return {"name": body.name.strip(), "kind": body.kind, "category": body.category,
            "value": value.quantize(Decimal("0.01"))}


@router.get("/plaid")
def plaid_status():
    """Connected banks (current PLAID_ENV only) and whether Plaid is set up at all."""
    try:
        plaid_client.get_client()
        problem = None
    except plaid_client.PlaidError as exc:
        problem = str(exc)
    return {"environment": settings.plaid_env, "problem": problem, "items": banks.list_items()}


@router.post("/plaid/link-token")
def plaid_link_token():
    try:
        return {"link_token": plaid_client.create_link_token()}
    except plaid_client.PlaidError as exc:
        raise HTTPException(502, str(exc))


class PlaidConnect(BaseModel):
    public_token: str
    institution_name: str | None = None
    institution_id: str | None = None


@router.post("/plaid/connect")
def plaid_connect(body: PlaidConnect):
    try:
        return banks.connect(body.public_token, body.institution_name, body.institution_id)
    except banks.DuplicateConnection as exc:
        raise HTTPException(409, str(exc))
    except plaid_client.PlaidError as exc:
        raise HTTPException(502, str(exc))


@router.post("/plaid/items/{item_id}/link-token")
def plaid_reconnect_token(item_id: str):
    """Link token in update mode, for signing in to the bank again."""
    try:
        return {"link_token": banks.update_link_token(item_id)}
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except plaid_client.PlaidError as exc:
        raise HTTPException(502, str(exc))


@router.post("/plaid/items/{item_id}/sync")
def plaid_sync(item_id: str):
    try:
        return banks.sync_item(item_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except plaid_client.PlaidError as exc:
        raise HTTPException(502, str(exc))


@router.delete("/plaid/items/{item_id}")
def plaid_remove(item_id: str):
    try:
        banks.remove(item_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except plaid_client.PlaidError as exc:
        raise HTTPException(502, str(exc))
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


def _amount(text: str | None) -> Decimal | None:
    """Reads "$1,234.50" as Decimal("1234.50"). Raises InvalidOperation if it isn't a number."""
    return Decimal(text.replace("$", "").replace(",", "").strip()) if text else None


def _manual_cost_fields(body: ManualCostBody) -> dict:
    """The form as ManualCost columns, or a 422 that says what to fix."""
    try:
        amount = _amount(body.amount)
        due = date.fromisoformat(body.next_due_date) if body.next_due_date else None
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"Couldn't read amount {body.amount!r} or date {body.next_due_date!r}")
    if not body.name.strip():
        raise HTTPException(422, "Give it a name.")
    if amount is None or not amount.is_finite() or not 0 < amount < 10**9:
        raise HTTPException(422, "Enter an amount, like 45.20.")
    if body.frequency not in manual.PER_YEAR:
        raise HTTPException(422, f"How often should be one of: {', '.join(manual.PER_YEAR)}")
    if body.category not in manual.CATEGORIES:
        raise HTTPException(422, f"Unknown category {body.category!r}")
    if body.due_day is not None and not 1 <= body.due_day <= 31:
        raise HTTPException(422, "Day of the month should be 1 to 31.")
    return {
        "name": body.name.strip(),
        "category": body.category,
        "amount": amount.quantize(Decimal("0.01")),
        "frequency": body.frequency,
        "due_day": body.due_day,
        "next_due_date": due,
        "active": body.active,
    }


def _manual_cost_json(c: ManualCost, on: date) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "category": c.category,
        "amount": float(c.amount),
        "frequency": c.frequency,
        "due_day": c.due_day,
        # the entered date moved forward past today, so the form always shows the next one
        "next_due_date": _iso(manual.next_due(c, on)) if c.next_due_date else None,
        "active": c.active,
        "monthly": float(manual.monthly(c)),
    }


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
