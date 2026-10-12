"""Where secrets and data live: Keychain instead of files, a private data folder outside iCloud."""

import dataclasses
import os
import stat

import pytest
from cryptography.fernet import Fernet

from app import config, crypto, db, keychain, secure


@pytest.fixture
def fake_keychain(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setenv("APP_KEYCHAIN", "on")
    monkeypatch.setattr(keychain.keyring, "get_password", lambda service, name: store.get(f"{service}/{name}"))
    monkeypatch.setattr(keychain.keyring, "set_password",
                        lambda service, name, value: store.__setitem__(f"{service}/{name}", value))
    return store


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_existing_key_file_moves_into_the_keychain(fake_keychain, real_fernet, tmp_path, monkeypatch):
    key = Fernet.generate_key()
    (tmp_path / "app.key").write_bytes(key)
    monkeypatch.setattr(crypto, "settings", dataclasses.replace(config.settings, secrets_dir=tmp_path))
    fernet = real_fernet()
    assert fake_keychain["HouseholdBills/encryption-key"] == key.decode()
    assert not (tmp_path / "app.key").exists()  # no copy left in the iCloud-synced folder
    # tokens encrypted with the old file's key still decrypt
    assert fernet.decrypt(Fernet(key).encrypt(b"token")) == b"token"
    assert real_fernet().decrypt(fernet.encrypt(b"again")) == b"again"  # next run reads the Keychain


def test_a_new_key_is_created_straight_in_the_keychain(fake_keychain, real_fernet, tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "settings", dataclasses.replace(config.settings, secrets_dir=tmp_path))
    real_fernet()
    assert "HouseholdBills/encryption-key" in fake_keychain and list(tmp_path.iterdir()) == []


def test_keychain_failure_never_deletes_the_key_file(real_fernet, tmp_path, monkeypatch):
    (tmp_path / "app.key").write_bytes(Fernet.generate_key())
    monkeypatch.setattr(crypto, "settings", dataclasses.replace(config.settings, secrets_dir=tmp_path))
    monkeypatch.setenv("APP_KEYCHAIN", "off")  # put() refuses
    with pytest.raises(keychain.KeychainUnavailable):
        real_fernet()
    assert (tmp_path / "app.key").exists()


def test_plaid_secrets_move_from_env_to_keychain(fake_keychain, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GOOGLE_CLIENT_ID=abc\nPLAID_CLIENT_ID=cid\nPLAID_PRODUCTION_SECRETE=prod-secret\n"
                   "PLAID_SENDBOX_SECRETE = sandbox-secret\nPLAID_ENV=production\n")
    os.chmod(env, 0o644)
    moved = secure.move_secrets_to_keychain(env)
    assert moved == ["PLAID_PRODUCTION_SECRETE", "PLAID_SENDBOX_SECRETE"]
    assert env.read_text() == "GOOGLE_CLIENT_ID=abc\nPLAID_CLIENT_ID=cid\nPLAID_ENV=production\n"
    assert mode(env) == 0o600
    assert fake_keychain["HouseholdBills/plaid-production-secret"] == "prod-secret"
    assert secure.move_secrets_to_keychain(env) == []  # nothing left to move

    # settings fall back to the Keychain once .env no longer has the secret
    s = dataclasses.replace(config.settings, plaid_env="production", plaid_production_secret="")
    assert s.plaid_secret == "prod-secret"


def test_secrets_stay_in_env_if_the_keychain_fails(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PLAID_PRODUCTION_SECRETE=prod-secret\n")
    monkeypatch.setenv("APP_KEYCHAIN", "off")
    with pytest.raises(keychain.KeychainUnavailable):
        secure.move_secrets_to_keychain(env)
    assert "prod-secret" in env.read_text()


def test_lock_down_makes_secret_files_private(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    (tmp_path / "secrete").mkdir()
    (tmp_path / "secrete" / "client_secret.json").write_text("{}")
    os.chmod(tmp_path / ".env", 0o644)
    secure.lock_down(tmp_path)
    assert (mode(tmp_path / ".env"), mode(tmp_path / "secrete"), mode(tmp_path / "secrete" / "client_secret.json")) == (
        0o600, 0o700, 0o600)


def test_legacy_data_moves_out_of_the_project_folder(tmp_path):
    old, new = tmp_path / "project" / "data", tmp_path / "Application Support" / "HouseholdBills"
    (old / "attachments" / "m1").mkdir(parents=True)
    (old / "cashflow.db").write_bytes(b"db")
    (old / "attachments" / "m1" / "bill.pdf").write_bytes(b"%PDF-")
    assert db.move_legacy_data(old, new) is True
    assert not old.exists()
    assert (new / "cashflow.db").read_bytes() == b"db" and (new / "attachments" / "m1" / "bill.pdf").exists()

    db.restrict_permissions(new)
    assert mode(new) == 0o700 and mode(new / "cashflow.db") == 0o600 and mode(new / "attachments" / "m1") == 0o700
    assert db.move_legacy_data(old, new) is False  # nothing left to move


def test_never_overwrites_an_existing_database(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "cashflow.db").write_bytes(b"old")
    (new / "cashflow.db").write_bytes(b"new")
    assert db.move_legacy_data(old, new) is False
    assert (new / "cashflow.db").read_bytes() == b"new" and (old / "cashflow.db").exists()


def test_default_data_folder_is_outside_the_project():
    assert "Library/Application Support/HouseholdBills" in str(config.DEFAULT_DATA_DIR)
    assert config.PROJECT_ROOT not in config.DEFAULT_DATA_DIR.parents
