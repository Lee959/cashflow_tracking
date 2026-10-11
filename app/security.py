"""Protections for a server that only ever runs on this computer but holds Gmail and bill data.

- Only requests addressed to localhost are answered. Without this, another website could point
  its own domain at 127.0.0.1 ("DNS rebinding") and read your bills through your browser.
- API and attachment requests must come from this app's own pages. Browsers label every request
  with where it came from (Sec-Fetch-Site / Origin); requests from another website open in the
  same browser are refused, so it can't disconnect Gmail or trigger scans (CSRF).
- Every response gets security headers; app pages also get a Content-Security-Policy so only the
  app's own scripts can run and no other site can frame it.
"""

import re

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]  # any port: the Vite dev server forwards with Host localhost:5173
LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d+)?")
PROTECTED_PATHS = ("/api/", "/attachments/")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
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
        if request.url.path.startswith(PROTECTED_PATHS):
            if reason := cross_site_reason(request.method, request.headers):
                return PlainTextResponse(f"Blocked: {reason}", status_code=403)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # PDFs open in the browser's own viewer, which a page CSP can break; they get their own headers
        if not request.url.path.startswith("/attachments/"):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response


def install(app: FastAPI) -> None:
    # added last = runs first: unknown hosts are rejected before anything else happens
    app.add_middleware(SameSiteOnlyMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
