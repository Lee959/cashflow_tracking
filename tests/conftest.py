"""Keep tests away from your real Keychain and data folder."""

import os
import tempfile

# Set before any app module is imported (app.config reads these at import time)
os.environ["APP_KEYCHAIN"] = "off"
os.environ["APP_DISABLE_LOGIN_FOR_TESTS"] = "1"
os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="householdbills-test-")

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from app import crypto  # noqa: E402

_REAL_FERNET = crypto._fernet.__wrapped__  # the uncached Keychain-backed loader, for tests that check it


@pytest.fixture
def real_fernet():
    return _REAL_FERNET


@pytest.fixture(autouse=True)
def test_encryption_key(monkeypatch):
    """A throwaway encryption key for each test instead of the one in the Keychain."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(crypto, "_fernet", lambda: key)
