"""
Story 5.3 (FR-29, FR-30): who writes to the Google Sheets, PC and AWS side
by side, from the Owner's PC after `aws login`:

    python tools/switches.py

    Writer  Syncs to the Sheets  Change it with
    PC      on                   config.py: sheets_sync_enabled = False
    AWS     off                  tools/put_parameters.py --sync-enabled true

The two switches are independent (AD-10): neither system reads or writes the
other's. Each is one documented change (docs/TECHNICAL.md, Runbook, "Who
writes to the Sheets"). It only reads; it changes neither.

Exit code 0 = both read, 1 = the AWS switch couldn't be read.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.aws_cli import AwsError, call  # noqa: E402

SWITCH = "/calls-log/sync_enabled"


def aws_switch(*, run=subprocess.run) -> bool:
    """Read as the runner reads it: only "true" is on (cloud/settings.py sync_enabled)."""
    out = call("ssm", "get-parameter", {"Name": SWITCH, "WithDecryption": True}, run=run)
    return out["Parameter"]["Value"].strip().lower() == "true"


def table(pc_on: bool, aws_on: Optional[bool]) -> List[str]:
    def word(on: Optional[bool]) -> str:
        return "unknown" if on is None else "on" if on else "off"

    rows = [("Writer", "Syncs to the Sheets", "Change it with"),
            ("PC", word(pc_on), f"config.py: sheets_sync_enabled = {not pc_on}"),
            ("AWS", word(aws_on), "tools/put_parameters.py --sync-enabled "
                                  + ("false" if aws_on else "true"))]
    lines = [f"{a:<8}{b:<21}{c}" for a, b, c in rows]
    if pc_on and aws_on:
        lines.append("Both write: process each Folder in one place only, or the Comparison Period's "
                     "rule applies (docs/TECHNICAL.md, Runbook).")
    elif aws_on is False and not pc_on:
        lines.append("Nobody writes to the Sheets: Day Sheets are still made on both sides.")
    return lines


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, pc_on: Optional[bool] = None) -> int:
    if pc_on is None:
        from config import CONFIG
        pc_on = bool(CONFIG.sheets_sync_enabled)
    try:
        aws_on: Optional[bool] = aws_switch(run=run)
    except AwsError as e:
        print(f"AWS switch unreadable: {e} (signed in? `aws login`)", file=sys.stderr)
        aws_on = None
    print("\n".join(table(pc_on, aws_on)))
    return 0 if aws_on is not None else 1


if __name__ == "__main__":
    sys.exit(main())
