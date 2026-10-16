"""
Re-sync existing output xlsx files to Google Sheets.

Standalone backfill/repair tool: for every "<Name> & yyyy-mm-dd.xlsx" in the
output folder it reads the session rows back out of the workbook and POSTs
them to the Apps Script Web App (see sheets_sync.py / apps_script/), which
creates the yyyy-mm-dd tab from the "Mẫu" template if missing, (re)writes the
data, and sets the month-tab "Mở->" hyperlink. Existing tabs are overwritten
with the xlsx content — the xlsx files are treated as the source of truth.

Use this when days were OCR'd while sync was disabled/broken, or to repair
missing tabs/hyperlinks.

Usage:
    python resync_output.py                      # sync everything in ./output
    python resync_output.py --output other_dir   # different folder
    python resync_output.py --name "Nguyen Van A" # only this person (prefix ok)
    python resync_output.py --date 2026-06-04    # only this date
    python resync_output.py --dry-run            # show what would be sent

Duplicate files like "Name & 2026-06-04(1).xlsx" are skipped when the base
file for the same person/day exists; otherwise the lowest "(n)" copy is used.
File names are read by cloud/naming.py, the parser AWS uses too: an unpadded
date ("2026-6-4") counts; text after the date or an impossible date doesn't.
The sessions are read from the sheet excel_writer writes (CONFIG.sheet_name).

Requires: openpyxl. Network: CONFIG.sheets_webapp_url must be reachable and
CONFIG.sheets_sync_enabled is ignored here (this tool exists to sync).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl

from config import CONFIG
import sheets_sync
from sheets_sync import SheetsSyncError
from cloud import naming   # the one folder-name parser (AD-8), shared with AWS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("resync")

@dataclass
class _Sess:
    """Minimal stand-in for session_builder.Session (sync_day only reads these)."""
    start_time: str
    end_time: str


def _fmt_time(value) -> Optional[str]:
    """Normalize an openpyxl cell value (time, day-fraction float, or string) to 'HH:MM'."""
    if value is None:
        return None
    if hasattr(value, "hour"):  # datetime.time / datetime.datetime
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, (int, float)):
        total = int(round(float(value) * 1440)) % 1440
        return f"{total // 60:02d}:{total % 60:02d}"
    s = str(value).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def read_sessions(xlsx_path: Path) -> List[_Sess]:
    """Read start/end pairs from the session rows of an output workbook."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=False)
    # The sheet excel_writer.write_sessions writes to, not whichever is active.
    ws = wb[CONFIG.sheet_name] if CONFIG.sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
    sessions: List[_Sess] = []
    row = CONFIG.first_session_row
    hard_stop = CONFIG.first_session_row + 200  # safety bound for expanded sheets
    while row <= hard_stop:
        start = _fmt_time(ws.cell(row, 2).value)
        end = _fmt_time(ws.cell(row, 3).value)
        if start is None and end is None:
            # template rows end at last_session_row; past that, first gap = done
            if row > CONFIG.last_session_row:
                break
            row += 1
            continue
        if start is None or end is None:
            log.warning("%s row %d: incomplete pair (start=%r end=%r), skipped",
                        xlsx_path.name, row, start, end)
        else:
            sessions.append(_Sess(start, end))
        row += 1
    return sessions


def collect_files(output_dir: Path) -> Dict[Tuple[str, str], Path]:
    """Map (name, date) -> xlsx path, preferring the non-'(n)' file."""
    chosen: Dict[Tuple[str, str], Tuple[int, Path]] = {}
    for path in sorted(output_dir.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        parsed = naming.parse_output_stem(path.stem)
        if parsed is None:
            log.warning("Skipping unrecognized filename: %s", path.name)
            continue
        day, rank = parsed
        key = (day.name, day.date)
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, path)
    return {k: v[1] for k, v in chosen.items()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Re-sync output xlsx files to Google Sheets.")
    ap.add_argument("--output", default="output", help="Folder of '<Name> & yyyy-mm-dd.xlsx' files")
    ap.add_argument("--name", default=None, help="Only sync this person (name prefix, case-sensitive as in filename)")
    ap.add_argument("--date", default=None, help="Only sync this yyyy-mm-dd date")
    ap.add_argument("--dry-run", action="store_true", help="Parse and report, but do not POST")
    args = ap.parse_args(argv)

    output_dir = Path(args.output)
    if not output_dir.is_dir():
        ap.error(f"not a folder: {output_dir}")

    if not CONFIG.sheets_webapp_url:
        log.error("config.py: sheets_webapp_url is empty — nothing to sync to.")
        return 2
    # This tool's whole purpose is syncing; do it even if the OCR-time
    # auto-sync toggle is off.
    CONFIG.sheets_sync_enabled = True

    files = collect_files(output_dir)
    if args.name:
        files = {k: v for k, v in files.items() if k[0].startswith(args.name)}
    if args.date:
        files = {k: v for k, v in files.items() if k[1] == args.date}
    if not files:
        log.error("No matching xlsx files found.")
        return 1

    ok = failed = 0
    failures: List[str] = []
    for (name, date), path in sorted(files.items()):
        sessions = read_sessions(path)
        if not sessions:
            log.warning("%s: no sessions found, skipped", path.name)
            continue
        if args.dry_run:
            log.info("DRY RUN %s & %s: %d session(s) from %s", name, date, len(sessions), path.name)
            ok += 1
            continue
        try:
            result = sheets_sync.sync_day(name, date, sessions)
            log.info("Synced %s & %s: gid=%s rows=%s linked=%s%s",
                     name, date, result.get("gid"), result.get("sessions_written"),
                     result.get("linked"), " CAPPED" if result.get("capped") else "")
            if not result.get("linked"):
                log.warning("%s & %s: month-tab hyperlink NOT set (date row not found in month sheet)", name, date)
            ok += 1
        except SheetsSyncError as exc:
            log.error("FAILED %s & %s: %s", name, date, exc)
            failures.append(f"{name} & {date}")
            failed += 1

    log.info("Done: %d synced, %d failed, %d total file(s).", ok, failed, len(files))
    if failures:
        log.error("Failed days: %s", "; ".join(failures))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
