"""Secrets kept in the macOS Keychain instead of files.

The project folder lives in ~/Documents, which syncs to iCloud; the encryption key and the Plaid
secrets must not. Keychain items are encrypted by macOS and only readable by your user account.
Set APP_KEYCHAIN=off to disable (the tests do, so they never touch your real Keychain).
"""

import os

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "HouseholdBills"


class KeychainUnavailable(RuntimeError):
    pass


def enabled() -> bool:
    return os.getenv("APP_KEYCHAIN", "on").lower() != "off"


def get(name: str) -> str | None:
    if not enabled():
        return None
    try:
        return keyring.get_password(SERVICE, name)
    except KeyringError as exc:
        raise KeychainUnavailable(f"Couldn't read {name!r} from the Keychain: {exc}") from exc


def put(name: str, value: str) -> None:
    """Stores a secret and reads it back, so callers can safely delete the old copy afterwards."""
    if not enabled():
        raise KeychainUnavailable("The Keychain is disabled (APP_KEYCHAIN=off)")
    try:
        keyring.set_password(SERVICE, name, value)
    except KeyringError as exc:
        raise KeychainUnavailable(f"Couldn't save {name!r} to the Keychain: {exc}") from exc
    if get(name) != value:
        raise KeychainUnavailable(f"{name!r} didn't read back correctly from the Keychain")


def delete(name: str) -> None:
    if not enabled():
        return
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass
