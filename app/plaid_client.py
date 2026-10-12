"""Thin wrapper around the Plaid API for TD, RBC and CIBC accounts, balances and transactions.

Uses Sandbox (test banks, fake data) until PLAID_ENV=production is set in .env; production
access is still pending approval in the Plaid dashboard.
"""

import json

import certifi
import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from app.config import settings

HOSTS = {"sandbox": plaid.Environment.Sandbox, "production": plaid.Environment.Production}
SANDBOX_TEST_BANK = "ins_43"  # Tartan-Dominion Bank of Canada, one of Plaid's sandbox test banks
CLIENT_NAME = "Household Bills"
# Read-only products. Nothing here can move money (that would need Auth/Transfer, never requested).
# Liabilities and Investments are only added (and billed) where the bank supports them.
REQUIRED_PRODUCTS = ["transactions"]
OPTIONAL_PRODUCTS = ["liabilities", "investments"]
HISTORY_DAYS = 730  # ask for the full 24 months of transactions on the first sync


class PlaidError(Exception):
    """A Plaid API error, with Plaid's own explanation as the message."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def get_client() -> plaid_api.PlaidApi:
    if settings.plaid_env not in HOSTS:
        raise PlaidError(f"PLAID_ENV must be 'sandbox' or 'production', not {settings.plaid_env!r}")
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise PlaidError(f"PLAID_CLIENT_ID or the {settings.plaid_env} secret is missing from .env")
    configuration = plaid.Configuration(
        host=HOSTS[settings.plaid_env],
        api_key={"clientId": settings.plaid_client_id, "secret": settings.plaid_secret},
    )
    # python.org's macOS Python ships without system CA certs; use certifi's bundle.
    configuration.ssl_ca_cert = certifi.where()
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def country_codes() -> list[CountryCode]:
    # Sandbox also offers US test banks, which have the richest fake data
    codes = ["CA", "US"] if settings.plaid_env == "sandbox" else ["CA"]
    return [CountryCode(c) for c in codes]


def create_link_token(user_id: str = "household") -> str:
    """Token for opening Plaid Link in the browser to connect a bank."""
    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=user_id),
        client_name=CLIENT_NAME,
        products=[Products(p) for p in REQUIRED_PRODUCTS],
        optional_products=[Products(p) for p in OPTIONAL_PRODUCTS],
        transactions=LinkTokenTransactions(days_requested=HISTORY_DAYS),
        country_codes=country_codes(),
        language="en",
    )
    return _call(lambda c: c.link_token_create(request)).link_token


def create_update_link_token(access_token: str, user_id: str = "household") -> str:
    """Token for Link's update mode: sign in to the bank again when a connection stops working
    (password changed, consent expired). Keeps the same connection, history and billing."""
    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=user_id),
        client_name=CLIENT_NAME,
        country_codes=country_codes(),
        language="en",
        access_token=access_token,
    )
    return _call(lambda c: c.link_token_create(request)).link_token


def get_item_products(access_token: str) -> list[str]:
    """Products actually active on a connection (optional ones only if the bank supports them)."""
    item = _call(lambda c: c.item_get(ItemGetRequest(access_token=access_token))).item
    names = list(item.products or []) + list(getattr(item, "billed_products", None) or [])
    return sorted({getattr(p, "value", str(p)) for p in names})


def get_credit_card_liabilities(access_token: str) -> list[dict]:
    """Statement balance, due date and minimum payment for each credit card."""
    resp = _call(lambda c: c.liabilities_get(LiabilitiesGetRequest(access_token=access_token)))
    return [card.to_dict() for card in (resp.liabilities.credit or [])]


def exchange_public_token(public_token: str) -> tuple[str, str]:
    """Swaps Link's one-time public token for a permanent access token. Returns (access_token, item_id)."""
    resp = _call(lambda c: c.item_public_token_exchange(ItemPublicTokenExchangeRequest(public_token=public_token)))
    return resp.access_token, resp.item_id


def sandbox_public_token(institution_id: str = SANDBOX_TEST_BANK) -> str:
    """Sandbox only: a public token for a test bank without going through Link (for tests and demos)."""
    if settings.plaid_env != "sandbox":
        raise PlaidError("sandbox_public_token only works with PLAID_ENV=sandbox")
    request = SandboxPublicTokenCreateRequest(institution_id=institution_id, initial_products=[Products("transactions")])
    return _call(lambda c: c.sandbox_public_token_create(request)).public_token


def get_accounts(access_token: str) -> list[dict]:
    resp = _call(lambda c: c.accounts_get(AccountsGetRequest(access_token=access_token)))
    return [a.to_dict() for a in resp.accounts]


def sync_transactions(access_token: str, cursor: str | None) -> tuple[list[dict], list[dict], list[str], str]:
    """All transaction changes since `cursor`. Returns (added, modified, removed_ids, next_cursor)."""
    added: list[dict] = []
    modified: list[dict] = []
    removed: list[str] = []
    while True:
        kwargs = {"access_token": access_token, "count": 500}
        if cursor:
            kwargs["cursor"] = cursor
        resp = _call(lambda c: c.transactions_sync(TransactionsSyncRequest(**kwargs)))
        added += [t.to_dict() for t in resp.added]
        modified += [t.to_dict() for t in resp.modified]
        removed += [t.transaction_id for t in resp.removed]
        cursor = resp.next_cursor
        if not resp.has_more:
            return added, modified, removed, cursor


def remove_item(access_token: str) -> None:
    """Tells Plaid to forget the bank connection (revokes the access token)."""
    _call(lambda c: c.item_remove(ItemRemoveRequest(access_token=access_token)))


def _call(fn):
    try:
        return fn(get_client())
    except plaid.ApiException as exc:
        try:
            body = json.loads(exc.body)
        except (TypeError, ValueError):
            raise PlaidError(f"Plaid error {exc.status}: {exc.reason}") from exc
        message = body.get("display_message") or body.get("error_message") or str(exc.reason)
        raise PlaidError(message, body.get("error_code")) from exc
