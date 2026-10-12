"""App settings, loaded from .env.

Variable names match the existing .env (including the PLAID_*_SECRETE spelling);
the correctly spelled PLAID_*_SECRET names are accepted too. Plaid secrets can instead live in
the macOS Keychain (`python -m app.cli secure-secrets` moves them there).

Data (database, bill PDFs) lives in ~/Library/Application Support/HouseholdBills, outside the
project folder, because ~/Documents may sync to iCloud. APP_DATA_DIR overrides it.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app import keychain

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
LEGACY_DATA_DIR = PROJECT_ROOT / "data"  # where data lived before it moved out of the project folder
DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "HouseholdBills"

load_dotenv(ENV_FILE)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


@dataclass(frozen=True)
class Settings:
    # Google OAuth client (Desktop app type)
    google_client_id: str
    google_client_secret: str
    google_project_id: str
    google_auth_uri: str
    google_token_uri: str
    google_cert_url: str

    # Plaid
    plaid_client_id: str
    plaid_env: str  # "sandbox" or "production"
    plaid_sandbox_secret: str
    plaid_production_secret: str

    # App
    base_url: str
    timezone: ZoneInfo
    data_dir: Path
    secrets_dir: Path
    bill_senders_file: Path

    @property
    def google_redirect_uri(self) -> str:
        # Desktop-app OAuth clients accept any http://localhost port/path, so
        # nothing needs registering in Cloud Console for this to work.
        return f"{self.base_url}/auth/google/callback"

    @property
    def google_client_config(self) -> dict:
        return {
            "installed": {
                "client_id": self.google_client_id,
                "client_secret": self.google_client_secret,
                "project_id": self.google_project_id,
                "auth_uri": self.google_auth_uri,
                "token_uri": self.google_token_uri,
                "auth_provider_x509_cert_url": self.google_cert_url,
                "redirect_uris": [self.google_redirect_uri],
            }
        }

    @property
    def plaid_secret(self) -> str:
        """From .env if it's still there, otherwise from the Keychain."""
        if self.plaid_env == "production":
            return self.plaid_production_secret or keychain.get("plaid-production-secret") or ""
        return self.plaid_sandbox_secret or keychain.get("plaid-sandbox-secret") or ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cashflow.db"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"


def load_settings() -> Settings:
    return Settings(
        google_client_id=_env("GOOGLE_CLIENT_ID"),
        google_client_secret=_env("GOOGLE_CLIENT_SECRET"),
        google_project_id=_env("GOOGLE_PROJECT_ID"),
        google_auth_uri=_env("GOOGLE_AUTH_URI", default="https://accounts.google.com/o/oauth2/auth"),
        google_token_uri=_env("GOOGLE_TOKEN_URI", default="https://oauth2.googleapis.com/token"),
        google_cert_url=_env("GOOGLE_AUTH_PROVIDER_CERT_URL", default="https://www.googleapis.com/oauth2/v1/certs"),
        plaid_client_id=_env("PLAID_CLIENT_ID"),
        plaid_env=_env("PLAID_ENV", default="sandbox").lower(),
        plaid_sandbox_secret=_env("PLAID_SENDBOX_SECRETE", "PLAID_SANDBOX_SECRET"),
        plaid_production_secret=_env("PLAID_PRODUCTION_SECRETE", "PLAID_PRODUCTION_SECRET"),
        base_url=_env("APP_BASE_URL", default="http://localhost:8000").rstrip("/"),
        timezone=ZoneInfo(_env("APP_TIMEZONE", default="America/Toronto")),
        data_dir=Path(_env("APP_DATA_DIR") or DEFAULT_DATA_DIR).expanduser(),
        secrets_dir=PROJECT_ROOT / "secrete",
        bill_senders_file=PROJECT_ROOT / "config" / "bill_senders.toml",
    )


settings = load_settings()
