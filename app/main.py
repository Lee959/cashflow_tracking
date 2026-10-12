"""Web app: JSON API + the built React frontend. Run with `python -m app`, then open http://localhost:8000.

For frontend development, also run `npm run dev` in frontend/ (http://localhost:5173, proxies to this server).
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import auth, google_auth, scheduler, security
from app.api import router as api_router
from app.bills import service
from app.config import PROJECT_ROOT, settings
from app.db import init_db
from app.gmail_client import is_pdf_data

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    security.install_log_redaction()
    init_db()
    task = asyncio.create_task(scheduler.run_forever())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


# No /docs, /redoc or /openapi.json: there's no reason to publish a map of the API, even locally
app = FastAPI(title="Cashflow Tracker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
security.install(app)
app.include_router(api_router)


def _to_settings(**params: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?{urlencode(params)}", status_code=303)


@app.get("/auth/start")
def auth_start(t: str = ""):
    """The launch link opened by `python -m app`: signs this browser in (Touch ID unlock comes next)."""
    cookie = auth.start_session(t)
    if cookie is None:
        return RedirectResponse("/?login=expired", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(auth.COOKIE, cookie, max_age=int(auth.SESSION_LIFETIME.total_seconds()),
                        httponly=True, samesite="strict", path="/")
    return response


@app.get("/auth/status")
def auth_status(request: Request):
    return {"state": auth.state(request.cookies.get(auth.COOKIE)) if auth.ENFORCED else auth.UNLOCKED}


def _auth_call(fn, *args):
    try:
        return fn(*args)
    except auth.AuthError as exc:
        raise HTTPException(403, str(exc))


@app.post("/auth/passkey/register/options")
def passkey_register_options(request: Request):
    return Response(_auth_call(auth.registration_options, request.cookies.get(auth.COOKIE)),
                    media_type="application/json")


@app.post("/auth/passkey/register/verify")
def passkey_register_verify(request: Request, credential: dict = Body(...)):
    _auth_call(auth.verify_registration, request.cookies.get(auth.COOKIE), credential)
    return {"state": auth.UNLOCKED}


@app.post("/auth/passkey/unlock/options")
def passkey_unlock_options(request: Request):
    return Response(_auth_call(auth.authentication_options, request.cookies.get(auth.COOKIE)),
                    media_type="application/json")


@app.post("/auth/passkey/unlock/verify")
def passkey_unlock_verify(request: Request, credential: dict = Body(...)):
    _auth_call(auth.verify_authentication, request.cookies.get(auth.COOKIE), credential)
    return {"state": auth.UNLOCKED}


@app.post("/auth/lock")
def auth_lock(request: Request):
    auth.lock(request.cookies.get(auth.COOKIE))
    return {"state": auth.LOCKED}


@app.get("/auth/google/login")
def google_login():
    try:
        return RedirectResponse(google_auth.build_authorization_url())
    except google_auth.GmailNotConnected as exc:
        return _to_settings(error=str(exc))


@app.get("/auth/google/callback")
def google_callback(state: str = "", code: str = "", error: str = ""):
    if error:
        return _to_settings(error=f"Google sign-in was cancelled ({error})")
    try:
        email = google_auth.complete_authorization(state, code)
    except google_auth.GmailNotConnected as exc:
        return _to_settings(error=str(exc))
    return _to_settings(msg=f"Connected Gmail: {email}")


@app.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: int):
    attachment = service.get_attachment(attachment_id)
    if attachment is None:
        raise HTTPException(404)
    path = (settings.data_dir / attachment.path).resolve()
    if not path.is_relative_to(settings.data_dir.resolve()) or not path.exists():
        raise HTTPException(404)
    # Only real PDFs, always served as PDFs: a spoofed email's "bill.pdf" declared as text/html
    # must never render as a page on this app's origin.
    with path.open("rb") as f:
        if not is_pdf_data(f.read(1024)):
            raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf", filename=attachment.filename,
                        content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


# React app: static assets, and index.html for every other path (client-side routes like /settings)
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "auth/", "attachments/")):
        raise HTTPException(404)
    file = (FRONTEND_DIST / path).resolve()
    if path and file.is_file() and file.is_relative_to(FRONTEND_DIST.resolve()):
        return FileResponse(file)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        "<p>The frontend isn't built yet. Run <code>npm install && npm run build</code> in "
        "<code>frontend/</code>, or use <code>npm run dev</code> and open http://localhost:5173.</p>",
        status_code=503,
    )
