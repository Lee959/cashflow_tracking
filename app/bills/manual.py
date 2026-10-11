"""Recurring fixed costs entered by hand, for payments that never send a bill email
(a gym membership, a rental water heater, a bill that goes to another inbox).

The dashboard adds them to the fixed bills at their per-month amount (see dashboard.fixed_costs).
"""

from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from app.bills.discover import CATEGORIES as ALL_CATEGORIES
from app.bills.service import NOT_IN_TOTALS
from app.db import session_scope
from app.models import ManualCost

# Payments per year for each frequency
PER_YEAR = {"monthly": 12, "every 2 months": 6, "quarterly": 4, "twice a year": 2, "yearly": 1}
# Card and investment statements aren't fixed costs, so they aren't offered here either.
CATEGORIES = [c for c in ALL_CATEGORIES if c not in NOT_IN_TOTALS]


def list_costs(active_only: bool = False) -> list[ManualCost]:
    with session_scope() as s:
        stmt = select(ManualCost).order_by(ManualCost.name, ManualCost.id)
        if active_only:
            stmt = stmt.where(ManualCost.active)
        return list(s.scalars(stmt))


def save_cost(
    cost_id: int | None,
    *,
    name: str,
    category: str,
    amount: Decimal,
    frequency: str,
    due_day: int | None,
    next_due_date: date | None,
    active: bool,
) -> ManualCost:
    """Adds a cost (cost_id None) or replaces one. Raises ValueError if cost_id doesn't exist."""
    with session_scope() as s:
        cost = ManualCost() if cost_id is None else s.get(ManualCost, cost_id)
        if cost is None:
            raise ValueError(f"Fixed cost {cost_id} not found")
        cost.name = name
        cost.category = category
        cost.amount = amount
        cost.frequency = frequency
        cost.due_day = due_day
        cost.next_due_date = next_due_date
        cost.active = active
        s.add(cost)
        s.flush()
        return cost


def delete_cost(cost_id: int) -> None:
    with session_scope() as s:
        cost = s.get(ManualCost, cost_id)
        if cost is None:
            raise ValueError(f"Fixed cost {cost_id} not found")
        s.delete(cost)


def monthly(cost: ManualCost) -> Decimal:
    return cost.amount * PER_YEAR[cost.frequency] / 12


def next_due(cost: ManualCost, on: date) -> date | None:
    """The first due date on or after `on`: the entered due date moved forward by the frequency,
    or for a monthly cost with only a day of the month, the next time that day comes round."""
    if cost.next_due_date:
        step = 12 // PER_YEAR[cost.frequency]
        due, n = cost.next_due_date, 0
        while due < on:
            n += 1
            due = cost.next_due_date + relativedelta(months=n * step)  # from the original: Jan 31 -> Mar 31
        return due
    if cost.due_day and cost.frequency == "monthly":
        this_month = on.replace(day=1)
        for first in (this_month, this_month + relativedelta(months=1)):
            due = first + relativedelta(day=cost.due_day)  # the 31st is the last day in shorter months
            if due >= on:
                return due
    return None
