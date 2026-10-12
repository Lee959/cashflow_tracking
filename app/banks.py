"""Bank connections through Plaid: connect, reconnect, sync balances and transactions, list, remove.

Safety rules this module keeps:
- Access tokens are encrypted before they're stored and are never returned by any function here
  except to make Plaid calls; list_items() and every API response leave them out.
- Each connection remembers which Plaid environment it came from, so sandbox test data is never
  mixed with real bank data: only connections from the current PLAID_ENV are shown or synced.
- The same bank login can't be connected twice (Plaid bills per connection); a second login at
  the same bank (another family member's accounts) is fine.
"""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select

from app import plaid_client
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import session_scope
from app.models import PlaidAccount, PlaidItem, PlaidTransaction

log = logging.getLogger(__name__)

# Errors that mean "sign in to the bank again" (Plaid's update mode fixes them)
RECONNECT_CODES = {"ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "PENDING_DISCONNECT", "USER_PERMISSION_REVOKED"}


class DuplicateConnection(Exception):
    pass


def connect(public_token: str, institution_name: str | None, institution_id: str | None) -> dict:
    """Finishes a Plaid Link connection: stores the (encrypted) access token, then pulls accounts and
    transactions. A brand-new connection can take a few minutes to have its history; later syncs
    (Sync, or the automatic fetch) pick it up."""
    access_token, item_id = plaid_client.exchange_public_token(public_token)
    accounts = plaid_client.get_accounts(access_token)
    if existing := _same_login_already_connected(institution_id, accounts):
        try:
            plaid_client.remove_item(access_token)  # don't keep, or pay for, a second copy
        except plaid_client.PlaidError:
            log.exception("Couldn't remove the duplicate connection at Plaid")
        raise DuplicateConnection(
            f"{institution_name or 'This bank'} is already connected as “{existing}”. "
            "If it stopped updating, use Reconnect on that one instead."
        )
    products = plaid_client.get_item_products(access_token)
    with session_scope() as s:
        item = s.scalar(select(PlaidItem).where(PlaidItem.item_id == item_id)) or PlaidItem(item_id=item_id)
        item.access_token = encrypt(access_token)
        item.institution_name = institution_name
        item.institution_id = institution_id
        item.environment = settings.plaid_env
        item.products = ",".join(products)
        s.add(item)
    return sync_item(item_id)


def update_link_token(item_id: str) -> str:
    """Link token for reconnecting an existing connection (update mode)."""
    with session_scope() as s:
        access_token = decrypt(_get(s, item_id).access_token)
    return plaid_client.create_update_link_token(access_token)


def sync_item(item_id: str) -> dict:
    """Refreshes balances (and card statements) and pulls new, changed and removed transactions."""
    with session_scope() as s:
        item = _get(s, item_id)
        access_token, cursor = decrypt(item.access_token), item.transactions_cursor
        has_liabilities = "liabilities" in (item.products or "").split(",")
    try:
        accounts = plaid_client.get_accounts(access_token)
        added, modified, removed, next_cursor = plaid_client.sync_transactions(access_token, cursor)
    except plaid_client.PlaidError as exc:
        with session_scope() as s:
            item = _get(s, item_id)
            item.last_error, item.last_error_code = str(exc), exc.code
        raise

    cards = []
    if has_liabilities:
        try:
            cards = plaid_client.get_credit_card_liabilities(access_token)
        except plaid_client.PlaidError as exc:  # e.g. PRODUCT_NOT_READY right after connecting
            log.info("Card statements not available yet for %s: %s", item_id, exc)

    with session_scope() as s:
        for a in accounts:
            s.merge(_account(item_id, a))
        s.flush()
        for card in cards:
            if account := s.get(PlaidAccount, card["account_id"]):
                account.statement_balance = _money(card.get("last_statement_balance"))
                account.statement_date = card.get("last_statement_issue_date")
                account.minimum_payment = _money(card.get("minimum_payment_amount"))
                account.payment_due_date = card.get("next_payment_due_date")
                account.is_overdue = card.get("is_overdue")
        for t in added + modified:
            s.merge(_transaction(item_id, t))
        if removed:
            s.execute(delete(PlaidTransaction).where(PlaidTransaction.transaction_id.in_(removed)))
        item = _get(s, item_id)
        item.transactions_cursor = next_cursor
        item.last_synced_at = datetime.now()
        item.last_error = item.last_error_code = None
    return {"item_id": item_id, "accounts": len(accounts), "added": len(added), "modified": len(modified),
            "removed": len(removed)}


def sync_all() -> list[dict]:
    """Syncs every connection from the current environment; one failing bank doesn't stop the rest."""
    results = []
    for item in list_items():
        try:
            results.append(sync_item(item["item_id"]))
        except plaid_client.PlaidError as exc:
            results.append({"item_id": item["item_id"], "error": f"{item['institution_name']}: {exc}"})
    return results


def list_items() -> list[dict]:
    """Connections in the current environment, without their access tokens."""
    with session_scope() as s:
        items = list(s.scalars(
            select(PlaidItem).where(PlaidItem.environment == settings.plaid_env).order_by(PlaidItem.created_at)
        ))
        accounts = list(s.scalars(
            select(PlaidAccount).where(PlaidAccount.item_id.in_([i.item_id for i in items]))
            .order_by(PlaidAccount.type, PlaidAccount.name)
        ))
        counts = dict(s.execute(
            select(PlaidTransaction.item_id, func.count()).group_by(PlaidTransaction.item_id)
        ).all())
    return [
        {
            "item_id": i.item_id,
            "institution_name": i.institution_name or "Bank",
            "environment": i.environment,
            "products": [p for p in (i.products or "").split(",") if p],
            "last_synced_at": i.last_synced_at.isoformat() if i.last_synced_at else None,
            "last_error": i.last_error,
            "needs_reconnect": i.last_error_code in RECONNECT_CODES,
            "transaction_count": counts.get(i.item_id, 0),
            "accounts": [_account_json(a) for a in accounts if a.item_id == i.item_id],
        }
        for i in items
    ]


def remove(item_id: str) -> None:
    """Revokes the connection at Plaid and deletes its accounts and transactions here."""
    with session_scope() as s:
        access_token = decrypt(_get(s, item_id).access_token)
    try:
        plaid_client.remove_item(access_token)
    except plaid_client.PlaidError as exc:
        if exc.code not in ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"):  # already gone at Plaid: just clean up
            raise
    with session_scope() as s:
        s.execute(delete(PlaidTransaction).where(PlaidTransaction.item_id == item_id))
        s.execute(delete(PlaidAccount).where(PlaidAccount.item_id == item_id))
        s.execute(delete(PlaidItem).where(PlaidItem.item_id == item_id))


def _same_login_already_connected(institution_id: str | None, accounts: list[dict]) -> str | None:
    """Name of an existing connection at the same bank that shares an account (same name and last
    digits) with the new one, i.e. the same login connected again."""
    if not institution_id:
        return None
    new = {(a.get("name"), a.get("mask")) for a in accounts}
    with session_scope() as s:
        for item in s.scalars(select(PlaidItem).where(PlaidItem.environment == settings.plaid_env,
                                                      PlaidItem.institution_id == institution_id)):
            existing = {(a.name, a.mask) for a in s.scalars(select(PlaidAccount).where(PlaidAccount.item_id == item.item_id))}
            if new & existing:
                return item.institution_name or "Bank"
    return None


def _get(s, item_id: str) -> PlaidItem:
    item = s.scalar(select(PlaidItem).where(PlaidItem.item_id == item_id))
    if item is None:
        raise KeyError(f"No bank connection {item_id}")
    return item


def _money(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _account(item_id: str, a: dict) -> PlaidAccount:
    balances = a.get("balances") or {}
    return PlaidAccount(
        account_id=a["account_id"],
        item_id=item_id,
        name=a.get("name") or "Account",
        official_name=a.get("official_name"),
        mask=a.get("mask"),
        type=str(a.get("type")),
        subtype=str(a["subtype"]) if a.get("subtype") else None,
        currency=balances.get("iso_currency_code"),
        current_balance=_money(balances.get("current")),
        available_balance=_money(balances.get("available")),
        credit_limit=_money(balances.get("limit")),
        updated_at=datetime.now(),
    )


def _transaction(item_id: str, t: dict) -> PlaidTransaction:
    category = t.get("personal_finance_category") or {}
    return PlaidTransaction(
        transaction_id=t["transaction_id"],
        account_id=t["account_id"],
        item_id=item_id,
        transaction_date=t["date"],
        name=t.get("name") or "",
        merchant_name=t.get("merchant_name"),
        amount=_money(t["amount"]),
        currency=t.get("iso_currency_code"),
        category=category.get("primary"),
        category_detailed=category.get("detailed"),
        pending=bool(t.get("pending")),
        payment_channel=str(t["payment_channel"]) if t.get("payment_channel") else None,
    )


def _account_json(a: PlaidAccount) -> dict:
    def num(v):
        return float(v) if v is not None else None

    return {
        "account_id": a.account_id,
        "name": a.name,
        "mask": a.mask,
        "type": a.type,
        "subtype": a.subtype,
        "currency": a.currency,
        "current_balance": num(a.current_balance),
        "available_balance": num(a.available_balance),
        "credit_limit": num(a.credit_limit),
        "statement_balance": num(a.statement_balance),
        "statement_date": a.statement_date.isoformat() if a.statement_date else None,
        "minimum_payment": num(a.minimum_payment),
        "payment_due_date": a.payment_due_date.isoformat() if a.payment_due_date else None,
        "is_overdue": a.is_overdue,
    }
