"""Numbers for the dashboard: one month's bills plus the 12-month trend. Never shows future months."""

import re
from datetime import date, datetime
from decimal import Decimal

from app.bills import manual, service
from app.config import settings
from app.models import Bill

HISTORY_MONTHS = 12


def today() -> date:
    return datetime.now(settings.timezone).date()


def clamp_month(month: str | None) -> str:
    """Bad or future months fall back to the current month."""
    current = service.current_month()
    if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) or month > current:
        return current
    return month


def month_summary(month: str, on: date | None = None) -> dict:
    on = on or today()
    month = clamp_month(month)
    current = service.current_month()

    bills = [b for b in service.bills_for_month(month) if b.status != "ignored"]
    counted = [b for b in bills if b.category not in service.NOT_IN_TOTALS]
    statements = [b for b in bills if b.category in service.NOT_IN_TOTALS]
    upcoming = [b for b in counted if b.due_date and b.due_date >= on]

    by_category: dict[str, Decimal] = {}
    for b in counted:
        by_category[b.category] = by_category.get(b.category, Decimal(0)) + (b.amount or 0)

    # The trend always runs up to this month (or the selected one, if that's older than a year).
    end = current if service.shift_month(current, -(HISTORY_MONTHS - 1)) <= month else month
    history = service.monthly_totals(end, HISTORY_MONTHS)

    return {
        "fixed": fixed_costs(on),
        "month": month,
        "current_month": current,
        "prev_month": service.shift_month(month, -1),
        "next_month": service.shift_month(month, 1) if month < current else None,
        "today": on.isoformat(),
        "total": _sum(counted),
        "count": len(counted),
        "remaining": _sum(upcoming),
        "next_bill": _bill_json(upcoming[0], on) if upcoming else None,
        "history": [{"month": m, "total": float(t)} for m, t in history],
        "categories": [
            {"category": c, "total": float(t)} for c, t in sorted(by_category.items(), key=lambda kv: -kv[1])
        ],
        "bills": [_bill_json(b, on) for b in counted],
        "statements": {"count": len(statements), "total": _sum(statements)},
        "needs_review": sum(1 for b in bills if b.status == "needs_review"),
    }


def fixed_costs(on: date | None = None) -> dict | None:
    """What the regular bills cost per month, per biller, over the last 12 months.

    Each biller's bills in the window are summed and spread over the months covered, so
    water billed every 2 months and property tax billed twice a year become a per-month
    amount. With less than 12 months of history, the months actually covered are used.
    Costs entered by hand (no bill email) are added at their per-month amount.
    """
    on = on or today()
    current = service.current_month()
    first = service.shift_month(current, -(HISTORY_MONTHS - 1))
    earliest = service.earliest_bill_month()
    manual_costs = manual.list_costs(active_only=True)
    if earliest is None and not manual_costs:
        return None
    start = max(first, earliest or current)
    months = _months_between(start, current) + 1

    by_biller: dict[str, list[Bill]] = {}
    for b in service.counted_bills(start, current):
        by_biller.setdefault(b.biller, []).append(b)

    items = []
    for biller, bills in by_biller.items():
        total = sum((b.amount for b in bills), Decimal(0))
        per_year = len(bills) * 12 / months
        last = max(bills, key=lambda b: (b.due_date or date.min, b.id))
        items.append({
            "biller": biller,
            "category": bills[0].category,
            "monthly": float(total / months),
            "yearly": float(total / months * 12),
            "cadence": _cadence(per_year),
            "bills": len(bills),
            "last_amount": float(last.amount),
            "last_due_date": last.due_date.isoformat() if last.due_date else None,
            "source": "email",
        })
    for c in manual_costs:
        due = manual.next_due(c, on)
        items.append({
            "biller": c.name,
            "category": c.category,
            "monthly": float(manual.monthly(c)),
            "yearly": float(c.amount * manual.PER_YEAR[c.frequency]),
            "cadence": _cadence(manual.PER_YEAR[c.frequency]),
            "id": c.id,
            "amount": float(c.amount),
            "next_due_date": due.isoformat() if due else None,
            "source": "manual",
        })
    items.sort(key=lambda i: -i["monthly"])
    monthly = sum(i["monthly"] for i in items)
    return {
        "monthly": monthly,
        "yearly": monthly * 12,
        "from_month": start,
        "to_month": current,
        "months": months,
        "items": items,
    }


def _cadence(per_year: float) -> str:
    if per_year >= 9:
        return "monthly"
    if per_year >= 5:
        return "every 2 months"
    if per_year >= 3:
        return "quarterly"
    if per_year >= 1.5:
        return "twice a year"
    return "once a year"


def _months_between(a: str, b: str) -> int:
    (ya, ma), (yb, mb) = (map(int, a.split("-")), map(int, b.split("-")))
    return (yb - ya) * 12 + (mb - ma)


def _sum(bills: list[Bill]) -> float:
    return float(sum((b.amount or 0 for b in bills), Decimal(0)))


def _bill_json(b: Bill, on: date) -> dict:
    return {
        "id": b.id,
        "biller": b.biller,
        "category": b.category,
        "amount": float(b.amount) if b.amount is not None else None,
        "due_date": b.due_date.isoformat() if b.due_date else None,
        "upcoming": bool(b.due_date and b.due_date >= on),
        "status": b.status,
        "notes": b.parse_notes,
        "subject": b.email.subject,
        "received_at": b.email.received_at.isoformat(),
        "attachments": [{"id": a.id, "filename": a.filename} for a in b.email.attachments],
    }
