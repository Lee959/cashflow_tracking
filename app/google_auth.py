"""Gmail OAuth: connect once in the browser; the refresh token is stored encrypted and reused."""

import json
import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import session_scope
from app.models import OAuthToken

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = [GMAIL_READONLY]

# Google may report scopes in a different order/format than requested; we check
# for gmail.readonly ourselves after the exchange instead.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# state -> PKCE code_verifier, for logins started but not finished yet.
# In-memory is fine: this is a single-process app on localhost.
_pending_logins: dict[str, str] = {}


class GmailNotConnected(Exception):
    pass


def _flow(**kwargs) -> Flow:
    return Flow.from_client_config(
        settings.google_client_config,
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
        **kwargs,
    )


def build_authorization_url() -> str:
    if not settings.google_client_id or not settings.google_client_secret:
        raise GmailNotConnected("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are missing from .env")
    flow = _flow()
    # prompt=consent makes Google return a refresh token every time we connect.
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    _pending_logins[state] = flow.code_verifier
    return url


def complete_authorization(state: str, code: str) -> str:
    """Exchanges the code from Google's redirect for tokens. Returns the connected Gmail address."""
    verifier = _pending_logins.pop(state, None)
    if verifier is None:
        raise GmailNotConnected("This login link expired (or the server restarted). Click Connect Gmail again.")
    flow = _flow(state=state, code_verifier=verifier, autogenerate_code_verifier=False)
    token = flow.fetch_token(code=code)
    if GMAIL_READONLY not in token.get("scope", ""):
        raise GmailNotConnected("Gmail read access wasn't granted. Connect again and tick the Gmail checkbox.")

    creds = flow.credentials
    email = _gmail_address(creds)
    _save(creds, email)
    return email


def get_credentials() -> Credentials:
    with session_scope() as s:
        row = s.query(OAuthToken).filter_by(provider="google").one_or_none()
        if row is None:
            raise GmailNotConnected("Gmail isn't connected yet.")
        info = json.loads(decrypt(row.token_json))
        email = row.account_email

    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            # While the OAuth consent screen is in "Testing" mode, Google expires
            # refresh tokens after 7 days; publishing the app removes that limit.
            raise GmailNotConnected(f"Gmail access expired or was revoked ({exc}). Connect Gmail again.") from exc
        _save(creds, email)
    return creds


def connected_account() -> str | None:
    with session_scope() as s:
        row = s.query(OAuthToken).filter_by(provider="google").one_or_none()
        return row.account_email if row else None


def disconnect() -> None:
    with session_scope() as s:
        s.query(OAuthToken).filter_by(provider="google").delete()


def _gmail_address(creds: Credentials) -> str:
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service.users().getProfile(userId="me").execute(num_retries=6)["emailAddress"]


def _save(creds: Credentials, email: str | None) -> None:
    with session_scope() as s:
        row = s.query(OAuthToken).filter_by(provider="google").one_or_none()
        if row is None:
            row = OAuthToken(provider="google")
            s.add(row)
        row.account_email = email
        row.token_json = encrypt(creds.to_json())
