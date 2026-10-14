"""
Push a processed day's sessions to Google Sheets via the Apps Script Web App.

This keeps the tool credential-free: instead of talking to the Google Sheets
API directly, it POSTs a small JSON payload to a deployed Apps Script Web App
(see apps_script/WebAppSync.gs), which creates the dated worksheet, writes the
sessions, and adds the month-sheet hyperlink.

Uses only the standard library so the project stays offline-first / dependency
-light; the network call is a no-op unless CONFIG.sheets_sync_enabled is True.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import List

from config import CONFIG
from session_builder import Session

log = logging.getLogger(__name__)


class SheetsSyncError(RuntimeError):
    """Raised when the Web App reports a failure or cannot be reached."""


def sync_day(name: str, date: str, sessions: List[Session]) -> dict:
    """
    Send one day's sessions for one person to the Web App.

    Returns the parsed JSON response on success. Returns {"skipped": True} if
    sync is disabled. Raises SheetsSyncError on any failure.
    """
    if not CONFIG.sheets_sync_enabled:
        return {"skipped": True, "reason": "sheets_sync_enabled is False"}
    if not CONFIG.sheets_webapp_url:
        raise SheetsSyncError("sheets_sync_enabled is True but sheets_webapp_url is empty")

    payload = {
        "secret": CONFIG.sheets_sync_secret,
        "name": name,
        "date": date,
        "sessions": [{"start": s.start_time, "end": s.end_time} for s in sessions],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CONFIG.sheets_webapp_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    # Retry transient network failures. A failed sync blocks the source folder
    # from being retired and marks the whole batch as failed, so it is worth a
    # couple of attempts. Note the exception net is OSError, not just URLError:
    # read timeouts surface as TimeoutError and dropped keep-alives as
    # ConnectionResetError, neither of which is a URLError.
    attempts = max(1, CONFIG.sheets_sync_retries)
    last_exc: Exception | None = None
    raw = ""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=CONFIG.sheets_sync_timeout) as resp:
                raw = resp.read().decode("utf-8")
            break
        except KeyboardInterrupt:
            raise
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc
            if attempt < attempts:
                delay = 2 ** (attempt - 1)
                log.warning(
                    "Sheets sync attempt %d/%d failed (%s); retrying in %ds",
                    attempt, attempts, exc, delay,
                )
                time.sleep(delay)
    else:
        raise SheetsSyncError(
            f"Web App request failed after {attempts} attempt(s): {last_exc}"
        ) from last_exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SheetsSyncError(f"Web App returned non-JSON response: {raw[:200]!r}") from exc

    if not result.get("ok"):
        raise SheetsSyncError(f"Web App error: {result.get('error', raw[:200])}")

    log.info(
        "Sheets sync OK: %s %s -> gid=%s, %d row(s), linked=%s, month_linked=%s%s",
        name, date, result.get("gid"), result.get("sessions_written", 0),
        result.get("linked"), result.get("month_linked"),
        " (CAPPED)" if result.get("capped") else "",
    )
    if result.get("month_linked") is False:
        log.warning(
            "%s %s: day tab B1 back-link NOT set (no month tab for %s in that spreadsheet)",
            name, date, date[:7],
        )
    return result
