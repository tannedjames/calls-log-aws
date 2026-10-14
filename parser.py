"""
Parse OCR text rows into structured call records.

Each row produces a CallRecord with start_time, raw_duration_seconds,
rounded_duration_minutes, end_time, direction, status, countable flag,
confidence, and warnings.

The spec requires:
- 24-hour output (convert AM/PM input)
- Duration rounding: >=20 sec rounds up; <20 sec rounds down
- Direction-aware filtering (outgoing 0-sec counts; incoming missed excludes)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from config import CONFIG

log = logging.getLogger(__name__)

DUMMY_DATE = datetime(2000, 1, 1)


# ---------------------------------------------------------------------------
# Record dataclass
# ---------------------------------------------------------------------------

@dataclass
class CallRecord:
    source_file: str
    raw_text: str
    start_time: Optional[str] = None          # "HH:MM"
    raw_duration_seconds: Optional[int] = None
    rounded_duration_minutes: Optional[int] = None
    end_time: Optional[str] = None            # "HH:MM"
    direction: str = "unknown"                # incoming | outgoing | unknown
    status: str = "unknown"                   # answered | missed | unanswered | zero_duration | unknown
    countable: bool = False
    ocr_confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    needs_review: bool = False

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "raw_text": self.raw_text,
            "start_time": self.start_time,
            "raw_duration_seconds": self.raw_duration_seconds,
            "rounded_duration_minutes": self.rounded_duration_minutes,
            "end_time": self.end_time,
            "direction": self.direction,
            "status": self.status,
            "countable": self.countable,
            "ocr_confidence": round(self.ocr_confidence, 4),
            "warnings": self.warnings,
            "needs_review": self.needs_review,
        }


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

# 12-hour with AM/PM (English) - e.g. "8:00 AM", "12:05PM", "03:00PM発信"
#
# Do NOT use \b at either end. PaddleOCR emits these rows without spaces --
# "03:00PM発信(32秒)" -- and Python's re treats CJK as word characters, so
# there is no word boundary between "M" and "発". The trailing \b therefore
# failed, the engine backtracked through every shorter alternative (each also
# ending against a word char), and NO time was found at all. Every call row on
# a Japanese screenshot was silently discarded with "start_time_not_found"
# while its duration parsed fine.
#
# Lookarounds on digits/colons instead: they anchor the number without caring
# what script surrounds it.
RE_TIME_12 = re.compile(
    r"(?<![\d:.])(?P<h>\d{1,2})\s*[:.]\s*(?P<m>\d{2})"
    r"(?:\s*(?P<ampm>[AaPp][Mm]?\.?))?"
    r"(?![\d:])"
)
# 24-hour HH:MM - e.g. "08:00", "21:34"
RE_TIME_24 = re.compile(r"(?<![\d:.])(?P<h>\d{1,2})\s*[:.]\s*(?P<m>\d{2})(?![\d:])")
# Japanese: 午前 (AM) / 午後 (PM)
RE_JP_AMPM = re.compile(r"(午前|午後)")
# Vietnamese: SA = sáng (AM), CH = chiều (PM)
RE_VI_AMPM = re.compile(r"\b(SA|CH|sa|ch)\b")


def parse_start_time(text: str) -> Tuple[Optional[str], List[str]]:
    """Extract start time and return ("HH:MM", warnings)."""
    value, warnings, _span = parse_start_time_ex(text)
    return value, warnings


def parse_start_time_ex(
    text: str,
) -> Tuple[Optional[str], List[str], Optional[Tuple[int, int]]]:
    """
    Extract start time and return ("HH:MM", warnings, span_of_match).

    The span matters: `parse_duration` must delete exactly the characters the
    start time was read from. Reconstructing that substring from the parsed
    value used to fail on zero-padded 12-hour rows -- for "03:08 PM 発信" the
    value is 15:08, none of the reconstructed patterns matched "03:08", the
    text survived into the duration parser, and RE_DUR_COLON read it as
    3 min 8 sec. Every such row became a phantom 3-minute call.

    Strategy: find ALL time-like tokens. Pick the most plausible one.
    Multiple candidates -> warn (ambiguous).
    """
    warnings: List[str] = []

    # Find every plausible HH:MM token (with or without AM/PM)
    # h, m, ampm, start_pos, end_pos
    candidates: List[Tuple[int, int, Optional[str], int, int]] = []
    for m in RE_TIME_12.finditer(text):
        h = int(m.group("h"))
        mm = int(m.group("m"))
        ampm = m.group("ampm")
        if ampm:
            ampm = ampm.upper().rstrip(".")
        if 0 <= h <= 23 and 0 <= mm <= 59:
            # Filter out things that look like durations: "1:25" right next to "min"
            # We'll let the duration parser handle "MM:SS" cases by looking at context.
            candidates.append((h, mm, ampm, m.start(), m.end()))

    if not candidates:
        return None, ["start_time_not_found"], None

    # Detect JP/VI AM/PM markers in the surrounding text
    jp = RE_JP_AMPM.search(text)
    vi = RE_VI_AMPM.search(text)
    jp_ampm = None
    if jp:
        jp_ampm = "AM" if jp.group(1) == "午前" else "PM"
    vi_ampm = None
    if vi:
        vi_ampm = "AM" if vi.group(1).upper() == "SA" else "PM"

    # Prefer candidates with an explicit AM/PM marker
    with_ampm = [c for c in candidates if c[2] is not None]
    chosen = with_ampm[0] if with_ampm else candidates[0]

    if len(candidates) > 1:
        warnings.append("multiple_time_candidates")

    h, mm, ampm, span_start, span_end = chosen
    ampm = ampm or jp_ampm or vi_ampm

    if ampm:
        h = _to_24h(h, ampm, warnings)
    else:
        if h == 24:
            h = 0
        # No AM/PM marker. If hour > 12 it is already unambiguous 24-hour.
        if h <= 12:
            if 0 < CONFIG.assume_pm_before_hour and h < CONFIG.assume_pm_before_hour:
                # Lost PM marker: "02:23 PM" read as "02:23". Nobody calls
                # between midnight and 07:00, so this is a 12-hour shift.
                h += 12
                warnings.append("ampm_missing_shifted_to_pm")
            else:
                warnings.append("ampm_missing_assumed_24h")

    if not (0 <= h <= 23):
        warnings.append("hour_out_of_range")
        return None, warnings, None

    return f"{h:02d}:{mm:02d}", warnings, (span_start, span_end)


def _to_24h(h: int, ampm: str, warnings: List[str]) -> int:
    ampm = ampm.upper().rstrip(".")
    if ampm not in ("AM", "PM", "A", "P"):
        warnings.append("unknown_ampm_marker")
        return h
    is_pm = ampm.startswith("P")
    if h == 12:
        return 12 if is_pm else 0
    if is_pm:
        return h + 12
    return h


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

# Common OCR misreads of 秒 ("second" in Japanese). PaddleOCR/EasyOCR routinely
# confuse 秒 with visually similar CJK glyphs. Treat these as equivalents so
# durations like "(59秒)" still parse when OCR returns "(59利)" etc.
_SEC_KANJI_ALTS = "秒利杪抄秋"

# "1 min 25 sec", "1m 25s", "1 phút 25 giây", "1分25秒"
RE_DUR_MIN_SEC = re.compile(
    rf"(?P<m>\d+)\s*(?:min(?:ute)?s?|m|phút|分)\s*(?P<s>\d+)?\s*(?:sec(?:ond)?s?|s|giây|[{_SEC_KANJI_ALTS}])?",
    re.IGNORECASE,
)
# "25 sec" / "25s" / "25 giây" / "25秒" with no minutes part
# (CJK kanji is a Unicode word char, so use a non-digit lookahead rather than
# \b after the suffix.)
RE_DUR_SEC_ONLY = re.compile(
    rf"(?<!\d)(?P<s>\d+)\s*(?:sec(?:ond)?s?|s|giây|[{_SEC_KANJI_ALTS}])(?!\d)",
    re.IGNORECASE,
)
# "00:01:25" or "01:25" — colon-separated duration
RE_DUR_COLON = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)")

# Stand-alone "0 sec" / "0s"
RE_ZERO_SEC = re.compile(
    rf"\b0\s*(?:sec(?:ond)?s?|s|giây|[{_SEC_KANJI_ALTS}])",
    re.IGNORECASE,
)

# Status-bar / chrome lines that should NOT be parsed as call entries.
# These include carrier labels with the system clock and battery percentages.
RE_STATUS_BAR = re.compile(
    r"(?:Rakuten|docomo|SoftBank|au\s|Verizon|AT&T|T-Mobile|Vodafone)\b"
    r"|\d{1,3}\s*%"            # battery percent
    r"|通話履歴|すべての通話",   # call-log app chrome (JP)
    re.IGNORECASE,
)

# In-app advertising banners. Rakuten Link injects coupon cards *between* call
# rows, and they carry times ("7月27日(月) 13:00まで") that were being parsed as
# call start times, inventing phantom calls in the middle of the day.
RE_PROMO_ROW = re.compile(
    r"クーポン"                    # coupon
    r"|円"                        # yen amounts: 1,000円 -> 880円 / 120円OFF
    r"|割引|限定|キャンペーン|特典|獲得|条件あり"
    r"|まで\b|までに"              # "valid until ..."
    r"|即日納品|新規ご契約|ご利用詳細"
    r"|\bOFF\b|\bギガ\b"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日"  # an explicit calendar date (M月D日)
    r"|\bcoupon\b|\bdiscount\b|\boff\b\s*!?$",
    re.IGNORECASE,
)

# A genuine call row shows a direction marker or a duration. Rows carrying a
# time but neither are almost always chrome we failed to classify.
RE_CALL_EVIDENCE = re.compile(
    r"発信|着信|不在|応答|通話中|拒否"
    r"|\d+\s*(?:秒|分|giây|phút|sec|min)"
    r"|\bincoming\b|\boutgoing\b|\bmissed\b"
    r"|gọi (?:đi|đến)|cuộc gọi|nhỡ",
    re.IGNORECASE,
)


def parse_duration(
    text: str,
    start_time_str: Optional[str],
    start_span: Optional[Tuple[int, int]] = None,
) -> Tuple[Optional[int], List[str]]:
    """
    Parse a duration anywhere in `text` and return (seconds, warnings).

    Returns 0 for explicit zero-duration. None if nothing parseable.

    `start_span` is the (begin, end) offset the start time was matched at. When
    supplied, those exact characters are removed -- far more reliable than
    regenerating the pattern from the 24-hour value, which silently failed for
    zero-padded 12-hour times like "03:08 PM".
    """
    warnings: List[str] = []

    work = text
    if start_span is not None:
        begin, end = start_span
        # Also swallow a trailing AM/PM the time regex may not have captured.
        tail = re.match(r"\s*[AaPp][Mm.]?\b", work[end:])
        if tail:
            end += tail.end()
        work = work[:begin] + " " + work[end:]
    elif start_time_str:
        # Legacy path (kept for direct callers/tests that pass only the value).
        h, m = start_time_str.split(":")
        h12 = int(h) % 12 or 12
        for pattern in (
            rf"(?<!\d){int(h):02d}\s*[:.]\s*{int(m):02d}(?!\d)",
            rf"(?<!\d){int(h)}\s*[:.]\s*{int(m):02d}(?!\d)",
            rf"(?<!\d)0?{h12}\s*[:.]\s*{int(m):02d}\s*[AaPp][Mm.]?",
        ):
            work = re.sub(pattern, " ", work)

    # 1) Explicit "X min Y sec" form (highest confidence)
    m = RE_DUR_MIN_SEC.search(work)
    if m:
        mins = int(m.group("m"))
        secs = int(m.group("s")) if m.group("s") else 0
        return mins * 60 + secs, warnings

    # 2) Seconds-only form
    m = RE_DUR_SEC_ONLY.search(work)
    if m:
        return int(m.group("s")), warnings

    # 3) Colon form: 00:01:25 always means H:M:S. 01:25 is ambiguous (m:s vs h:m).
    matches = list(RE_DUR_COLON.finditer(work))
    if matches:
        # Prefer a hh:mm:ss occurrence
        for hm in matches:
            if hm.group(3) is not None:
                h, mi, s = int(hm.group(1)), int(hm.group(2)), int(hm.group(3))
                return h * 3600 + mi * 60 + s, warnings
        # Fall back to mm:ss (the documented common case)
        hm = matches[0]
        a, b = int(hm.group(1)), int(hm.group(2))
        # In call logs short durations dominate. Treat NN:NN as mm:ss but warn.
        warnings.append("duration_format_ambiguous_assumed_mm_ss")
        return a * 60 + b, warnings

    # 4) Explicit zero
    if RE_ZERO_SEC.search(work):
        return 0, warnings

    return None, ["duration_not_found"]


# ---------------------------------------------------------------------------
# Direction / status detection
# ---------------------------------------------------------------------------

# Direction keywords across English, Japanese, Vietnamese
INCOMING_HINTS = (
    "incoming", "received", "from",
    "着信", "受信", "受話",
    "cuộc gọi đến", "gọi đến", "đến",
)
OUTGOING_HINTS = (
    "outgoing", "outbound", "dialed", "called", "to",
    "発信", "発呼",
    "cuộc gọi đi", "gọi đi", "đi",
)
MISSED_HINTS = (
    "missed", "no answer", "unanswered", "declined", "rejected",
    "不在", "応答なし", "拒否",
    "nhỡ", "không trả lời", "lỡ",
)
ANSWERED_HINTS = (
    "answered", "completed", "connected",
    "応答", "通話",
    "đã trả lời", "đã kết nối",
)


def _contains_any(text: str, words) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def detect_direction_and_status(
    text: str, raw_duration_seconds: Optional[int]
) -> Tuple[str, str, List[str]]:
    """
    Return (direction, status, warnings).

    direction in {incoming, outgoing, unknown}
    status    in {answered, missed, unanswered, zero_duration, unknown}
    """
    warnings: List[str] = []

    incoming = _contains_any(text, INCOMING_HINTS)
    outgoing = _contains_any(text, OUTGOING_HINTS)
    missed = _contains_any(text, MISSED_HINTS)
    answered = _contains_any(text, ANSWERED_HINTS)

    if incoming and outgoing:
        direction = "unknown"
        warnings.append("direction_conflicting_keywords")
    elif incoming:
        direction = "incoming"
    elif outgoing:
        direction = "outgoing"
    else:
        direction = "unknown"

    if missed:
        status = "missed"
    elif raw_duration_seconds == 0:
        status = "zero_duration"
    elif raw_duration_seconds and raw_duration_seconds > 0:
        status = "answered"
    elif answered:
        status = "answered"
    else:
        status = "unknown"

    return direction, status, warnings


# ---------------------------------------------------------------------------
# Countability rules
# ---------------------------------------------------------------------------

def is_countable(direction: str, status: str, raw_duration_seconds: Optional[int]
                 ) -> Tuple[bool, List[str]]:
    """
    Implements the spec's filtering rules:

    Count:
      - Normal incoming calls (answered)
      - Normal outgoing calls (answered)
      - Outgoing unanswered/missed/zero-duration FROM the device
      - Outgoing 0-duration

    Exclude:
      - Incoming missed/unanswered TO the device
      - Anything where start/duration not parseable (caller checks separately)

    Ambiguity (direction unknown + zero/missed) -> not countable, flag review.
    """
    warnings: List[str] = []

    if direction == "incoming":
        if status in ("missed", "unanswered", "zero_duration"):
            return False, warnings  # excluded by rule
        return True, warnings

    if direction == "outgoing":
        # Outgoing always counts -- even zero/missed/unanswered from device
        return True, warnings

    # direction unknown
    if status in ("missed", "zero_duration", "unanswered"):
        warnings.append("direction_unknown_for_missed_or_zero")
        return False, warnings
    # Direction unknown but duration > 0: assume real call, count it but flag
    if raw_duration_seconds and raw_duration_seconds > 0:
        warnings.append("direction_unknown_assumed_countable")
        return True, warnings
    warnings.append("direction_and_status_unknown")
    return False, warnings


# ---------------------------------------------------------------------------
# Rounding + end time
# ---------------------------------------------------------------------------

def round_duration_minutes(raw_seconds: int) -> int:
    """
    Spec rule:
      remaining_seconds >= 20  -> round UP to next minute
      remaining_seconds <  20  -> round DOWN to latest minute
    """
    if raw_seconds <= 0:
        return 0
    minutes = raw_seconds // 60
    remainder = raw_seconds % 60
    if remainder >= CONFIG.round_up_seconds_threshold:
        minutes += 1
    return minutes


def add_minutes(time_str: str, minutes: int) -> str:
    dt = datetime.strptime(time_str, "%H:%M")
    dt = DUMMY_DATE.replace(hour=dt.hour, minute=dt.minute) + timedelta(minutes=minutes)
    return dt.strftime("%H:%M")


# ---------------------------------------------------------------------------
# Row-level entry point
# ---------------------------------------------------------------------------

def parse_row(source_file: str, raw_text: str, ocr_confidence: float,
              y_frac: Optional[float] = None) -> CallRecord:
    """
    Parse one OCR row into a CallRecord.

    `y_frac` is the row's vertical centre as a fraction of image height (0=top).
    It exists to protect the no-marker recovery path: a row carrying only a
    time is normally a call whose direction marker OCR split off, but at the
    very top of the screen it is the status-bar clock. Without this, "8:32"
    from the status bar would become an 08:32 call whenever OCR failed to
    group it with the carrier name. Pass None when position is unknown.
    """
    rec = CallRecord(
        source_file=source_file,
        raw_text=raw_text,
        ocr_confidence=ocr_confidence,
    )

    # Drop status-bar / app-chrome rows up front so a clock time like
    # "Rakuten 19:23 34%" is never mistaken for a call's start time.
    if RE_STATUS_BAR.search(raw_text):
        rec.warnings.append("status_bar_row_ignored")
        rec.countable = False
        rec.needs_review = False  # not actionable for the user
        return rec

    # Drop in-app ad/coupon banners, which sit between real call rows and carry
    # their own times ("7月27日(月) 13:00まで").
    if RE_PROMO_ROW.search(raw_text):
        rec.warnings.append("promo_row_ignored")
        rec.countable = False
        rec.needs_review = False
        return rec

    start, w1, start_span = parse_start_time_ex(raw_text)
    rec.start_time = start
    rec.warnings.extend(w1)

    raw_secs, w2 = parse_duration(raw_text, start, start_span)
    rec.raw_duration_seconds = raw_secs
    rec.warnings.extend(w2)

    direction, status, w3 = detect_direction_and_status(raw_text, raw_secs)
    rec.direction = direction
    rec.status = status
    rec.warnings.extend(w3)

    # Sanity / review flags
    if ocr_confidence < CONFIG.ocr_confidence_threshold:
        rec.warnings.append(f"low_ocr_confidence_{ocr_confidence:.2f}")

    # If we have a valid start time but the duration isn't visible, treat the
    # call as 0-second rather than dropping it. The source genuinely shows no
    # duration (very short / immediately disconnected), and per the user's
    # rule these no-duration calls count the same as any other call -- their
    # duration is just 0.
    #
    # Outgoing: always recover (matches the original spec behavior).
    # Incoming / unknown: recover UNLESS the row is explicitly marked missed
    #   (those stay excluded by the standard countability rule).
    #
    # A bare "03:08PM" with no direction marker IS recovered. OCR frequently
    # splits a call row so the marker lands in a separate detected line, or
    # misreads 発信 outright, and requiring a marker here silently deleted real
    # calls. The phantom rows this once guarded against -- coupon banners
    # carrying "13:00まで" -- are now caught earlier by RE_PROMO_ROW and
    # RE_STATUS_BAR, so the marker requirement cost recall without buying
    # precision.
    has_marker = bool(RE_CALL_EVIDENCE.search(raw_text))
    in_status_bar = (
        y_frac is not None
        and y_frac < CONFIG.status_bar_top_frac
        and not has_marker
    )
    if in_status_bar:
        rec.warnings.append("status_bar_region_row_ignored")
        rec.countable = False
        rec.needs_review = False
        return rec

    recovered_no_duration = False
    if (
        start is not None
        and raw_secs is None
        and status != "missed"
    ):
        rec.raw_duration_seconds = 0
        rec.status = "zero_duration"
        # Strip the now-incorrect duration_not_found warning if present
        rec.warnings = [w for w in rec.warnings if w != "duration_not_found"]
        if direction == "outgoing":
            rec.warnings.append("duration_missing_assumed_zero_for_outgoing")
        else:
            rec.warnings.append("duration_missing_assumed_zero")
        if not has_marker:
            # Recovered without any direction/duration marker. Still counted,
            # but worth surfacing in the review CSV.
            rec.warnings.append("recovered_without_call_marker")
        raw_secs = 0
        status = "zero_duration"
        recovered_no_duration = True

    if start is None or raw_secs is None:
        rec.countable = False
        rec.needs_review = True
        return rec

    # Rounding + end time
    rec.rounded_duration_minutes = round_duration_minutes(raw_secs)
    rec.end_time = add_minutes(start, rec.rounded_duration_minutes)

    # Cross-midnight sanity
    if rec.end_time < rec.start_time:
        rec.warnings.append("call_appears_to_cross_midnight")

    if recovered_no_duration:
        # Per user rule: a row with a valid start time but no duration is a
        # real call with 0 duration. Count it like any other call regardless
        # of direction. (Explicitly-missed rows were filtered out above.)
        countable, w4 = True, []
    else:
        countable, w4 = is_countable(direction, status, raw_secs)
    rec.countable = countable
    rec.warnings.extend(w4)

    # Decide whether this row actually needs human review. Informational-only
    # warnings (low_ocr_confidence on a row that nevertheless parsed every
    # field, OR the "duration_missing_assumed_zero_for_outgoing" assumption)
    # should NOT pollute the review CSV — they're already captured in
    # processing_log.json for audit. Only flag rows that are genuinely
    # uncertain or excluded.
    _INFORMATIONAL = {
        "duration_missing_assumed_zero_for_outgoing",
        "duration_missing_assumed_zero",
    }
    actionable = [
        w for w in rec.warnings
        if not w.startswith("low_ocr_confidence_") and w not in _INFORMATIONAL
    ]
    if actionable:
        rec.needs_review = True
    elif not rec.countable:
        rec.needs_review = True
    else:
        rec.needs_review = False

    return rec
