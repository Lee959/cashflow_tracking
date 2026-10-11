"""Phase 2 (not wired into the UI yet): Plaid for TD, RBC and CIBC transactions and balances.

Uses Sandbox until PLAID_ENV=production is set in .env (production access is still pending
approval in the Plaid dashboard).
"""

import certifi
import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

from app.config import settings

HOSTS = {"sandbox": plaid.Environment.Sandbox, "production": plaid.Environment.Production}


def get_client() -> plaid_api.PlaidApi:
    if settings.plaid_env not in HOSTS:
        raise ValueError(f"PLAID_ENV must be 'sandbox' or 'production', not {settings.plaid_env!r}")
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise ValueError(f"PLAID_CLIENT_ID or the {settings.plaid_env} secret is missing from .env")
    configuration = plaid.Configuration(
        host=HOSTS[settings.plaid_env],
        api_key={"clientId": settings.plaid_client_id, "secret": settings.plaid_secret},
    )
    # python.org's macOS Python ships without system CA certs; use certifi's bundle.
    configuration.ssl_ca_cert = certifi.where()
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def create_link_token(user_id: str = "household") -> str:
    """Token for opening Plaid Link in the browser to connect a bank."""
    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=user_id),
        client_name="Cashflow Tracker",
        products=[Products("transactions")],
        country_codes=[CountryCode("CA")],
        language="en",
    )
    return get_client().link_token_create(request).link_token
