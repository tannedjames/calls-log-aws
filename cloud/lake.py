"""
The data lake's records (ADR 9, docs/aws/data-contract.md, schema_version 1).

When a Run is Done, the runner hands the day over as one JSON envelope in
staging/<Person-Day>/<uploadId>.json: its calls, its sessions and the upload.
The publisher (Phase 2) checks it with check() and writes Parquet to lake/.
This module builds and checks that envelope.

* No name, phone number, OCR text or file name enters it. The person is
  person_key, an HMAC of the employee's NAME_TO_ID key under the secret
  /calls-log/person_salt; an image is its position in the PC's order; a
  warning is cut down to a closed set of codes.
* The columns are the contract's. A column change bumps SCHEMA_VERSION and
  the contract together; a test compares the two.

Standard library, config and cloud.naming only: nothing new in the runner image.
"""

from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import hmac
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import CONFIG

from cloud import naming

SCHEMA_VERSION = 1
LOCAL_SALT = "local only, never a production salt"   # --local: keys that match nothing real
LOW_CONF_SHARE_LIMIT = 0.30   # a starting value; Phase 4.5 calibrates it

ENVELOPE_KEYS = ("schema_version", "person_key", "work_date", "upload_id", "staged_at",
                 "day_sheet", "calls", "sessions", "uploads")
# The staged columns; the publisher adds ingested_at to each row.
CALL_COLUMNS = ("person_key", "work_date", "upload_id", "image_key", "call_seq", "start_ts",
                "end_ts", "duration_s", "direction", "call_status", "countable", "row_conf",
                "start_conf", "duration_conf", "flagged", "flag_field", "flag_reason",
                "parser_version", "ocr_model")
SESSION_COLUMNS = ("person_key", "work_date", "upload_id", "session_no", "start_ts", "end_ts",
                   "span_min", "call_count", "source")
UPLOAD_COLUMNS = ("upload_id", "person_key", "work_date", "image_count", "attempts", "ready_at",
                  "started_at", "finished_at", "status", "runner_memory_mb")

# The lake's columns and their Athena types, in file order: the publisher's
# Parquet schema and the Glue tables (infra/lake.tf) both follow it, and tests
# compare it with the contract. work_month is the partition, in the path only.
_TYPES = {
    "person_key": "string", "work_date": "date", "upload_id": "string", "image_key": "string",
    "call_seq": "int", "start_ts": "timestamp", "end_ts": "timestamp", "duration_s": "int",
    "direction": "string", "call_status": "string", "countable": "boolean", "row_conf": "double",
    "start_conf": "double", "duration_conf": "double", "flagged": "boolean", "flag_field": "string",
    "flag_reason": "string", "parser_version": "string", "ocr_model": "string",
    "session_no": "int", "span_min": "int", "call_count": "int", "source": "string",
    "image_count": "int", "attempts": "int", "ready_at": "timestamp", "started_at": "timestamp",
    "finished_at": "timestamp", "status": "string", "runner_memory_mb": "int",
    "ingested_at": "timestamp",
}
COLUMN_TYPES = {
    "calls": {c: _TYPES[c] for c in CALL_COLUMNS + ("ingested_at",)},
    "sessions": {c: _TYPES[c] for c in SESSION_COLUMNS + ("ingested_at",)},
    "uploads": {c: _TYPES[c] for c in UPLOAD_COLUMNS},
}

# Warning code -> the field it is about. parser.py and session_builder.py
# write these; anything else becomes "other".
FLAG_FIELDS = {
    "start_time_not_found": "start",
    "multiple_time_candidates": "start",
    "ampm_missing_shifted_to_pm": "start",
    "ampm_missing_assumed_24h": "start",
    "hour_out_of_range": "start",
    "unknown_ampm_marker": "start",
    "duration_not_found": "duration",
    "duration_format_ambiguous_assumed_mm_ss": "duration",
    "duration_missing_assumed_zero": "duration",
    "duration_missing_assumed_zero_for_outgoing": "duration",
    "direction_conflicting_keywords": "direction",
    "direction_unknown_for_missed_or_zero": "direction",
    "direction_unknown_assumed_countable": "direction",
    "direction_and_status_unknown": "direction",
    "recovered_without_call_marker": "direction",
    "call_appears_to_cross_midnight": "row",
    "possible_fuzzy_duplicate": "row",
    "low_ocr_confidence": "row",
    "status_bar_row_ignored": "row",
    "promo_row_ignored": "row",
    "status_bar_region_row_ignored": "row",
}
# Warnings that carry a value after their code (a file name, a time, a number).
_WITH_VALUE = {"low_ocr_confidence_": "low_ocr_confidence",
               "possible_fuzzy_duplicate_with_": "possible_fuzzy_duplicate"}
# What parse_row itself treats as informational, not a reason to review.
_INFORMATIONAL = {"low_ocr_confidence", "duration_missing_assumed_zero",
                  "duration_missing_assumed_zero_for_outgoing"}
# flag_reason values: the codes, and three the emitter adds.
FLAG_REASONS = tuple(FLAG_FIELDS) + ("low_field_confidence", "not_countable", "other")

_DIRECTIONS = {"incoming", "outgoing", "unknown"}
_CALL_STATUSES = {"answered", "missed", "unanswered", "zero_duration", "unknown"}
_PERSON_KEY = re.compile(r"[0-9a-f]{16}")


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def person_key(name: str, salt: str, prefixes: Iterable[str] = ()) -> str:
    """
    The lake's stand-in for a name: HMAC-SHA256 under the salt of the
    NAME_TO_ID key the Web App would pick for the name (else the folded
    name), first 16 hex digits. Spellings of one employee give one key.
    """
    if not salt:
        raise ValueError("person_salt is unset")
    identity = naming.norm(naming.matching_prefix(name, prefixes) or name)
    return hmac.new(salt.encode("utf-8"), identity.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def flag_code(warning: str) -> str:
    """A warning cut down to its code, so no file name or value reaches the lake."""
    if warning in FLAG_FIELDS:
        return warning
    for prefix, code in _WITH_VALUE.items():
        if warning.startswith(prefix):
            return code
    return "other"


@functools.lru_cache(maxsize=None)
def parser_version() -> str:
    """sha256 of the rule files, line endings folded, so the PC and the image agree."""
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for name in ("parser.py", "session_builder.py", "config.py"):
        digest.update((root / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


def utc_now() -> str:
    """The time now, as Folder Status writes times (UTC, to the second)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ocr_model() -> str:
    return (f"{CONFIG.ocr_engine}:{CONFIG.paddle_det_model_name or 'default'}"
            f"/{CONFIG.paddle_rec_model_name or 'default'}")


def _ts(date: str, hhmm: Optional[str], after: Optional[str] = None) -> Optional[str]:
    """Local wall-clock time on the work date, or the next day when it is before `after`."""
    if not hhmm:
        return None
    day = _dt.date.fromisoformat(date)
    if after and hhmm < after:
        day += _dt.timedelta(days=1)
    return f"{day.isoformat()}T{hhmm}:00"


def _minutes(start_ts: str, end_ts: str) -> int:
    return int((_dt.datetime.fromisoformat(end_ts) - _dt.datetime.fromisoformat(start_ts)).total_seconds() // 60)


def _conf(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def _flag(rec: Any) -> Tuple[bool, Optional[str], Optional[str]]:
    """(flagged, flag_field, flag_reason): the parser's own reason first, then low confidence."""
    threshold = CONFIG.ocr_confidence_threshold
    low = {f: c for f, c in (("start", rec.start_conf), ("duration", rec.duration_conf))
           if c is not None and c < threshold}
    row_low = rec.ocr_confidence < threshold
    if not (rec.needs_review or low or row_low):
        return False, None, None
    for warning in rec.warnings:
        code = flag_code(warning)
        if code not in _INFORMATIONAL:
            return True, FLAG_FIELDS.get(code, "row"), code
    if low:
        return True, next(iter(low)), "low_field_confidence"
    if row_low:
        return True, "row", "low_ocr_confidence"
    if not rec.countable:
        return True, "row", "not_countable"
    return True, "row", "other"


def day_sheet(pairs: Sequence[Tuple[str, str]]) -> Dict[str, int]:
    """Sessions and total span (B2) of a Day Sheet read back as (start, end) pairs."""
    total = 0
    for start, end in pairs:
        a, b = (int(t[:2]) * 60 + int(t[3:5]) for t in (start, end))
        total += b - a
    return {"sessions": len(pairs), "span_min": total}


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------

def payload(day: naming.PersonDay, upload_id: str, *, image_names: Sequence[str], records: Sequence[Any],
            sessions: Sequence[Any], sheet: Dict[str, int], upload: Dict[str, Any], salt: str,
            prefixes: Iterable[str] = (), staged_at: str) -> Dict[str, Any]:
    """
    One Done upload as the staged envelope. `records` are the deduped
    CallRecords, in the order dedupe kept them; those without a start time
    aren't calls and are left out. `sheet` is day_sheet() of the written Day
    Sheet; `upload` holds the uploads row's own columns.
    """
    key = person_key(day.name, salt, prefixes)
    ids = {"person_key": key, "work_date": day.date, "upload_id": upload_id}
    image_key = {name: f"{i:02d}" for i, name in enumerate(image_names, 1)}
    calls = []
    for rec in (r for r in records if r.start_time):
        flagged, flag_field, flag_reason = _flag(rec)
        calls.append({
            **ids,
            "image_key": image_key[rec.source_file],
            "call_seq": len(calls) + 1,
            "start_ts": _ts(day.date, rec.start_time),
            "end_ts": _ts(day.date, rec.end_time, after=rec.start_time),
            "duration_s": rec.raw_duration_seconds,
            "direction": rec.direction,
            "call_status": rec.status,
            "countable": bool(rec.countable),
            "row_conf": _conf(rec.ocr_confidence),
            "start_conf": _conf(rec.start_conf),
            "duration_conf": _conf(rec.duration_conf),
            "flagged": flagged,
            "flag_field": flag_field,
            "flag_reason": flag_reason,
            "parser_version": parser_version(),
            "ocr_model": ocr_model(),
        })
    rows = [{
        **ids,
        "session_no": s.session_index,
        "start_ts": _ts(day.date, s.start_time),
        "end_ts": _ts(day.date, s.end_time, after=s.start_time),
        "span_min": s.span_minutes,
        "call_count": s.call_count,
        "source": "runner",
    } for s in sessions]
    uploads = [{"upload_id": upload_id, "person_key": key, "work_date": day.date,
                **{c: upload.get(c) for c in UPLOAD_COLUMNS if c not in ids}}]
    return {"schema_version": SCHEMA_VERSION, **ids, "staged_at": staged_at, "day_sheet": dict(sheet),
            "calls": calls, "sessions": rows, "uploads": uploads}


# ---------------------------------------------------------------------------
# Quality checks: Athena takes any row, so the publisher only writes an
# envelope this finds nothing wrong with. Problems name rows by position,
# never by content.
# ---------------------------------------------------------------------------

def check(p: Dict[str, Any]) -> List[str]:
    """What is wrong with a staged envelope; empty when it may enter the lake."""
    try:
        return _check(p)
    except (AttributeError, KeyError, TypeError, ValueError) as e:   # any JSON may arrive
        return [f"malformed envelope ({type(e).__name__})"]


def _check(p: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if tuple(p) != ENVELOPE_KEYS:
        return [f"envelope keys {sorted(p)} are not the contract's"]
    if p["schema_version"] != SCHEMA_VERSION:
        return [f"schema_version {p['schema_version']!r}, expected {SCHEMA_VERSION}"]
    ids = {k: p[k] for k in ("person_key", "work_date", "upload_id")}
    if not _PERSON_KEY.fullmatch(str(ids["person_key"])):
        problems.append("person_key is not 16 hex digits")
    if not naming.is_upload_id(str(ids["upload_id"])):
        problems.append("upload_id is not an uploadId")
    try:
        _dt.date.fromisoformat(str(ids["work_date"]))
    except ValueError:
        problems.append("work_date is not a date")

    for table, columns in (("calls", CALL_COLUMNS), ("sessions", SESSION_COLUMNS), ("uploads", UPLOAD_COLUMNS)):
        for i, row in enumerate(p[table]):
            if tuple(row) != columns:
                problems.append(f"{table}[{i}]: columns are not the contract's")
            elif any(row[k] != v for k, v in ids.items()):
                problems.append(f"{table}[{i}]: person_key, work_date or upload_id differs from the envelope")
    if problems:
        return problems

    calls, sessions, uploads = p["calls"], p["sessions"], p["uploads"]
    for i, c in enumerate(calls):
        where = f"calls[{i}]"
        if c["call_seq"] != i + 1:
            problems.append(f"{where}: call_seq is not {i + 1}")
        if c["direction"] not in _DIRECTIONS or c["call_status"] not in _CALL_STATUSES:
            problems.append(f"{where}: unknown direction or call_status")
        if not re.fullmatch(r"\d{2}", str(c["image_key"])):
            problems.append(f"{where}: image_key is not a position")
        for f in ("row_conf", "start_conf", "duration_conf"):
            if c[f] is not None and not 0.0 <= c[f] <= 1.0:
                problems.append(f"{where}: {f} outside 0..1")
        if c["flagged"] != (c["flag_reason"] is not None) or (c["flag_reason"] is not None and (
                c["flag_reason"] not in FLAG_REASONS or c["flag_field"] not in ("start", "duration", "direction", "row"))):
            problems.append(f"{where}: flag columns don't agree")
        if c["end_ts"] is None:
            if c["countable"]:
                problems.append(f"{where}: a countable call without end_ts")
            continue
        if c["duration_s"] is None:
            problems.append(f"{where}: end_ts without duration_s")
            continue
        # <=, not <: a 0-second call ends the minute it starts.
        if c["end_ts"] < c["start_ts"]:
            problems.append(f"{where}: end_ts before start_ts")
        elif abs(_minutes(c["start_ts"], c["end_ts"]) - c["duration_s"] / 60) > 1:
            problems.append(f"{where}: end_ts - start_ts disagrees with duration_s")

    for i, s in enumerate(sessions):
        where = f"sessions[{i}]"
        if s["session_no"] != i + 1:
            problems.append(f"{where}: session_no is not {i + 1}")
        if s["call_count"] < 1:
            problems.append(f"{where}: no calls")
        if s["end_ts"] < s["start_ts"]:
            problems.append(f"{where}: end_ts before start_ts")
        # A session over midnight fails here on purpose: its Day Sheet span is negative.
        elif s["span_min"] != _minutes(s["start_ts"], s["end_ts"]):
            problems.append(f"{where}: span_min disagrees with end_ts - start_ts")
        if i and sessions[i - 1]["end_ts"] > s["start_ts"]:
            problems.append(f"{where}: overlaps the session before it")
    if not sessions:
        problems.append("no sessions: a Done upload always has one")

    # Every countable call is in exactly one session (drop_standalone_zero_sessions off).
    countable = sum(1 for c in calls if c["countable"] and c["end_ts"] is not None)
    if sum(s["call_count"] for s in sessions) != countable:
        problems.append("the sessions' call_count total differs from the countable calls")
    sheet = p["day_sheet"]
    if sheet.get("sessions") != len(sessions) or sheet.get("span_min") != sum(s["span_min"] for s in sessions):
        problems.append("sessions differ from the Day Sheet (row count or B2 total)")
    low = sum(1 for c in calls if c["row_conf"] < CONFIG.ocr_confidence_threshold)
    if calls and low / len(calls) > LOW_CONF_SHARE_LIMIT:
        problems.append(f"{low} of {len(calls)} calls are below the confidence threshold")

    if len(uploads) != 1:
        problems.append("uploads must hold exactly one row")
    elif uploads[0]["status"] != "Done" or not (uploads[0]["image_count"] or 0) >= 1 \
            or not (uploads[0]["attempts"] or 0) >= 1:
        problems.append("uploads[0]: not a Done upload with images and an attempt")
    return problems
