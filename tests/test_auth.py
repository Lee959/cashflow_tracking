"""Login: launch link -> session cookie -> Touch ID passkey -> unlocked. The WebAuthn signature check
itself is the webauthn library's job; here it's replaced by fakes so every other step runs for real."""

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth, db, security
from app.main import app

LAUNCH = "launch-secret-for-tests"


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(auth, "ENFORCED", True)
    monkeypatch.setenv("APP_LAUNCH_TOKEN", LAUNCH)
    calls = {}

    def fake_register(**kwargs):
        calls["register"] = kwargs
        return SimpleNamespace(credential_id=b"cred-1", credential_public_key=b"public-key", sign_count=0)

    def fake_authenticate(**kwargs):
        calls["authenticate"] = kwargs
        return SimpleNamespace(new_sign_count=kwargs["credential_current_sign_count"] + 1)

    monkeypatch.setattr(auth.webauthn, "verify_registration_response", fake_register)
    monkeypatch.setattr(auth.webauthn, "verify_authentication_response", fake_authenticate)
    c = TestClient(app, base_url="http://localhost")
    c.calls = calls
    return c


def sign_in(client):
    r = client.get(f"/auth/start?t={LAUNCH}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    return r


def set_up_touch_id(client):
    client.post("/auth/passkey/register/options")
    return client.post("/auth/passkey/register/verify", json={"id": "Y3JlZC0x", "response": {}})


def test_everything_is_locked_without_a_session(client):
    assert client.get("/auth/status").json() == {"state": "none"}
    r = client.get("/api/dashboard")
    assert r.status_code == 401 and r.json()["state"] == "none"
    assert client.get("/attachments/1").status_code == 401
    assert client.post("/api/sync", json={}).status_code == 401
    assert client.get("/auth/google/login", follow_redirects=False).status_code == 401


def test_wrong_launch_link_gives_no_session(client):
    r = client.get("/auth/start?t=guess", follow_redirects=False)
    assert r.headers["location"] == "/?login=expired" and "set-cookie" not in r.headers
    assert client.get("/auth/status").json() == {"state": "none"}


def test_launch_link_sets_a_private_cookie_but_data_still_needs_touch_id(client):
    r = sign_in(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/auth/status").json() == {"state": "needs_setup"}
    assert client.get("/api/dashboard").status_code == 401


def test_touch_id_setup_requires_this_macs_authenticator_and_user_verification(client):
    sign_in(client)
    options = client.post("/auth/passkey/register/options").json()
    assert options["rp"]["id"] == "localhost"
    assert options["authenticatorSelection"]["authenticatorAttachment"] == "platform"
    assert options["authenticatorSelection"]["userVerification"] == "required"

    assert client.post("/auth/passkey/register/verify", json={"id": "Y3JlZC0x"}).json() == {"state": "unlocked"}
    sent = client.calls["register"]
    assert sent["require_user_verification"] is True and sent["expected_rp_id"] == "localhost"
    assert "http://localhost:8000" in sent["expected_origin"]
    assert client.get("/api/dashboard").status_code == 200


def test_lock_and_unlock_with_touch_id(client):
    sign_in(client)
    set_up_touch_id(client)
    assert client.post("/auth/lock").json() == {"state": "locked"}
    assert client.get("/api/dashboard").status_code == 401

    options = client.post("/auth/passkey/unlock/options").json()
    assert options["userVerification"] == "required" and options["rpId"] == "localhost"
    assert client.post("/auth/passkey/unlock/verify", json={"id": "Y3JlZC0x"}).json() == {"state": "unlocked"}
    assert client.calls["authenticate"]["require_user_verification"] is True
    assert client.get("/api/dashboard").status_code == 200


def test_challenge_works_once_and_unknown_passkeys_fail(client):
    sign_in(client)
    set_up_touch_id(client)
    client.post("/auth/lock")
    client.post("/auth/passkey/unlock/options")
    assert client.post("/auth/passkey/unlock/verify", json={"id": "bm9wZQ"}).status_code == 403  # unknown passkey
    assert client.post("/auth/passkey/unlock/verify", json={"id": "Y3JlZC0x"}).status_code == 403  # challenge used up
    assert client.get("/auth/status").json() == {"state": "locked"}


def test_a_failed_signature_check_stays_locked(client, monkeypatch):
    sign_in(client)
    set_up_touch_id(client)
    client.post("/auth/lock")

    def forged(**kwargs):
        raise ValueError("signature mismatch")

    monkeypatch.setattr(auth.webauthn, "verify_authentication_response", forged)
    client.post("/auth/passkey/unlock/options")
    r = client.post("/auth/passkey/unlock/verify", json={"id": "Y3JlZC0x"})
    assert r.status_code == 403 and "didn't verify" in r.json()["detail"]
    assert client.get("/api/dashboard").status_code == 401


def test_no_second_passkey_can_be_added_while_locked(client):
    sign_in(client)
    set_up_touch_id(client)
    client.post("/auth/lock")
    assert client.post("/auth/passkey/register/options").status_code == 403


def test_locks_itself_after_30_idle_minutes(client):
    sign_in(client)
    set_up_touch_id(client)
    cookie = client.cookies.get(auth.COOKIE)
    assert auth.state(cookie) == auth.UNLOCKED
    assert auth.state(cookie, now=datetime.now() + timedelta(minutes=31)) == auth.LOCKED
    assert auth.state(cookie, now=datetime.now() + timedelta(days=8)) == auth.NONE  # session expired


def test_other_websites_cant_lock_or_unlock(client):
    sign_in(client)
    assert client.post("/auth/lock", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/auth/passkey/register/options", headers={"Origin": "https://evil.example"}).status_code == 403


def test_reset_forgets_passkeys_and_sessions(client):
    sign_in(client)
    set_up_touch_id(client)
    assert auth.reset() == 1
    assert client.get("/auth/status").json() == {"state": "none"}


def test_no_api_map_and_no_caching(client):
    sign_in(client)
    set_up_touch_id(client)
    for path in ["/docs", "/redoc", "/openapi.json"]:
        r = client.get(path)
        assert "openapi" not in r.text.lower() and "swagger" not in r.text.lower(), path
    assert client.get("/api/dashboard").headers["cache-control"] == "no-store"


def test_logs_never_show_tokens_or_the_launch_secret():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s"', (
        "127.0.0.1", "GET", "/auth/start?t=abc123secret&x=1"), None)
    security.RedactSecrets().filter(record)
    assert "abc123secret" not in record.getMessage() and "t=[redacted]" in record.getMessage()

    record = logging.LogRecord("app", logging.ERROR, "", 0, "bad token access-production-1234-abcd", None, None)
    security.RedactSecrets().filter(record)
    assert record.getMessage() == "bad token access-production-[redacted]"
