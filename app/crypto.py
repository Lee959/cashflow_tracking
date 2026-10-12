"""Encrypts tokens (Google refresh token, Plaid access tokens) before they hit the database.

The key lives in the macOS Keychain, never next to the database, so a copied database alone
doesn't expose your Gmail or bank access. A key from the old secrete/app.key file is moved into
the Keychain the first time it's needed, and the file is deleted (that folder syncs to iCloud).
"""

from functools import lru_cache

from cryptography.fernet import Fernet

from app import keychain
from app.config import settings

KEY_NAME = "encryption-key"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = keychain.get(KEY_NAME)
    if key is None:
        legacy = settings.secrets_dir / "app.key"
        key = legacy.read_text().strip() if legacy.exists() else Fernet.generate_key().decode()
        keychain.put(KEY_NAME, key)  # reads it back before we rely on it
        if legacy.exists():
            legacy.unlink()
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
