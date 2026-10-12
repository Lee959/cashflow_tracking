"""Protections for a server that only ever runs on this computer but holds Gmail and bill data.

- Only requests addressed to localhost are answered. Without this, another website could point
  its own domain at 127.0.0.1 ("DNS rebinding") and read your bills through your browser.
- API and attachment requests must come from this app's own pages. Browsers label every request
  with where it came from (Sec-Fetch-Site / Origin); requests from another website open in the
  same browser are refused, so it can't disconnect Gmail or trigger scans (CSRF).
- Every response gets security headers; app pages also get a Content-Security-Policy so only the
  app's own scripts can run and no other site can frame it. API responses are never cached.
- /api and /attachments need a signed-in, Touch ID-unlocked session (see app/auth.py), so other
  programs on this Mac can't read your data through localhost either.
- Log lines are scrubbed of Plaid tokens and the launch-link secret.
"""

import logging
import re

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from app import auth

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]  # any port: the Vite dev server forwards with Host localhost:5173
LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d+)?")
PROTECTED_PATHS = ("/api/", "/attachments/")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # Plaid Link (connecting a bank) must load its script and frame straight from Plaid
    "script-src 'self' https://cdn.plaid.com/link/v2/stable/link-initialize.js",
    "frame-src https://cdn.plaid.com/",
    "connect-src 'self' https://sandbox.plaid.com https://production.plaid.com",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def cross_site_reason(method: str, headers) -> str | None:
    """Why a request looks like it came from another website, or None if it's fine.

    Requests without these headers come from non-browser clients (curl, the CLI, tests), which
    another website can't drive, so they're allowed.
    """
    site = headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return f"request from another site (Sec-Fetch-Site: {site})"
    origin = headers.get("origin")
    if method in UNSAFE_METHODS and origin and not LOCAL_ORIGIN.fullmatch(origin):
        return f"request from {origin}"
    return None


class SameSiteOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # login actions (POST /auth/...) get the same check; Google's redirect back is a GET and must pass
        if path.startswith(PROTECTED_PATHS) or (path.startswith("/auth/") and request.method in UNSAFE_METHODS):
            if reason := cross_site_reason(request.method, request.headers):
                return PlainTextResponse(f"Blocked: {reason}", status_code=403)

        response = await call_next(request)
        if path.startswith(PROTECTED_PATHS + ("/auth/",)):
            response.headers.setdefault("Cache-Control", "no-store")  # no bank data in the browser's disk cache
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # PDFs open in the browser's own viewer, which a page CSP can break; they get their own headers
        if not request.url.path.startswith("/attachments/"):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    """Data and actions need an unlocked session; everything else (the page, /auth/*) doesn't."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if auth.ENFORCED and (path.startswith(PROTECTED_PATHS) or path == "/auth/google/login"):
            cookie = request.cookies.get(auth.COOKIE)
            current = await run_in_threadpool(auth.state, cookie)
            if current != auth.UNLOCKED:
                return JSONResponse({"detail": "locked", "state": current}, status_code=401)
            await run_in_threadpool(auth.touch, cookie)
        return await call_next(request)


# Plaid access/public/link tokens, and the ?t= secret of the launch link (uvicorn logs request URLs)
_SECRET_PATTERNS = [
    (re.compile(r"\b(access|public|link)-(sandbox|development|production)-[\w-]+"), r"\1-\2-[redacted]"),
    (re.compile(r"([?&]t=)[^\s&\"']+"), r"\1[redacted]"),
]


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def install_log_redaction() -> None:
    """Adds the filter to every handler of the app's and uvicorn's loggers (call after logging is set up)."""
    redact = RedactSecrets()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "app"):
        logger = logging.getLogger(name)
        logger.addFilter(redact)
        for handler in logger.handlers:
            handler.addFilter(redact)


def install(app: FastAPI) -> None:
    # added last = runs first: unknown hosts are rejected first, then other sites, then logged-out requests
    app.add_middleware(LoginRequiredMiddleware)
    app.add_middleware(SameSiteOnlyMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
