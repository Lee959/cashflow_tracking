"""Automatic bill fetching: runs every few hours while the app is running.

The Settings page's "Fetch now" and the CLI go through `run_sync` too, so only one
fetch runs at a time and every run is recorded in the sync_runs table.
"""

import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta

from googleapiclient.errors import HttpError
from sqlalchemy import select

from app import google_auth
from app.bills import service
from app.bills.senders import BillerConfigError
from app.db import session_scope
from app.gmail_client import friendly_error
from app.models import SyncRun

INTERVAL = timedelta(hours=float(os.getenv("APP_SYNC_INTERVAL_HOURS", "6")))
CHECK_EVERY_SECONDS = 600
STARTUP_DELAY_SECONDS = 15

log = logging.getLogger(__name__)
_lock = threading.Lock()


class SyncInProgress(Exception):
    pass


def default_months() -> list[str]:
    """This month and next: next month's bills usually arrive during this one."""
    month = service.current_month()
    return [month, service.shift_month(month, 1)]


def run_sync(trigger: str, months: list[str] | None = None, force: bool = False) -> SyncRun:
    if not _lock.acquire(blocking=False):
        raise SyncInProgress("A fetch is already running. Try again in a minute.")
    try:
        with session_scope() as s:
            run = SyncRun(trigger=trigger)
            s.add(run)
        months = months or default_months()
        try:
            results = [service.sync_month(m, force=force) for m in months]
            run.ok = True
            run.summary = _summarize(months, results)
            try:
                service.refresh_payment_methods()
            except Exception:  # payment methods are a nice-to-have; never fail the fetch over them
                log.exception("Checking payment-setup emails failed")
        except (google_auth.GmailNotConnected, BillerConfigError) as exc:
            run.summary = str(exc)
        except HttpError as exc:
            run.summary = friendly_error(exc)
        except Exception as exc:  # recorded so it shows up in Settings instead of vanishing in a log
            log.exception("Bill fetch failed")
            run.summary = f"Unexpected error: {exc}"
        run.finished_at = datetime.now()
        with session_scope() as s:
            s.merge(run)
        return run
    finally:
        _lock.release()


def is_running() -> bool:
    return _lock.locked()


def last_run() -> SyncRun | None:
    runs = recent_runs(1)
    return runs[0] if runs else None


def recent_runs(limit: int = 10) -> list[SyncRun]:
    with session_scope() as s:
        return list(s.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)))


def next_run_at() -> datetime | None:
    last = last_run()
    return last.started_at + INTERVAL if last else None


def is_due(now: datetime | None = None) -> bool:
    last = last_run()
    return last is None or (now or datetime.now()) - last.started_at >= INTERVAL


async def run_forever() -> None:
    """Started with the web app. Checks every 10 minutes whether a fetch is due, so app
    restarts don't trigger extra fetches."""
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            if google_auth.connected_account() and is_due():
                await asyncio.to_thread(run_sync, "auto")
        except SyncInProgress:
            pass
        except Exception:
            log.exception("Automatic bill fetch crashed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


def _summarize(months: list[str], results: list[service.SyncResult]) -> str:
    new = sum(r.new for r in results)
    if not new:
        return f"No new bills ({', '.join(months)})"
    parts = [f"{sum(r.parsed for r in results)} with amount"]
    if statements := sum(r.statements for r in results):
        parts.append(f"{statements} statement notices")
    if review := sum(r.needs_review for r in results):
        parts.append(f"{review} need review")
    return f"{new} new bill email(s): {', '.join(parts)} ({', '.join(months)})"
