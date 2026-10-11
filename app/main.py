"""Web app: JSON API + the built React frontend. Run with `python -m app`, then open http://localhost:8000.

For frontend development, also run `npm run dev` in frontend/ (http://localhost:5173, proxies to this server).
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import google_auth, scheduler
from app.api import router as api_router
from app.bills import service
from app.config import PROJECT_ROOT, settings
from app.db import init_db

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler.run_forever())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Cashflow Tracker", lifespan=lifespan)
app.include_router(api_router)


def _to_settings(**params: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?{urlencode(params)}", status_code=303)


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
    return FileResponse(path, media_type=attachment.mime_type, filename=attachment.filename,
                        content_disposition_type="inline")


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
