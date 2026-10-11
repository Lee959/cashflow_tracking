"""Encrypts tokens (Google refresh token, Plaid access tokens) before they hit the database.

The key lives in secrete/app.key (git-ignored), separate from data/cashflow.db, so a
copied database alone doesn't expose your Gmail or bank access.
"""

import os
from functools import lru_cache

from cryptography.fernet import Fernet

from app.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key_file = settings.secrets_dir / "app.key"
    if not key_file.exists():
        settings.secrets_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(Fernet.generate_key())
        os.chmod(key_file, 0o600)
    return Fernet(key_file.read_bytes())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
