"""
Acceptance tests for the rule logic. These mirror Test 1..7 in the spec
plus a few extras (overlap, fifth exception cap, AM/PM parsing, JP/VI strings).

Run with:
    cd ocr_call_extractor
    python -m pytest tests.py -v
"""

from __future__ import annotations

import pytest

from parser import (
    parse_start_time, parse_duration, round_duration_minutes,
    detect_direction_and_status, is_countable, parse_row, add_minutes,
    CallRecord,
)
from session_builder import dedupe, build_sessions


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("8:00 AM", "08:00"),
    ("1:30 PM", "13:30"),
    ("12:05 AM", "00:05"),
    ("12:05 PM", "12:05"),
    ("21:34", "21:34"),
    ("午前 9:15", "09:15"),
    ("午後 1:30", "13:30"),
    ("8:00 SA", "08:00"),     # Vietnamese
    ("2:15 CH", "14:15"),
])
def test_time_parsing(text, expected):
    out, _ = parse_start_time(text)
    assert out == expected


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_seconds", [
    ("1 min 25 sec", 85),
    ("1m 25s", 85),
    ("01:25", 85),
    ("00:01:25", 85),
    ("1分25秒", 85),
    ("1 phút 25 giây", 85),
    ("0 sec", 0),
    ("0:00", 0),
    ("3 min 15 sec", 195),
    ("3 min 20 sec", 200),
])
def test_duration_parsing(text, expected_seconds):
    secs, _ = parse_duration(text, None)
    assert secs == expected_seconds


# ---------------------------------------------------------------------------
# Rounding rule (Tests 1, 2, 6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected_minutes", [
    (85, 2),       # 1m25s -> 2
    (79, 1),       # 1m19s -> 1
    (195, 3),      # 3m15s -> 3
    (200, 4),      # 3m20s -> 4
    (20, 1),       # 0m20s -> 1
    (19, 0),       # 0m19s -> 0
    (0, 0),        # 0s -> 0
])
def test_rounding(seconds, expected_minutes):
    assert round_duration_minutes(seconds) == expected_minutes


def test_test1_duration_rounding():
    """Spec Test 1: 08:00 + 1m25s -> end 08:02"""
    rec = parse_row("img.jpg", "8:00 AM 1 min 25 sec outgoing", ocr_confidence=0.95)
    assert rec.start_time == "08:00"
    assert rec.rounded_duration_minutes == 2
    assert rec.end_time == "08:02"


def test_test2_under_20_sec_rounding():
    """Spec Test 2: 08:05 + 3m15s -> end 08:08"""
    rec = parse_row("img.jpg", "8:05 AM 3 min 15 sec outgoing", ocr_confidence=0.95)
    assert rec.start_time == "08:05"
    assert rec.rounded_duration_minutes == 3
    assert rec.end_time == "08:08"


# ---------------------------------------------------------------------------
# Helpers to build test records
# ---------------------------------------------------------------------------

def make_rec(source, text, conf=0.95):
    return parse_row(source, text, ocr_confidence=conf)


# ---------------------------------------------------------------------------
# Session grouping (Tests 3, 4, 5)
# ---------------------------------------------------------------------------

def test_test3_before_9am_gap():
    """
    08:00, 1m25s -> ends 08:02
    08:05, 3m15s -> ends 08:08
    08:16, 2m00s -> ends 08:18
    Expect: two sessions because 08:08 -> 08:16 = 8 min gap before 9 AM.
    """
    recs = [
        make_rec("a.jpg", "8:00 AM 1 min 25 sec outgoing"),
        make_rec("b.jpg", "8:05 AM 3 min 15 sec outgoing"),
        make_rec("c.jpg", "8:16 AM 2 min 0 sec outgoing"),
    ]
    sessions, stats = build_sessions(recs)
    assert len(sessions) == 2
    assert sessions[0].start_time == "08:00"
    assert sessions[0].end_time == "08:08"
    assert sessions[1].start_time == "08:16"
    assert sessions[1].end_time == "08:18"
    assert stats["exceptions_used_total"] == 0


def test_test4_post_9am_exception_merge():
    """
    09:00, 3m00s -> ends 09:03
    09:12, 2m00s -> ends 09:14
    Gap = 9 min, next call >= 09:00, exception eligible -> single session.
    """
    recs = [
        make_rec("a.jpg", "9:00 AM 3 min 0 sec outgoing"),
        make_rec("b.jpg", "9:12 AM 2 min 0 sec outgoing"),
    ]
    sessions, stats = build_sessions(recs)
    assert len(sessions) == 1
    assert sessions[0].start_time == "09:00"
    assert sessions[0].end_time == "09:14"
    assert stats["exceptions_used_total"] == 1


def test_test5_fifth_exception_not_allowed():
    """
    Five post-9 AM gaps of 6-10 minutes.
    Only first 4 merge. The 5th eligible gap starts a new session.
    """
    # Build chain: every call is 1 minute long, 7-minute gaps between ends and next starts.
    # end1=09:01, start2=09:08, end2=09:09, start3=09:16, end3=09:17, start4=09:24,
    # end4=09:25, start5=09:32, end5=09:33, start6=09:40.
    # First 5 gaps = 7 min each. First 4 are merged, the 5th gap starts new session.
    times = ["9:00", "9:08", "9:16", "9:24", "9:32", "9:40"]
    recs = [make_rec(f"img{i}.jpg", f"{t} AM 1 min 0 sec outgoing") for i, t in enumerate(times)]
    sessions, stats = build_sessions(recs)
    assert stats["exceptions_used_total"] == 4
    assert len(sessions) == 2
    # First session ate calls 1..5
    assert sessions[0].start_time == "09:00"
    assert sessions[0].end_time == "09:33"
    assert sessions[0].call_count == 5
    # Second session has only the last call
    assert sessions[1].start_time == "09:40"
    assert sessions[1].end_time == "09:41"
    assert sessions[1].call_count == 1


def test_test6_outgoing_zero_duration_counted():
    """Outgoing 0-sec from device counts; start==end."""
    rec = make_rec("a.jpg", "9:00 AM outgoing 0 sec")
    assert rec.countable is True
    assert rec.start_time == "09:00"
    assert rec.end_time == "09:00"


def test_test7_incoming_missed_excluded():
    """Incoming missed call to device is excluded."""
    rec = make_rec("a.jpg", "9:00 AM incoming missed call")
    assert rec.countable is False


# ---------------------------------------------------------------------------
# Extra: overlap, dedupe, gap boundary
# ---------------------------------------------------------------------------

def test_overlap_merges_and_flags():
    """Overlapping calls become one session with a warning."""
    recs = [
        make_rec("a.jpg", "10:00 AM 20 min 0 sec outgoing"),
        make_rec("b.jpg", "10:15 AM 10 min 0 sec outgoing"),
    ]
    sessions, stats = build_sessions(recs)
    assert len(sessions) == 1
    assert sessions[0].start_time == "10:00"
    assert sessions[0].end_time == "10:25"
    assert stats["overlaps_total"] == 1
    assert any("overlap_with" in w for w in sessions[0].warnings)


def test_dedup_exact_duplicate():
    """Two identical entries collapse; one duplicate is logged."""
    recs = [
        make_rec("a.jpg", "8:00 AM 1 min 25 sec outgoing"),
        make_rec("b.jpg", "8:00 AM 1 min 25 sec outgoing"),
    ]
    kept, dupes = dedupe(recs)
    assert len(kept) == 1
    assert len(dupes) == 1


def test_gap_exactly_5_min_same_session_before_9am():
    """Gap == 5 min stays in same session per spec ('<=5')."""
    recs = [
        make_rec("a.jpg", "8:00 AM 1 min 0 sec outgoing"),   # ends 08:01
        make_rec("b.jpg", "8:06 AM 1 min 0 sec outgoing"),   # gap = 5 min
    ]
    sessions, _ = build_sessions(recs)
    assert len(sessions) == 1


def test_gap_exactly_10_min_post_9am_uses_exception():
    """Gap == 10 min after 9 AM consumes one exception."""
    recs = [
        make_rec("a.jpg", "9:00 AM 1 min 0 sec outgoing"),   # ends 09:01
        make_rec("b.jpg", "9:11 AM 1 min 0 sec outgoing"),   # gap = 10 min
    ]
    sessions, stats = build_sessions(recs)
    assert len(sessions) == 1
    assert stats["exceptions_used_total"] == 1


def test_gap_over_10_min_post_9am_no_exception():
    """Gap > 10 min after 9 AM is not eligible."""
    recs = [
        make_rec("a.jpg", "9:00 AM 1 min 0 sec outgoing"),   # ends 09:01
        make_rec("b.jpg", "9:12 AM 1 min 0 sec outgoing"),   # gap = 11 min
    ]
    sessions, stats = build_sessions(recs)
    assert len(sessions) == 2
    assert stats["exceptions_used_total"] == 0


def test_add_minutes_basic():
    assert add_minutes("08:00", 2) == "08:02"
    assert add_minutes("09:58", 5) == "10:03"
    assert add_minutes("23:58", 5) == "00:03"   # wraps; downstream flags this


# ---------------------------------------------------------------------------
# Regression tests for the July 20-24 defects
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # OCR dropped the PM marker. Nobody calls before 07:00, so these are PM.
    ("02:23 発信 (30秒)", "14:23"),
    ("01:03 発信 (12秒)", "13:03"),
    ("02:49 発信 (40秒)", "14:49"),
    ("06:59 発信 (5秒)",  "18:59"),
    # Unambiguous or plausible-as-24h values must NOT be shifted.
    ("08:02 発信 (30秒)", "08:02"),
    ("11:07 発信 (30秒)", "11:07"),
    ("12:05 発信 (30秒)", "12:05"),
    ("21:34 発信 (30秒)", "21:34"),
    # An explicit marker always wins over the heuristic.
    ("02:23 PM 発信 (30秒)", "14:23"),
    ("08:00 AM 発信 (30秒)", "08:00"),
])
def test_lost_pm_marker_recovered(text, expected):
    """Phantom pre-dawn sessions came from PM markers lost by OCR."""
    assert parse_start_time(text)[0] == expected


def test_lost_pm_shift_is_flagged():
    rec = parse_row("x.jpg", "02:23 発信 (30秒)", 0.95)
    assert "ampm_missing_shifted_to_pm" in rec.warnings


def test_no_duration_row_is_not_read_as_duration():
    """'03:08 PM 発信' with no duration must be 0 sec, not 3 min 8 sec."""
    rec = parse_row("x.jpg", "03:08 PM 発信", 0.95)
    assert rec.start_time == "15:08"
    assert rec.raw_duration_seconds == 0
    assert rec.end_time == "15:08"


def test_promo_banner_is_not_a_call():
    """In-app coupon banners carry times and invented phantom calls."""
    rec = parse_row("x.jpg", "1,000円 → 880円 7月27日(月) 13:00まで", 0.95)
    assert rec.start_time is None
    assert not rec.countable
    assert "promo_row_ignored" in rec.warnings


def _sess(start, end, src="a.jpg"):
    return CallRecord(source_file=src, raw_text="", start_time=start,
                      end_time=end, direction="outgoing", status="answered",
                      countable=True, rounded_duration_minutes=1)


def test_afternoon_exceptions_not_starved_by_morning():
    """
    Pre-9AM gaps must not consume the post-9AM exception budget, otherwise
    a 14:30 -> 14:36 pair fails to merge (real case, 20 July).
    """
    day = [
        _sess("08:07", "08:17"), _sess("08:26", "08:27"),
        _sess("08:33", "08:34"), _sess("08:42", "08:46"),
        _sess("14:16", "14:27"), _sess("14:36", "14:38"),
        _sess("14:45", "14:46"),
    ]
    sessions, stats = build_sessions(day)
    merged = [s for s in sessions if s.start_time == "14:16"]
    assert len(merged) == 1
    assert merged[0].end_time == "14:46"
    assert merged[0].call_count == 3


def test_standalone_zero_length_session_is_kept():
    """
    A lone 0-second call adds nothing to the daily total but DID happen, and
    dropping it corrupted B3/first-start when a day opened with one.
    """
    sessions, stats = build_sessions([_sess("22:56", "22:56")])
    assert len(sessions) == 1
    assert (sessions[0].start_time, sessions[0].end_time) == ("22:56", "22:56")
    assert stats["zero_length_sessions_dropped"] == 0


def test_zero_duration_opening_call_sets_first_start():
    """Day opens with a 0-second call at 08:00, next call at 08:30."""
    sessions, _ = build_sessions([_sess("08:00", "08:00"), _sess("08:30", "09:10")])
    assert sessions[0].start_time == "08:00"


def test_bare_time_row_without_marker_is_counted():
    """
    OCR often splits a row so the direction marker lands elsewhere. Requiring
    a marker here silently deleted real calls.
    """
    rec = parse_row("x.jpg", "03:08PM", 0.99)
    assert rec.start_time == "15:08"
    assert rec.raw_duration_seconds == 0
    assert rec.countable
    assert "recovered_without_call_marker" in rec.warnings


def test_bare_time_row_with_marker_not_flagged():
    rec = parse_row("x.jpg", "03:08PM\u767a\u4fe1", 0.99)
    assert rec.countable
    assert "recovered_without_call_marker" not in rec.warnings


def test_promo_and_status_rows_still_rejected_without_the_marker_guard():
    """The guard was removed; these must still be filtered earlier."""
    for text in ("1,000\u5186 \u2192 880\u5186 7\u670827\u65e5(\u6708) 13:00\u307e\u3067",
                 "Rakuten 8:32 R 189%"):
        rec = parse_row("x.jpg", text, 0.99)
        assert not rec.countable, text
        assert rec.start_time is None, text


def test_zero_length_call_kept_when_merged():
    """A 0-second call adjacent to others still joins its session."""
    sessions, _ = build_sessions([_sess("09:10", "09:20"), _sess("09:22", "09:22")])
    assert len(sessions) == 1
    assert sessions[0].call_count == 2


def test_sessions_renumbered_after_drop():
    sessions, _ = build_sessions([
        _sess("01:30", "01:30"), _sess("09:10", "09:20"), _sess("11:00", "11:30"),
    ])
    assert [s.session_index for s in sessions] == list(range(1, len(sessions) + 1))


# ---------------------------------------------------------------------------
# Unspaced CJK rows -- the form PaddleOCR actually emits for screenshots
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,start,secs", [
    # Real PaddleOCR output. No space before AM/PM and none before the kanji.
    # Python's re counts CJK as word chars, so the old trailing \b in
    # RE_TIME_12 could not match and EVERY row on a Japanese screenshot was
    # discarded with "start_time_not_found" while its duration parsed fine.
    ("03:00PM発信(32秒)", "15:00", 32),
    ("02:56PM発信(22秒)", "14:56", 22),
    ("02:49PM発信(38秒)", "14:49", 38),
    ("08:02AM発信(30秒)", "08:02", 30),
    ("12:05AM発信(5秒)",  "00:05", 5),
    ("21:34発信(0秒)",    "21:34", 0),
    # Spaced form must keep working.
    ("03:00 PM 発信 (32秒)", "15:00", 32),
    ("08:26 AM 発信 (33秒)", "08:26", 33),
])
def test_unspaced_cjk_row_parses(text, start, secs):
    rec = parse_row("x.jpg", text, 0.99)
    assert rec.start_time == start
    assert rec.raw_duration_seconds == secs
    assert rec.countable


def test_unspaced_row_without_duration():
    rec = parse_row("x.jpg", "03:08PM発信", 0.99)
    assert rec.start_time == "15:08"
    assert rec.raw_duration_seconds == 0
    assert rec.countable


def test_phone_number_is_not_a_time():
    for num in ("012-345-6789", "0123-45-6789", "090-1234-5678"):
        assert parse_start_time(num)[0] is None


# ---------------------------------------------------------------------------
# sheets_sync -- Web App push. Network is mocked; nothing leaves the machine.
# Written with unittest.mock (no pytest fixtures) so run_tests.py can run it.
# ---------------------------------------------------------------------------

import json
from contextlib import ExitStack
from unittest import mock

import sheets_sync
from config import CONFIG
from session_builder import Session
from sheets_sync import SheetsSyncError, sync_day


def _resp(body):
    """A context-manager stand-in for urlopen's response."""
    r = mock.MagicMock()
    r.__enter__.return_value.read.return_value = (
        body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
    return r


def _sync(urlopen_effect, sessions=None, **cfg):
    """
    Run sync_day with CONFIG overridden and urlopen/sleep/log mocked.
    Returns (result_or_exception, urlopen_mock, sleep_mock, log_mock).
    """
    settings = dict(sheets_sync_enabled=True,
                    sheets_webapp_url="https://example.invalid/exec",
                    sheets_sync_secret="s3cret", sheets_sync_retries=3)
    settings.update(cfg)
    with ExitStack() as st:
        for k, v in settings.items():
            st.enter_context(mock.patch.object(CONFIG, k, v))
        urlopen = st.enter_context(mock.patch.object(
            sheets_sync.urllib.request, "urlopen", side_effect=urlopen_effect))
        sleep = st.enter_context(mock.patch.object(sheets_sync.time, "sleep"))
        log = st.enter_context(mock.patch.object(sheets_sync, "log"))
        try:
            out = sync_day("Nguyen Van A", "2026-07-20", sessions or [])
        except SheetsSyncError as exc:
            out = exc
    return out, urlopen, sleep, log


def test_sync_disabled_makes_no_request():
    out, urlopen, _, _ = _sync([], sheets_sync_enabled=False)
    assert out["skipped"] is True
    urlopen.assert_not_called()


def test_sync_enabled_without_url_raises():
    out, urlopen, _, _ = _sync([], sheets_webapp_url="")
    assert isinstance(out, SheetsSyncError)
    urlopen.assert_not_called()


def test_sync_payload_shape():
    sessions = [Session(1, "08:00", "08:08", 8, 2), Session(2, "09:00", "09:14", 14, 2)]
    out, urlopen, _, _ = _sync([_resp({"ok": True, "month_linked": True})], sessions)
    assert out["ok"] is True
    req = urlopen.call_args[0][0]
    assert req.get_method() == "POST"
    assert json.loads(req.data) == {
        "secret": "s3cret", "name": "Nguyen Van A", "date": "2026-07-20",
        "sessions": [{"start": "08:00", "end": "08:08"},
                     {"start": "09:00", "end": "09:14"}],
    }


def test_sync_retries_transient_errors_then_succeeds():
    """Read timeouts and dropped keep-alives are OSError, not URLError."""
    out, urlopen, sleep, _ = _sync([TimeoutError("read"), ConnectionResetError(),
                                    _resp({"ok": True})])
    assert out["ok"] is True
    assert urlopen.call_count == 3
    assert [c.args[0] for c in sleep.call_args_list] == [1, 2]   # backoff


def test_sync_gives_up_after_configured_attempts():
    err = sheets_sync.urllib.error.URLError("down")
    out, urlopen, sleep, _ = _sync([err, err], sheets_sync_retries=2)
    assert isinstance(out, SheetsSyncError)
    assert "2 attempt" in str(out)
    assert urlopen.call_count == 2
    assert sleep.call_count == 1      # no sleep after the final attempt


def test_sync_zero_retries_still_tries_once():
    out, urlopen, _, _ = _sync([_resp({"ok": True})], sheets_sync_retries=0)
    assert out["ok"] is True
    assert urlopen.call_count == 1


def test_sync_non_json_response_raises():
    """An Apps Script login/HTML error page must not count as success."""
    out, _, _, _ = _sync([_resp(b"<html>Sign in</html>")])
    assert isinstance(out, SheetsSyncError)
    assert "non-JSON" in str(out)


def test_sync_webapp_reported_error_raises():
    out, _, _, _ = _sync([_resp({"ok": False, "error": "bad secret"})])
    assert isinstance(out, SheetsSyncError)
    assert "bad secret" in str(out)


def test_sync_warns_when_month_backlink_missing():
    out, _, _, log = _sync([_resp({"ok": True, "month_linked": False})])
    assert out["ok"] is True
    log.warning.assert_called_once()
    assert "2026-07" in log.warning.call_args.args   # date[:7] month key


@pytest.mark.parametrize("month_linked", [True, None])
def test_sync_no_backlink_warning_otherwise(month_linked):
    """None = older Web App deployment that doesn't report the field."""
    body = {"ok": True}
    if month_linked is not None:
        body["month_linked"] = month_linked
    _, _, _, log = _sync([_resp(body)])
    log.warning.assert_not_called()


# ---------------------------------------------------------------------------
# ocr -- page-orientation classification runs only on landscape images.
# The PaddleOCR engine is a stub, so no model is loaded.
# ---------------------------------------------------------------------------

from PIL import Image

import ocr


def _predict_kwargs(size, **cfg):
    """OCR a blank image of `size` with a stub engine; return predict()'s kwargs."""
    engine = mock.MagicMock()
    engine.predict.return_value = []
    with ExitStack() as st:
        for k, v in cfg.items():
            st.enter_context(mock.patch.object(CONFIG, k, v))
        st.enter_context(mock.patch.object(ocr, "_paddle_engine", engine))
        st.enter_context(mock.patch.object(ocr, "_paddle_api", 3))
        st.enter_context(mock.patch.object(ocr, "_check_framework"))
        ocr._ocr_paddle(Image.new("RGB", size, "white"))
    return engine.predict.call_args.kwargs


@pytest.mark.parametrize("size, expected", [
    ((720, 1600), False),   # portrait: already upright, never re-rotated
    ((1000, 1000), False),  # square counts as portrait
    ((1600, 720), True),    # landscape: a sideways screen, classify it
])
def test_doc_orientation_only_for_landscape(size, expected):
    kw = _predict_kwargs(size, paddle_use_doc_orientation=True)
    assert kw["use_doc_orientation_classify"] is expected


def test_doc_orientation_setting_off_never_classifies():
    kw = _predict_kwargs((1600, 720), paddle_use_doc_orientation=False)
    assert kw["use_doc_orientation_classify"] is False


# ---------------------------------------------------------------------------
# Characterisation tests: behaviour the AWS runner must reproduce exactly.
# They record what the code does today; a failure means behaviour changed.
# ---------------------------------------------------------------------------

import tempfile
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.workbook.workbook import Workbook

import excel_writer
import main
from config import Config

_TEMPLATE = Path(__file__).resolve().parent / "sprs.xlsx"
_FOLDER = "Nguyen Van A & 2026-06-01"


def _two_calls():
    """08:00 + 1m25s and 08:05 + 3m15s -> one Session 08:00-08:08."""
    return [make_rec("shot_01.jpg", "8:00 AM 1 min 25 sec outgoing"),
            make_rec("shot_02.jpg", "8:05 AM 3 min 15 sec outgoing")]


def _folder_run(records, sync_effect=None, folder=_FOLDER, **cfg):
    """
    Run main._process_one_folder in batch mode (delete_after=True) on a temp
    folder holding two placeholder images. OCR (extract_calls) and the Web
    App call (sync_day) are stubbed, and urlopen fails the test if reached,
    so nothing can touch a live Sheet. Returns what happened.
    """
    settings = dict(sheets_sync_enabled=True, archive_input_folders=True)
    settings.update(cfg)
    stats = {"ocr_failures": 0, "ocr_empty": 0}
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as st:
        images = Path(tmp) / "input_images"
        sub = images / folder
        sub.mkdir(parents=True)
        for name in ("shot_01.jpg", "shot_02.jpg"):
            (sub / name).write_bytes(b"placeholder, never decoded")
        out_dir = Path(tmp) / "output"

        for k, v in settings.items():
            st.enter_context(mock.patch.object(CONFIG, k, v))
        st.enter_context(mock.patch.object(
            main, "extract_calls", return_value=(records, [], stats)))
        sync = st.enter_context(mock.patch.object(
            main, "sync_day", side_effect=sync_effect))
        st.enter_context(mock.patch.object(
            sheets_sync.urllib.request, "urlopen",
            side_effect=AssertionError("a test reached the network")))
        st.enter_context(mock.patch.object(main, "log"))
        st.enter_context(mock.patch.object(excel_writer, "log"))

        try:
            result = main._process_one_folder(sub, _TEMPLATE, out_dir,
                                              delete_after=True)
        except RuntimeError as exc:
            result = exc
        return {
            "result": result,
            "xlsx": sorted(p.name for p in out_dir.glob("*.xlsx")),
            "in_place": sub.is_dir(),
            "archived": (images / CONFIG.input_archive_dirname / folder).is_dir(),
            "sync": sync,
        }


def test_folder_sync_failure_keeps_xlsx_and_folder():
    run = _folder_run(_two_calls(), sync_effect=SheetsSyncError("down"))
    assert run["xlsx"] == [f"{_FOLDER}.xlsx"]
    assert run["in_place"] and not run["archived"]


def test_folder_zero_sessions_raises_and_writes_nothing():
    run = _folder_run([])
    assert isinstance(run["result"], RuntimeError)
    assert "no sessions extracted" in str(run["result"])
    assert run["xlsx"] == []
    assert run["in_place"] and not run["archived"]
    run["sync"].assert_not_called()


def test_folder_success_syncs_then_archives():
    run = _folder_run(_two_calls())
    assert run["xlsx"] == [f"{_FOLDER}.xlsx"]
    assert run["archived"] and not run["in_place"]
    name, date, sessions = run["sync"].call_args.args
    assert (name, date) == ("Nguyen Van A", "2026-06-01")
    assert [(s.start_time, s.end_time) for s in sessions] == [("08:00", "08:08")]


def test_folder_sync_switched_off_still_archives():
    """The PC's Sync Switch off: xlsx only, folder retired, no Web App call."""
    run = _folder_run(_two_calls(), sheets_sync_enabled=False)
    assert run["xlsx"] == [f"{_FOLDER}.xlsx"]
    assert run["archived"]
    run["sync"].assert_not_called()


def test_folder_unparseable_name_keeps_folder():
    run = _folder_run(_two_calls(), folder="Nguyen Van A no date")
    assert run["xlsx"] == ["Nguyen Van A no date.xlsx"]
    assert run["in_place"] and not run["archived"]
    run["sync"].assert_not_called()


def _write_day_sheet(sessions, template=_TEMPLATE):
    """
    write_sessions into a temp file; return (in-memory sheet as written,
    reloaded sheet). The in-memory copy keeps the raw floats, which openpyxl
    would turn into datetime.time on reload because of the HH:mm format.
    """
    captured = {}
    real_save = Workbook.save

    def capture(self, filename):
        captured["wb"] = self
        return real_save(self, filename)

    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(Workbook, "save", capture), \
            mock.patch.object(excel_writer, "log"):
        out = Path(tmp) / f"{_FOLDER}.xlsx"
        excel_writer.write_sessions(template, out, sessions)
        reloaded = load_workbook(out)
    return captured["wb"][CONFIG.sheet_name], reloaded[CONFIG.sheet_name]


def _seventeen_sessions():
    """Starts 08:00, 08:25, ... each 10 minutes long: one more than the template's 16 rows."""
    out = []
    for i in range(17):
        start = add_minutes("08:00", 25 * i)
        out.append(Session(i + 1, start, add_minutes(start, 10), 10, 1))
    return out


def _fraction(hhmm):
    h, m = hhmm.split(":")
    return (int(h) * 60 + int(m)) / 1440.0


def test_day_sheet_layout_with_expanded_rows():
    sessions = _seventeen_sessions()
    ws, reloaded = _write_day_sheet(sessions)
    for i, s in enumerate(sessions):
        r = 7 + i
        # Numeric day-fractions, never strings or =TIME(...) formulas.
        assert isinstance(ws[f"B{r}"].value, float)
        assert abs(ws[f"B{r}"].value - _fraction(s.start_time)) < 1e-12
        assert abs(ws[f"C{r}"].value - _fraction(s.end_time)) < 1e-12
        assert ws[f"D{r}"].value == f"=C{r}-B{r}"
        assert ws[f"B{r}"].number_format == "HH:mm"
        assert ws[f"C{r}"].number_format == "HH:mm"
        assert ws[f"D{r}"].number_format == "[h]:mm"
    assert ws["B2"].value == "=SUM(D7:D23)"      # the range grows with the rows
    assert ws["B2"].number_format == "[h]:mm"
    assert ws["B3"].value == "=B7"
    assert ws["B3"].number_format == "HH:mm"
    # The inserted row copies row 22's style and continues the numbering.
    # style_id covers font, border, fill, alignment and number format at once
    # (openpyxl's style proxies don't compare by value).
    assert ws["A23"].value == 17
    for col in "ABCD":
        assert ws[f"{col}23"].style_id == ws[f"{col}22"].style_id, col
    # The formulas survive the save.
    assert reloaded["D23"].value == "=C23-B23"
    assert reloaded["B2"].value == "=SUM(D7:D23)"


def test_day_sheet_unused_rows_are_cleared_to_zero():
    sessions = [Session(1, "08:00", "08:08", 8, 2), Session(2, "09:00", "09:14", 14, 2)]
    ws, _ = _write_day_sheet(sessions)
    for r in range(9, 23):
        assert ws[f"B{r}"].value is None and ws[f"C{r}"].value is None
        assert ws[f"D{r}"].value == 0
        assert ws[f"D{r}"].number_format == "[h]:mm"
    assert ws["B2"].value == "=SUM(D7:D22)"
    assert ws["A23"].value is None                # no rows inserted


def test_day_sheet_keeps_the_attendance_comment():
    """B4 is filled in by a person and must never be overwritten."""
    with tempfile.TemporaryDirectory() as tmp:
        template = Path(tmp) / "template.xlsx"
        wb = load_workbook(_TEMPLATE)
        wb[CONFIG.sheet_name]["B4"] = "Fictional note: on time"
        wb.save(template)
        ws, _ = _write_day_sheet([Session(1, "08:00", "08:08", 8, 2)], template)
    assert ws["B4"].value == "Fictional note: on time"


def test_load_bearing_settings_are_unchanged():
    """
    Each of these is a decision with a history (AGENTS.md, Known pitfalls):
    strict engine, because a silent fallback ran on EasyOCR for two months;
    oneDNN off, because it kills every inference on Windows; 4 exceptions
    per day, the Owner's rule. Changing one needs the Owner, not a refactor.
    """
    c = Config()
    assert c.ocr_strict_engine is True
    assert c.paddle_enable_mkldnn is False
    assert c.post_9am_exception_limit == 4


# ---------------------------------------------------------------------------
# Synthetic Set plan (tools/gen_synth.py). Rendering needs a CJK font and
# check_synth.py needs the OCR models; the plan needs neither.
# ---------------------------------------------------------------------------

from tools import gen_synth


def test_synth_plan_is_deterministic_per_seed():
    a, b = gen_synth.plan(7, 3, 2, 1), gen_synth.plan(7, 3, 2, 1)
    assert a["truth"] == b["truth"] and a["tilts"] == b["tilts"]
    assert gen_synth.plan(8, 3, 2, 1)["truth"] != a["truth"]


def test_synth_truth_is_exactly_what_the_images_show():
    p = gen_synth.plan(7, 3, 2, 1)
    # 11 rows per screenshot, one row shared between neighbours.
    assert [len(p["truth"][k]["calls"]) for k in ("screenshots", "photos", "sideways")] \
        == [31, 21, 11]
    # Photos show the newest calls, so their truth is the end of the day.
    day = p["truth"]["screenshots"]["calls"]
    assert p["truth"]["photos"]["calls"] == day[-21:]
    assert p["truth"]["sideways"]["calls"] == day[-11:]
    starts = [c["start"] for c in day]
    assert starts == sorted(starts)                 # oldest first


def test_synth_truth_never_holds_identical_calls():
    """dedupe() merges identical calls, so two in the truth would be a false mismatch."""
    for seed in range(200):
        calls = gen_synth.plan(seed, 12, 0, 0)["truth"]["screenshots"]["calls"]
        keys = [(c["start"], c["kind"], c["secs"]) for c in calls]
        assert len(keys) == len(set(keys)), seed


# ---------------------------------------------------------------------------
# Runner image: threads follow the CPUs a container is given; baked models
# must match models.sha256 exactly (tools/bake_models.py).
# ---------------------------------------------------------------------------

import config
import runtime_env
from tools import bake_models


@pytest.mark.parametrize("text, expected", [
    ("max 100000", None),        # cgroup v2: no quota
    ("-1 100000", None),         # cgroup v1: no quota
    ("200000 100000", 2),        # docker --cpus 2
    ("150000 100000", 2),        # 1.5 CPUs rounds up
    ("100000 100000", 1),
    ("50000 100000", 1),         # half a CPU still gets one thread
    ("0 100000", None),
    ("", None),
    ("garbage", None),
])
def test_cgroup_cpu_limit_parsing(text, expected):
    assert runtime_env._cgroup_cpu_limit(text) == expected


def _cpus(count, affinity=None, cgroup=None):
    with ExitStack() as st:
        st.enter_context(mock.patch.object(runtime_env.os, "cpu_count", return_value=count))
        st.enter_context(mock.patch.object(runtime_env, "_read_cgroup_cpu_limit", return_value=cgroup))
        if affinity is None:
            # Windows has no sched_getaffinity; make Linux runs behave the same.
            if hasattr(runtime_env.os, "sched_getaffinity"):
                st.enter_context(mock.patch.object(runtime_env.os, "sched_getaffinity",
                                                   side_effect=OSError))
        else:
            st.enter_context(mock.patch.object(runtime_env.os, "sched_getaffinity",
                                               create=True, return_value=set(range(affinity))))
        return runtime_env.available_cpus()


def test_cgroup_files_read_and_missing():
    with mock.patch.object(runtime_env, "open", mock.mock_open(read_data="200000 100000\n"),
                           create=True):
        assert runtime_env._read_cgroup_cpu_limit() == 2     # cgroup v2 cpu.max
    with mock.patch.object(runtime_env, "open", side_effect=OSError, create=True):
        assert runtime_env._read_cgroup_cpu_limit() is None  # no cgroup: no limit


def test_available_cpus_takes_the_tightest_limit():
    assert _cpus(16) == 16                          # the PC: no limits
    assert _cpus(16, affinity=4) == 4               # pinned to 4 cores
    assert _cpus(16, affinity=8, cgroup=2) == 2     # container quota
    assert _cpus(2, cgroup=4) == 2                  # quota above the machine


@pytest.mark.parametrize("cpus, threads", [(16, 10), (10, 10), (2, 2), (1, 1)])
def test_paddle_threads_are_ten_at_most_and_never_above_the_cpus(cpus, threads):
    with mock.patch.object(config, "available_cpus", return_value=cpus):
        assert Config().paddle_cpu_threads == threads


def _model_dir(tmp, files):
    root = Path(tmp) / "official_models"
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def _manifest_for(root, rels):
    return {rel: bake_models.sha256(root / rel) for rel in rels}


def test_model_verify_accepts_exact_files_and_ignores_hoster_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        root = _model_dir(tmp, {"M/inference.pdiparams": b"weights", "M/inference.yml": b"cfg",
                                "M/README.md": b"docs", "M/.cache/huggingface/x": b"etag"})
        manifest = _manifest_for(root, ["M/inference.pdiparams", "M/inference.yml"])
        assert bake_models.verify(root, manifest) == []


def test_model_verify_reports_changed_missing_and_unlisted_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = _model_dir(tmp, {"M/inference.pdiparams": b"weights", "M/inference.yml": b"cfg"})
        manifest = _manifest_for(root, ["M/inference.pdiparams", "M/inference.yml"])
        (root / "M/inference.pdiparams").write_bytes(b"new weights")     # drift
        (root / "M/inference.yml").unlink()                               # missing
        (root / "N/inference.json").parent.mkdir(parents=True)
        (root / "N/inference.json").write_bytes(b"{}")                    # extra model
        assert bake_models.verify(root, manifest) == [
            "checksum differs: M/inference.pdiparams",
            "missing: M/inference.yml",
            "not in models.sha256: N/inference.json",
        ]


def test_models_manifest_lists_the_three_models_in_use():
    entries = bake_models.read_manifest(bake_models.MANIFEST.read_text(encoding="utf-8"))
    assert {rel.split("/")[0] for rel in entries} == {
        "PP-OCRv6_medium_det", "PP-OCRv5_mobile_rec", "PP-LCNet_x1_0_doc_ori"}
    assert all(len(d) == 64 for d in entries.values())


# ---------------------------------------------------------------------------
# Parity check (tools/parity_check.py): the comparison and the safety rules.
# Docker is not needed; the container run itself is checked on the PC.
# ---------------------------------------------------------------------------

from tools import parity_check

_EXPECTED = [["08:00", "10:01"], ["11:04", "12:01"], ["13:02", "15:02"]]


def test_parity_identical_sessions():
    assert parity_check.first_difference(_EXPECTED, [list(s) for s in _EXPECTED]) is None


@pytest.mark.parametrize("got, message", [
    ([["08:00", "10:01"], ["11:04", "12:05"], ["13:02", "15:02"]],
     "session 2: expected 11:04-12:01, got 11:04-12:05"),
    ([["08:00", "10:01"], ["11:04", "12:01"]], "session 3: expected 13:02-15:02, got none"),
    (_EXPECTED + [["16:00", "16:10"]], "session 4: expected none, got 16:00-16:10"),
])
def test_parity_reports_the_first_differing_session(got, message):
    assert parity_check.first_difference(_EXPECTED, got) == message


def test_parity_detects_a_write_into_a_reference_folder():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "shot.jpg").write_bytes(b"x")
        before = parity_check.fingerprint(folder)
        assert parity_check.fingerprint(folder) == before
        (folder / "new.xlsx").write_bytes(b"y")
        assert parity_check.fingerprint(folder) != before


def test_parity_refuses_a_reference_set_inside_the_repo():
    with mock.patch("builtins.print") as out:
        code = parity_check.main(["--image", "x", "--ref", str(parity_check.ROOT / "input_images")])
    assert code == 2
    assert "outside the repo" in out.call_args.args[0]


def test_parity_needs_an_expectations_file():
    with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print") as out:
        code = parity_check.main(["--image", "x", "--ref", tmp])
    assert code == 2
    assert "No expectations file" in out.call_args.args[0]


# ---------------------------------------------------------------------------
# Privacy gate (tools/check_private_data.py): the hashed denylist and the
# fail-closed mode CI uses. All values here are fictional.
# ---------------------------------------------------------------------------

import os

from tools import check_private_data as privacy

_FAKE_VALUES = [
    ("employee name 'nguyen van fake'", "Nguyễn Văn Fake"),
    ("a spreadsheet ID from Employees.gs", "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcd"),
    ("SHEETS_SYNC_SECRET", "s3cr3t-Value-For-Tests"),
]


def test_denylist_export_holds_no_value_and_no_name():
    blob = json.dumps(privacy.export_denylist(_FAKE_VALUES))
    for _label, value in _FAKE_VALUES:
        assert value not in blob and privacy.norm(value) not in blob
    assert "fake" not in blob.lower()
    assert [e["label"] for e in json.loads(blob)["entries"]] == [
        "employee name #1", "spreadsheet ID #1", "SHEETS_SYNC_SECRET"]


def test_denylist_catches_planted_values_however_written():
    deny = privacy.export_denylist(_FAKE_VALUES)
    text = ("Call notes for NGUYEN VAN FAKE (accents dropped, capitals)\n"
            "sheet: https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcd/edit\n"
            "SECRET = 's3cr3t-Value-For-Tests'\n")
    assert privacy.denylist_hits(text, privacy.norm(text), deny) == [
        "SHEETS_SYNC_SECRET", "employee name #1", "spreadsheet ID #1"]


def test_denylist_passes_clean_text():
    deny = privacy.export_denylist(_FAKE_VALUES)
    text = "Nguyen Van A & 2026-06-01: fictional example, nothing private here."
    assert privacy.denylist_hits(text, privacy.norm(text), deny) == []


def _gate(argv, files, denylist=None):
    """Run the gate on temp files, as on a PC or CI without the private files."""
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as st:
        root = Path(tmp)
        for name, text in files.items():
            (root / name).write_text(text, encoding="utf-8")
        st.enter_context(mock.patch.object(privacy, "ROOT", root))
        st.enter_context(mock.patch.object(privacy, "publishable_files",
                                           return_value=list(files)))
        st.enter_context(mock.patch.dict(os.environ, {"PRIVATE_DENYLIST": denylist or ""}))
        out = st.enter_context(mock.patch("builtins.print"))
        code = privacy.main(argv)
        return code, " ".join(str(c.args[0]) for c in out.call_args_list)


def test_gate_fails_closed_without_private_files_or_denylist():
    code, said = _gate(["--fail-closed"], {"notes.md": "harmless"})
    assert code == 1 and "FAILED CLOSED" in said
    # The local git hooks don't pass --fail-closed: a note, then OK.
    code, said = _gate([], {"notes.md": "harmless"})
    assert code == 0 and "note:" in said


def test_gate_with_denylist_blocks_a_planted_name_in_a_tracked_file():
    deny = json.dumps(privacy.export_denylist(_FAKE_VALUES))
    code, said = _gate(["--fail-closed"], {"notes.md": "Met nguyen van fake today."}, deny)
    assert code == 1 and "notes.md: contains employee name #1 (denylist)" in said
    assert "fake" not in said.lower().replace("notes.md", "")   # the value itself isn't printed
    code, _ = _gate(["--fail-closed"], {"notes.md": "Nothing private."}, deny)
    assert code == 0


def test_gate_refuses_a_malformed_denylist():
    code, said = _gate(["--fail-closed"], {"notes.md": "x"}, '{"format": "something else"}')
    assert code == 1 and "can't be used" in said


# ---------------------------------------------------------------------------
# Folder names (cloud/naming.py, AD-8): one parser for upload, processing,
# resync and import. All names are fictional.
# ---------------------------------------------------------------------------

import datetime as _dt
import re as _re
import tempfile
import unicodedata
from pathlib import Path

import openpyxl

import main
import resync_output
from cloud import naming

# Expected values from the real norm_() in apps_script/WebAppSync.gs, run under
# Node 24 on 2026-10-15 (the script is in the Story 3.3 spec). They pin the
# edges where Python's defaults differ from JavaScript's: U+0085 and U+001F are
# not JS whitespace, U+FEFF is, and only U+0300-U+036F marks are dropped.
_NORM_FROM_WEBAPP = [
    ("Nguyen Van A", "nguyen van a"),
    ("Nguyễn Văn A", "nguyen van a"),
    ("Nguyễn Văn A", "nguyen van a"),
    ("TRẦN THỊ B", "tran thi b"),
    ("Đỗ Văn C", "do van c"),
    ("  Lê   Thị\tD  ", "le thi d"),
    ("Phạm Văn　E", "pham van e"),
    ("﻿Hoàng F", "hoang f"),
    ("Ngô Gia", "ngo gia"),
    ("Bùi\u0085X", "bui\u0085x"),
    ("Vũ\u001fY", "vu\u001fy"),
    ("ガギグ", "ガギグ"),
    ("a᷀b", "a᷀b"),
    ("İstanbul", "istanbul"),
    ("ΟΔΥΣΣΕΥΣ", "οδυσσευς"),
    ("", ""),
    ("Nguyen Van A & 2026-06-01", "nguyen van a & 2026-06-01"),
]


@pytest.mark.parametrize("text,expected", _NORM_FROM_WEBAPP)
def test_naming_norm_matches_the_web_app(text, expected):
    assert naming.norm(text) == expected


@pytest.mark.parametrize("folder,name,date", [
    ("Nguyen Van A & 2026-06-01", "Nguyen Van A", "2026-06-01"),
    ("Nguyen Van A & 2026-6-1", "Nguyen Van A", "2026-06-01"),       # unpadded, as main accepts
    ("Nguyen Van A 2026-06-01", "Nguyen Van A", "2026-06-01"),       # no "&"
    ("Nguyễn Văn A - 2026-06-01", "Nguyễn Văn A", "2026-06-01"),
    ("Tran Thi B & 2028-02-29", "Tran Thi B", "2028-02-29"),         # leap day
])
def test_naming_parses_the_folder_names_main_accepts(folder, name, date):
    day = naming.parse(folder)
    assert (day.name, day.date, day.key) == (name, date, f"{name} & {date}")
    assert (day.name, day.date) == main.parse_name_date(folder)


@pytest.mark.parametrize("folder", [
    "Nguyen Van A & 2026-02-30", "Nguyen Van A & 2026-13-01", "Tran Thi B & 2027-02-29",
    "Nguyen Van A", "& 2026-06-01", "",
])
def test_naming_rejects_impossible_or_missing_parts(folder):
    assert naming.parse(folder) is None


def test_naming_nfc_and_nfd_give_one_person_day():
    nfc = "Nguyễn Văn A & 2026-06-01"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc
    assert naming.parse(nfd) == naming.parse(nfc)
    assert naming.parse(nfd).key == nfc   # keys are NFC


def test_naming_prefix_match_is_the_web_apps():
    keys = ["nguyen van a", "nguyen van", "tran thi b"]   # stored order matters
    assert naming.matching_prefix("Nguyễn Văn A", keys) == "nguyen van a"
    assert naming.matching_prefix("NGUYEN VAN AN", keys) == "nguyen van a"   # prefix, first key wins
    assert naming.matching_prefix("Nguyen Van Binh", keys) == "nguyen van"
    assert naming.matching_prefix("Trần Thị B (2)", keys) == "tran thi b"
    assert naming.matching_prefix("Le Van C", keys) is None
    assert naming.matching_prefix("Nguyen Van A", ["Nguyen Van A"]) is None   # keys aren't folded, as in matchId_


def test_naming_prefixes_json_must_be_a_list_of_names():
    assert naming.prefixes_from_json('["nguyen van a", "tran thi b"]') == ["nguyen van a", "tran thi b"]
    for bad in ('{"a": 1}', '["ok", 3]', '["ok", ""]', '"unset"'):
        try:
            naming.prefixes_from_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


@pytest.mark.parametrize("stem,expected", [
    ("Nguyen Van A & 2026-06-01", ("Nguyen Van A", "2026-06-01", 0)),
    ("Nguyen Van A & 2026-06-01(3)", ("Nguyen Van A", "2026-06-01", 3)),
    ("Nguyen Van A & 2026-6-1", ("Nguyen Van A", "2026-06-01", 0)),
    ("Nguyen Van A & 2026-06-01 redo", None),
    ("Nguyen Van A & 2026-02-30", None),
    ("Report 2026-06-01", None),
])
def test_naming_reads_day_sheet_file_names(stem, expected):
    got = naming.parse_output_stem(stem)
    assert (got and (got[0].name, got[0].date, got[1])) == expected


def test_naming_keys_and_upload_ids():
    when = _dt.datetime(2026, 6, 1, 8, 30, 5, tzinfo=_dt.timezone.utc)
    uid = naming.new_upload_id(when)
    assert uid.startswith("20260601T083005Z-") and naming.is_upload_id(uid)
    assert not naming.is_upload_id("../results") and not naming.is_upload_id(uid + "x")
    day = naming.parse("Nguyen Van A & 2026-06-01")
    assert naming.result_key(day) == "results/Nguyen Van A & 2026-06-01.xlsx"
    assert naming.inbox_key(uid, "shot.jpg") == f"inbox/{uid}/shot.jpg"
    assert naming.work_key(uid, "shot.jpg") == f"work/{uid}/shot.jpg.json"


# resync_output.py reads file names through cloud/naming.py since Story 3.3.

_OLD_RESYNC_RE = _re.compile(r"^(?P<name>.+) & (?P<date>\d{4}-\d{2}-\d{2})(?:\((?P<dup>\d+)\))?$")


def _old_collect(stems):
    """resync_output.collect_files before Story 3.3, for comparison."""
    chosen = {}
    for stem in sorted(stems):
        m = _OLD_RESYNC_RE.match(stem)
        if m:
            key, rank = (m.group("name"), m.group("date")), int(m.group("dup") or 0)
            if key not in chosen or rank < chosen[key][0]:
                chosen[key] = (rank, stem)
    return {k: v[1] for k, v in chosen.items()}


def _resync_collect(stems):
    with tempfile.TemporaryDirectory() as d:
        for stem in stems:
            (Path(d) / f"{stem}.xlsx").write_bytes(b"")
        with mock.patch.object(resync_output, "log"):
            return {k: p.stem for k, p in resync_output.collect_files(Path(d)).items()}


def test_resync_choice_unchanged_for_correctly_named_files():
    stems = ["Nguyen Van A & 2026-06-01", "Nguyen Van A & 2026-06-01(1)",
             "Tran Thi B & 2026-06-02(2)", "Tran Thi B & 2026-06-02(1)",
             "Le Van C & 2026-06-03(1)"]
    assert _resync_collect(stems) == _old_collect(stems)


def test_resync_reads_unpadded_dates_and_skips_bad_names():
    got = _resync_collect(["Pham Van D & 2026-6-3", "Nguyen Van A & 2026-02-30",
                           "Report 2026-06-01 backup", "~$Nguyen Van A & 2026-06-01"])
    assert got == {("Pham Van D", "2026-06-03"): "Pham Van D & 2026-6-3"}


def test_resync_merges_nfc_and_nfd_names_of_one_day():
    nfc = "Nguyễn Văn E & 2026-06-04"
    got = _resync_collect([unicodedata.normalize("NFD", nfc) + "(1)", nfc])
    assert got == {("Nguyễn Văn E", "2026-06-04"): nfc}


def test_resync_reads_the_configured_sheet_not_the_active_one():
    wb = openpyxl.Workbook()
    other = wb.active
    other.title = "Notes"
    other.cell(CONFIG.first_session_row, 2).value = "07:00"
    other.cell(CONFIG.first_session_row, 3).value = "07:30"
    ws = wb.create_sheet(CONFIG.sheet_name)
    ws.cell(CONFIG.first_session_row, 2).value = "08:00"
    ws.cell(CONFIG.first_session_row, 3).value = "10:01"
    wb.active = 0   # the "Notes" sheet is the active one
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "Nguyen Van A & 2026-06-01.xlsx"
        wb.save(path)
        sessions = resync_output.read_sessions(path)
    assert [(s.start_time, s.end_time) for s in sessions] == [("08:00", "10:01")]


def test_core_modules_never_import_cloud():
    """AD-1: dependencies point only from cloud/ into the core."""
    core = ["main", "image_preprocess", "ocr", "parser", "session_builder", "excel_writer",
            "sheets_sync", "config", "runtime_env", "review_report"]
    root = Path(__file__).resolve().parent
    for name in core:
        text = (root / f"{name}.py").read_text(encoding="utf-8")
        assert not _re.search(r"^\s*(from|import)\s+cloud\b", text, _re.M), f"{name}.py imports cloud"
