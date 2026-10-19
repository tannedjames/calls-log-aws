"""
Ready -> Done times (Story 5.4, FR-5): the numbers tools/turnaround.py prints
and the upload page's Owner tab shows. Pure functions over Folder Status
items (cloud/status.py recent): counts by status, then the median, p95 and the
slowest wait from readyAt to the Done write, against FR-5's 2 hours.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Dict, List

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
