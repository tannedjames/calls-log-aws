"""
Story 5.4 (FR-5, SM-2): how fast AWS turns Ready Folders into Done ones,
from the Owner's PC after `aws login`. For the first week after cutover's
daily review, and the case study's measured turnaround (Story 6.2).

    python tools/turnaround.py              # uploads of the last 7 days
    python tools/turnaround.py --days 28

It reads the Folder Status index uploads-by-day (no scan) and prints counts
by status, and the Ready -> Done time (readyAt to the Done write, after the
Sync per AD-7): median, p95 and the slowest, against FR-5's 2 hours.
Counts and times only, never a name, so it can go in the runbook log.

Exit code 0 = read (p95 within 2 h, or nothing finished), 1 = AWS failed,
3 = p95 over 2 hours.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import math
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud.status import FolderStatus  # noqa: E402
from tools.aws_cli import AwsError, Client  # noqa: E402

TABLE = "calls-log-folders"
TARGET_S = 2 * 3600   # FR-5: p95 Ready -> Sync within 2 hours


def _at(text: str) -> _dt.datetime:
    return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def percentile(values: List[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with at least q of them at or below it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summary(items: List[Dict]) -> Dict:
    counts: Dict[str, int] = {}
    waits = []
    for item in items:
        counts[item.get("status") or "?"] = counts.get(item.get("status") or "?", 0) + 1
        if item.get("status") == "Done" and item.get("readyAt") and item.get("updatedAt"):
            waits.append((_at(item["updatedAt"]) - _at(item["readyAt"])).total_seconds())
    out: Dict = {"uploads": len(items), "counts": counts, "finished": len(waits)}
    if waits:
        out.update(median=percentile(waits, 0.5), p95=percentile(waits, 0.95), slowest=max(waits))
    return out


def _span(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def report(s: Dict, days: int) -> List[str]:
    lines = [f"Uploads in the last {days} day(s): {s['uploads']}  "
             + "  ".join(f"{k} {v}" for k, v in sorted(s["counts"].items()))]
    if not s["finished"]:
        lines.append("No Folder went from Ready to Done in that time.")
        return lines
    verdict = "within" if s["p95"] <= TARGET_S else "OVER"
    lines.append(f"Ready -> Done over {s['finished']} Folder(s): median {_span(s['median'])}, "
                 f"p95 {_span(s['p95'])} ({verdict} the 2 h target), slowest {_span(s['slowest'])}")
    return lines


def main(argv: Optional[List[str]] = None, *, run=subprocess.run,
         today: Optional[_dt.date] = None) -> int:
    ap = argparse.ArgumentParser(description="Ready -> Done times on AWS (calls-log, Story 5.4).")
    ap.add_argument("--days", type=int, default=7, help="how many UTC days of uploads (default 7)")
    args = ap.parse_args(argv)
    if not 1 <= args.days <= 90:
        print("--days must be 1 to 90", file=sys.stderr)
        return 2
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    status = FolderStatus(Client("dynamodb", run=run), TABLE)
    try:
        items = [i for back in range(args.days)
                 for i in status.recent((today - _dt.timedelta(days=back)).isoformat())]
    except AwsError as e:
        print(f"AWS failed: {e} (signed in? `aws login`)", file=sys.stderr)
        return 1
    s = summary(items)
    print("\n".join(report(s, args.days)))
    return 3 if s["finished"] and s["p95"] > TARGET_S else 0


if __name__ == "__main__":
    sys.exit(main())
