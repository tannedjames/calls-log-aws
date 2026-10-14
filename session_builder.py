"""
Build calling sessions from parsed CallRecords.

Pipeline:
    1. Drop records that are not countable.
    2. Remove exact duplicates (same start_time + same rounded duration +
       same direction). Keep first; record duplicate sources for the log.
    3. Flag (but don't drop) fuzzy duplicates (start times within 1 minute).
    4. Sort countable records by start_time.
    5. Group into sessions:
         - same session if gap <= normal_gap_minutes (5)
         - else if next call starts >= 09:00 and gap <= 10 and exceptions
           remaining (up to 4 for the day), merge and consume one exception
         - else, start a new session
    6. Handle overlaps by merging and flagging.

Gap is computed with the previous call's ROUNDED end time per the spec.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from config import CONFIG
from parser import CallRecord

log = logging.getLogger(__name__)

DUMMY_DATE = datetime(2000, 1, 1)


@dataclass
class Session:
    session_index: int
    start_time: str        # "HH:MM"
    end_time: str          # "HH:MM"
    span_minutes: int
    call_count: int
    exceptions_used_inside_session: int = 0
    warnings: List[str] = field(default_factory=list)
    member_sources: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_index": self.session_index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "span_minutes": self.span_minutes,
            "call_count": self.call_count,
            "exceptions_used_inside_session": self.exceptions_used_inside_session,
            "warnings": self.warnings,
            "member_sources": self.member_sources,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_dt(time_str: str) -> datetime:
    h, m = time_str.split(":")
    return DUMMY_DATE.replace(hour=int(h), minute=int(m))


def _minutes_between(a: str, b: str) -> int:
    """Return minutes from time a to time b (b - a). Negative if b < a."""
    return int((_to_dt(b) - _to_dt(a)).total_seconds() // 60)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def dedupe(records: List[CallRecord]) -> Tuple[List[CallRecord], List[dict]]:
    """
    Remove exact duplicates. Returns (kept, duplicates_info).

    Exact = same start_time, same rounded_duration_minutes, same direction,
            same countable flag. (Spec also mentions same status/source type;
            we use direction + status here.)

    Fuzzy duplicates (start within 1 minute, same rounded duration) are kept
    but warned.
    """
    seen: Dict[tuple, CallRecord] = {}
    duplicates_info: List[dict] = []

    for rec in records:
        key = (
            rec.start_time,
            rec.rounded_duration_minutes,
            rec.direction,
            rec.status,
            rec.countable,
        )
        if key in seen:
            kept = seen[key]
            duplicates_info.append({
                "kept_from": kept.source_file,
                "duplicate_from": rec.source_file,
                "start_time": rec.start_time,
                "rounded_duration_minutes": rec.rounded_duration_minutes,
            })
        else:
            seen[key] = rec

    kept_records = list(seen.values())

    # Flag fuzzy duplicates
    by_dur = defaultdict(list)
    for rec in kept_records:
        if rec.rounded_duration_minutes is not None:
            by_dur[rec.rounded_duration_minutes].append(rec)

    for dur, group in by_dur.items():
        group_sorted = sorted(group, key=lambda r: r.start_time or "")
        for i in range(len(group_sorted) - 1):
            a, b = group_sorted[i], group_sorted[i + 1]
            if not (a.start_time and b.start_time):
                continue
            gap = abs(_minutes_between(a.start_time, b.start_time))
            if 0 < gap <= 1:
                msg = f"possible_fuzzy_duplicate_with_{b.source_file}@{b.start_time}"
                if msg not in a.warnings:
                    a.warnings.append(msg)
                    a.needs_review = True

    return kept_records, duplicates_info


# ---------------------------------------------------------------------------
# Session building
# ---------------------------------------------------------------------------

def build_sessions(records: List[CallRecord]) -> Tuple[List[Session], dict]:
    """
    Group countable records into sessions.
    Returns (sessions, stats) where stats has exception/overlap counts.
    """
    countable = [r for r in records if r.countable and r.start_time and r.end_time]
    countable.sort(key=lambda r: r.start_time)

    sessions: List[Session] = []
    exceptions_used_total = 0
    overlaps_total = 0

    if not countable:
        return [], {"exceptions_used_total": 0, "overlaps_total": 0, "countable_calls": 0}

    # Bootstrap first session with first call
    cur = _new_session_from(countable[0], index=1)

    for prev, nxt in zip(countable, countable[1:]):
        cur_end = cur.end_time
        nxt_start = nxt.start_time
        nxt_end = nxt.end_time

        gap = _minutes_between(cur_end, nxt_start)

        if gap < 0:
            # Overlap: merge into current session, extend end if later, flag.
            overlaps_total += 1
            cur.warnings.append(
                f"overlap_with_{nxt.source_file}@{nxt_start}-{nxt_end}"
            )
            if nxt_end > cur.end_time:
                cur.end_time = nxt_end
            cur.call_count += 1
            cur.member_sources.append(nxt.source_file)
            continue

        if gap <= CONFIG.normal_gap_minutes:
            # Normal merge
            if nxt_end > cur.end_time:
                cur.end_time = nxt_end
            cur.call_count += 1
            cur.member_sources.append(nxt.source_file)
            continue

        # Gap > 5 minutes. Consider post-9 AM exception.
        eligible_exception = (
            nxt_start >= CONFIG.post_9am_start
            and gap <= CONFIG.post_9am_exception_gap_minutes
            and exceptions_used_total < CONFIG.post_9am_exception_limit
        )
        if eligible_exception:
            exceptions_used_total += 1
            cur.exceptions_used_inside_session += 1
            if nxt_end > cur.end_time:
                cur.end_time = nxt_end
            cur.call_count += 1
            cur.member_sources.append(nxt.source_file)
            continue

        # Finalize current and start a new one
        cur.span_minutes = _minutes_between(cur.start_time, cur.end_time)
        sessions.append(cur)
        cur = _new_session_from(nxt, index=len(sessions) + 1)

    # Finalize the last session
    cur.span_minutes = _minutes_between(cur.start_time, cur.end_time)
    sessions.append(cur)

    # Drop standalone zero-length sessions (e.g. "22:56 -> 22:56"). A 0-second
    # call that merges into a longer session still counts -- it just doesn't
    # extend the span -- but on its own it contributes nothing to the daily
    # total and shows up as a spurious row.
    dropped_zero = 0
    if CONFIG.drop_standalone_zero_sessions:
        kept: List[Session] = []
        for s in sessions:
            if s.span_minutes <= 0:
                dropped_zero += 1
                log.info(
                    "Dropping zero-length session at %s (%d call(s))",
                    s.start_time, s.call_count,
                )
                continue
            kept.append(s)
        sessions = kept
        # Renumber so column A stays 1..N with no gaps.
        for i, s in enumerate(sessions, 1):
            s.session_index = i

    stats = {
        "exceptions_used_total": exceptions_used_total,
        "overlaps_total": overlaps_total,
        "countable_calls": len(countable),
        "zero_length_sessions_dropped": dropped_zero,
    }
    return sessions, stats


def _new_session_from(rec: CallRecord, index: int) -> Session:
    span = _minutes_between(rec.start_time, rec.end_time)
    return Session(
        session_index=index,
        start_time=rec.start_time,
        end_time=rec.end_time,
        span_minutes=span,
        call_count=1,
        member_sources=[rec.source_file],
    )
