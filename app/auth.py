"""Login for a server that only runs on this Mac. Two steps:

1. Launch link. `python -m app` makes a new secret for each run and opens
   http://localhost:8000/auth/start?t=<secret>. Visiting it sets an HttpOnly, SameSite=Strict session
   cookie. Other programs on this Mac (including sandboxed apps and browser extensions) can reach
   localhost:8000 but don't know the secret, so they can't get a session.
2. Touch ID. The session is unlocked with a passkey (WebAuthn, this Mac's built-in authenticator,
   fingerprint or password required). It locks again after 30 minutes without use, or with Lock.

Until both are done, /api/* and /attachments/* answer 401.
"""

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

import webauthn
from sqlalchemy import delete, select
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.db import session_scope
from app.models import Passkey, WebSession

COOKIE = "hb_session"
SESSION_LIFETIME = timedelta(days=7)
IDLE_LOCK = timedelta(minutes=30)
RP_ID = "localhost"  # WebAuthn needs a hostname, so the app must be opened as localhost, not 127.0.0.1
RP_NAME = "Household Bills"
USER_ID = b"household-bills-owner"
ORIGINS = ["http://localhost:8000", "http://localhost:5173"]  # the app, and the Vite dev server

# The only switch that turns login off. The test suite sets it; nothing else should.
ENFORCED = os.getenv("APP_DISABLE_LOGIN_FOR_TESTS") != "1"

# Session states, in order: no session -> needs a passkey set up -> locked -> unlocked
NONE, NEEDS_SETUP, LOCKED, UNLOCKED = "none", "needs_setup", "locked", "unlocked"


class AuthError(Exception):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def start_session(launch_secret: str | None) -> str | None:
    """A new session cookie if `launch_secret` is this run's launch secret, else None."""
    expected = os.getenv("APP_LAUNCH_TOKEN")
    if not expected or not launch_secret or not hmac.compare_digest(expected, launch_secret):
        return None
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    with session_scope() as s:
        s.execute(delete(WebSession).where(WebSession.expires_at < now))  # tidy up old ones
        s.add(WebSession(token_hash=_hash(token), expires_at=now + SESSION_LIFETIME))
    return token


def state(cookie: str | None, now: datetime | None = None) -> str:
    now = now or datetime.now()
    with session_scope() as s:
        session = _session(s, cookie, now)
        if session is None:
            return NONE
        if s.scalar(select(Passkey.credential_id).limit(1)) is None:
            return NEEDS_SETUP
        idle = session.last_seen_at is None or now - session.last_seen_at > IDLE_LOCK
        return LOCKED if session.unlocked_at is None or idle else UNLOCKED


def touch(cookie: str | None) -> None:
    """Keeps an unlocked session unlocked (the idle timer restarts)."""
    with session_scope() as s:
        if session := _session(s, cookie, datetime.now()):
            session.last_seen_at = datetime.now()


def lock(cookie: str | None) -> None:
    with session_scope() as s:
        if session := _session(s, cookie, datetime.now()):
            session.unlocked_at = None


def registration_options(cookie: str | None) -> str:
    """WebAuthn options for adding a Touch ID passkey: allowed for the first passkey right after the
    launch link, or later from an unlocked session."""
    if state(cookie) not in (NEEDS_SETUP, UNLOCKED):
        raise AuthError("Unlock first.")
    with session_scope() as s:
        existing = [PublicKeyCredentialDescriptor(id=_unb64(p.credential_id)) for p in s.scalars(select(Passkey))]
        options = webauthn.generate_registration_options(
            rp_id=RP_ID,
            rp_name=RP_NAME,
            user_id=USER_ID,
            user_name="Household Bills",
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,  # this Mac's Touch ID
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=existing,
        )
        _session(s, cookie, datetime.now()).challenge = _b64(options.challenge)
    return webauthn.options_to_json(options)


def verify_registration(cookie: str | None, credential: dict) -> None:
    if state(cookie) not in (NEEDS_SETUP, UNLOCKED):
        raise AuthError("Unlock first.")
    challenge = _take_challenge(cookie)
    try:
        verified = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=RP_ID,
            expected_origin=ORIGINS,
            require_user_verification=True,
        )
    except Exception as exc:  # the library raises several error types for a bad or forged response
        raise AuthError(f"Touch ID setup didn't verify: {exc}") from exc
    with session_scope() as s:
        s.add(Passkey(credential_id=_b64(verified.credential_id), public_key=verified.credential_public_key,
                      sign_count=verified.sign_count))
        _unlock(_session(s, cookie, datetime.now()))


def authentication_options(cookie: str | None) -> str:
    if state(cookie) == NONE:
        raise AuthError("Open the app with its launch link first.")
    with session_scope() as s:
        allowed = [PublicKeyCredentialDescriptor(id=_unb64(p.credential_id)) for p in s.scalars(select(Passkey))]
        if not allowed:
            raise AuthError("Set up Touch ID first.")
        options = webauthn.generate_authentication_options(
            rp_id=RP_ID,
            allow_credentials=allowed,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        _session(s, cookie, datetime.now()).challenge = _b64(options.challenge)
    return webauthn.options_to_json(options)


def verify_authentication(cookie: str | None, credential: dict) -> None:
    if state(cookie) == NONE:
        raise AuthError("Open the app with its launch link first.")
    challenge = _take_challenge(cookie)
    with session_scope() as s:
        passkey = s.get(Passkey, str(credential.get("id", "")))
        if passkey is None:
            raise AuthError("Unknown passkey.")
        try:
            verified = webauthn.verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=RP_ID,
                expected_origin=ORIGINS,
                credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:
            raise AuthError(f"Touch ID didn't verify: {exc}") from exc
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = datetime.now()
        _unlock(_session(s, cookie, datetime.now()))


def reset() -> int:
    """Forgets every passkey and session (for a new Mac, or if Touch ID was reset). Returns passkeys removed."""
    with session_scope() as s:
        count = len(list(s.scalars(select(Passkey.credential_id))))
        s.execute(delete(Passkey))
        s.execute(delete(WebSession))
    return count


def _session(s, cookie: str | None, now: datetime) -> WebSession | None:
    if not cookie:
        return None
    session = s.get(WebSession, _hash(cookie))
    return session if session is not None and session.expires_at > now else None


def _take_challenge(cookie: str | None) -> bytes:
    """The pending challenge, usable once."""
    with session_scope() as s:
        session = _session(s, cookie, datetime.now())
        if session is None or not session.challenge:
            raise AuthError("Start again.")
        challenge, session.challenge = session.challenge, None
    return _unb64(challenge)


def _unlock(session: WebSession) -> None:
    session.unlocked_at = session.last_seen_at = datetime.now()
