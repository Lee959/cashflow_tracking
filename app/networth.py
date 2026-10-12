"""Net worth: what you own minus what you owe, and how it changes over time.

Sources: every account of the banks connected in the current Plaid environment (cash, investments,
credit cards, loans, mortgage) plus things entered by hand (the house, a car, a loan elsewhere).

Plaid only reports today's balances, so history is built here: each sync (and each change to a
manual item) records today's balance of every source in balance_snapshots. Today's rows are
rewritten each time, so a removed bank or deleted item drops out of today without touching the past.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.config import settings
from app.db import session_scope
from app.models import BalanceSnapshot, ManualAsset, PlaidAccount, PlaidItem

GROUP_LABELS = {
    "cash": "Cash",
    "investments": "Investments",
    "property": "Property",
    "vehicle": "Vehicles",
    "other_asset": "Other assets",
    "credit_cards": "Credit cards",
    "mortgage": "Mortgage",
    "loans": "Loans",
    "other_debt": "Other debts",
}
MANUAL_CATEGORIES = {
    "asset": ["property", "vehicle", "investments", "cash", "other_asset"],
    "debt": ["mortgage", "loans", "credit_cards", "other_debt"],
}
HISTORY_DAYS = 365


def today() -> date:
    return datetime.now(settings.timezone).date()


def take_snapshot(on: date | None = None) -> int:
    """Records today's balance of every source. Returns how many sources were recorded."""
    on = on or today()
    env = settings.plaid_env
    with session_scope() as s:
        items = {i.item_id: i for i in s.scalars(select(PlaidItem).where(PlaidItem.environment == env))}
        rows = []
        for a in s.scalars(select(PlaidAccount).where(PlaidAccount.item_id.in_(list(items)))):
            if a.current_balance is None:
                continue
            group, is_debt = _plaid_group(a)
            rows.append(BalanceSnapshot(snapshot_date=on, source_id=a.account_id, group=group, is_debt=is_debt,
                                        name=f"{items[a.item_id].institution_name or 'Bank'} · {a.name}",
                                        balance=a.current_balance, environment=env))
        for m in s.scalars(select(ManualAsset)):
            rows.append(BalanceSnapshot(snapshot_date=on, source_id=f"manual-{m.id}", name=m.name, group=m.category,
                                        is_debt=m.kind == "debt", balance=m.value, environment="manual"))
        s.execute(delete(BalanceSnapshot).where(BalanceSnapshot.snapshot_date == on,
                                                BalanceSnapshot.environment.in_([env, "manual"])))
        s.add_all(rows)
    return len(rows)


def summary(on: date | None = None) -> dict | None:
    """Current net worth with its parts, plus one point per day that has a snapshot (last year)."""
    on = on or today()
    env = settings.plaid_env
    with session_scope() as s:
        rows = list(s.scalars(select(BalanceSnapshot).where(
            BalanceSnapshot.environment.in_([env, "manual"]),
            BalanceSnapshot.snapshot_date.between(on - timedelta(days=HISTORY_DAYS), on),
        )))
    if not rows:
        return None

    by_date: dict[date, list[BalanceSnapshot]] = defaultdict(list)
    for r in rows:
        by_date[r.snapshot_date].append(r)
    history = [{"date": d.isoformat(), "net": float(_net(by_date[d]))} for d in sorted(by_date)]
    latest = max(by_date)
    current = by_date[latest]

    groups: dict[str, dict] = {}
    for r in sorted(current, key=lambda r: -r.balance):
        g = groups.setdefault(r.group, {"group": r.group, "label": GROUP_LABELS.get(r.group, r.group),
                                        "is_debt": r.is_debt, "total": Decimal(0), "items": []})
        g["total"] += r.balance
        g["items"].append({"name": r.name, "balance": float(r.balance), "manual": r.environment == "manual"})

    net = _net(current)
    month_ago = _point_on_or_after(by_date, latest - timedelta(days=30))
    return {
        "as_of": latest.isoformat(),
        "net": float(net),
        "assets": float(sum((r.balance for r in current if not r.is_debt), Decimal(0))),
        "debts": float(sum((r.balance for r in current if r.is_debt), Decimal(0))),
        "change_30d": float(net - _net(by_date[month_ago])) if month_ago and month_ago < latest else None,
        "since": month_ago.isoformat() if month_ago and month_ago < latest else None,
        "groups": [{**g, "total": float(g["total"])} for g in
                   sorted(groups.values(), key=lambda g: (g["is_debt"], -g["total"]))],
        "history": history,
        "is_test_data": env != "production" and any(r.environment == env for r in current),
    }


def list_assets() -> list[ManualAsset]:
    with session_scope() as s:
        return list(s.scalars(select(ManualAsset).order_by(ManualAsset.kind, ManualAsset.name)))


def save_asset(asset_id: int | None, *, name: str, kind: str, category: str, value: Decimal) -> ManualAsset:
    """Adds (asset_id None) or replaces a manual item, then updates today's snapshot."""
    with session_scope() as s:
        asset = ManualAsset() if asset_id is None else s.get(ManualAsset, asset_id)
        if asset is None:
            raise ValueError(f"Item {asset_id} not found")
        asset.name, asset.kind, asset.category, asset.value = name, kind, category, value
        s.add(asset)
        s.flush()
    take_snapshot()
    return asset


def delete_asset(asset_id: int) -> None:
    with session_scope() as s:
        asset = s.get(ManualAsset, asset_id)
        if asset is None:
            raise ValueError(f"Item {asset_id} not found")
        s.delete(asset)
    take_snapshot()


def _net(rows: list[BalanceSnapshot]) -> Decimal:
    return sum((-r.balance if r.is_debt else r.balance for r in rows), Decimal(0))


def _point_on_or_after(by_date: dict, day: date) -> date | None:
    return min((d for d in by_date if d >= day), default=None)


def _plaid_group(a: PlaidAccount) -> tuple[str, bool]:
    if a.type == "depository":
        return "cash", False
    if a.type in ("investment", "brokerage"):
        return "investments", False
    if a.type == "credit":
        return "credit_cards", True
    if a.type == "loan":
        return ("mortgage" if a.subtype in ("mortgage", "home equity") else "loans"), True
    return "other_asset", False
