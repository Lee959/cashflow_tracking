"""What actually left the bank accounts and credit cards (from Plaid transactions), and which
bank transaction paid each emailed bill.

Counting rules, so nothing is counted twice:
- only bank (depository) and credit card accounts; not loans or investments
- transfers between your own accounts don't count, and neither does paying off a credit card:
  the purchases on the card are already counted one by one
- "spent" is purchases; refunds and income are reported separately
Plaid's sign convention: amount > 0 is money out, amount < 0 is money in.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import Bill, PlaidAccount, PlaidItem, PlaidTransaction

SPENDING_ACCOUNT_TYPES = ("depository", "credit")
NOT_SPENDING = {"TRANSFER_IN", "TRANSFER_OUT", "INCOME"}
CARD_PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
MATCH_DAYS = 7  # a bill's payment can post a few days either side of its due date
RECENT = 8


def counts_as_spending(t: PlaidTransaction) -> bool:
    return t.category not in NOT_SPENDING and t.category_detailed != CARD_PAYMENT


def month_spending(month: str, on: date) -> dict | None:
    """Spending and income in `month` across connected banks, or None if no bank is connected."""
    accounts, items = _accounts()
    if not items:
        return None
    first = date(int(month[:4]), int(month[5:]), 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    txns = _transactions(accounts, first, min(last, on))

    spending = [t for t in txns if counts_as_spending(t)]
    purchases = [t for t in spending if t.amount > 0]
    refunds = [t for t in spending if t.amount < 0]
    income = [t for t in txns if t.category == "INCOME"]
    by_category: dict[str, Decimal] = defaultdict(Decimal)
    for t in purchases:
        by_category[t.category or "OTHER"] += t.amount
    synced = [i.last_synced_at for i in items if i.last_synced_at]

    return {
        "environment": settings.plaid_env,
        "is_test_data": settings.plaid_env != "production",
        "total": float(sum((t.amount for t in purchases), Decimal(0))),
        "refunds": float(-sum((t.amount for t in refunds), Decimal(0))),
        "income": float(-sum((t.amount for t in income), Decimal(0))),
        "count": len(purchases),
        "accounts": len(accounts),
        "by_category": [
            {"category": c, "total": float(v)} for c, v in sorted(by_category.items(), key=lambda kv: -kv[1])
        ],
        "recent": [_txn_json(t, accounts) for t in sorted(spending, key=lambda t: t.transaction_date, reverse=True)[:RECENT]],
        "last_synced_at": max(synced).isoformat() if synced else None,
    }


def match_bill_payments(bills: list[Bill]) -> dict[int, dict]:
    """Bill id -> the bank transaction that paid it: same amount, within a week of the due date.
    A transaction whose name mentions the biller ("HYDRO OTTAWA") wins over a same-amount stranger."""
    payable = [b for b in bills if b.amount and b.amount > 0 and b.due_date]
    accounts, items = _accounts()
    if not payable or not items:
        return {}
    window_start = min(b.due_date for b in payable) - timedelta(days=MATCH_DAYS)
    window_end = max(b.due_date for b in payable) + timedelta(days=MATCH_DAYS)
    candidates = [t for t in _transactions(accounts, window_start, window_end) if t.amount > 0]

    matches: dict[int, dict] = {}
    used: set[str] = set()
    for bill in sorted(payable, key=lambda b: b.due_date):
        words = [w for w in bill.biller.lower().split() if len(w) >= 3]
        best = None
        for t in candidates:
            gap = abs((t.transaction_date - bill.due_date).days)
            if t.transaction_id in used or gap > MATCH_DAYS or abs(t.amount - bill.amount) > Decimal("0.01"):
                continue
            text = f"{t.name} {t.merchant_name or ''}".lower()
            score = (not any(w in text for w in words), gap)  # name match first, then closest date
            if best is None or score < best[0]:
                best = (score, t)
        if best:
            t = best[1]
            used.add(t.transaction_id)
            matches[bill.id] = {"date": t.transaction_date.isoformat(), "account": _account_label(accounts.get(t.account_id)),
                                "name": t.name, "pending": t.pending}
    return matches


def _accounts() -> tuple[dict[str, PlaidAccount], list[PlaidItem]]:
    """Spending accounts of the banks connected in the current Plaid environment."""
    with session_scope() as s:
        items = list(s.scalars(select(PlaidItem).where(PlaidItem.environment == settings.plaid_env)))
        accounts = s.scalars(select(PlaidAccount).where(
            PlaidAccount.item_id.in_([i.item_id for i in items]),
            PlaidAccount.type.in_(SPENDING_ACCOUNT_TYPES),
        ))
        return {a.account_id: a for a in accounts}, items


def _transactions(accounts: dict[str, PlaidAccount], first: date, last: date) -> list[PlaidTransaction]:
    if not accounts:
        return []
    with session_scope() as s:
        return list(s.scalars(select(PlaidTransaction).where(
            PlaidTransaction.account_id.in_(list(accounts)),
            PlaidTransaction.transaction_date.between(first, last),
        )))


def _account_label(a: PlaidAccount | None) -> str:
    if a is None:
        return "bank account"
    return f"{a.name} ••{a.mask}" if a.mask else a.name


def _txn_json(t: PlaidTransaction, accounts: dict[str, PlaidAccount]) -> dict:
    return {
        "date": t.transaction_date.isoformat(),
        "name": t.merchant_name or t.name,
        "amount": float(t.amount),
        "category": t.category,
        "account": _account_label(accounts.get(t.account_id)),
        "pending": t.pending,
    }
