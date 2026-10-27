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
# Parameter tool (tools/put_parameters.py): real values reach AWS only through
# a temporary file, are never printed, and nothing is written when a private
# file is missing. The AWS CLI is faked; all values are fictional.
# ---------------------------------------------------------------------------

import io
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from tools import put_parameters

_PP_SECRET = "s3cr3t-Value-For-Tests"
_PP_URL = "https://script.google.com/macros/s/FICTIONAL-DEPLOYMENT/exec"


def _pp_private_files(folder):
    secrets = Path(folder) / "secrets_local.py"
    secrets.write_text(f'SHEETS_WEBAPP_URL = "{_PP_URL}"\nSHEETS_SYNC_SECRET = "{_PP_SECRET}"\n',
                       encoding="utf-8")
    employees = Path(folder) / "Employees.gs"
    employees.write_text("var NAME_TO_ID = {\n  'Nguyen Van A': 'id-a',\n  'Tran Thi B': 'id-b',\n"
                         "  'Nguyen Van A': 'id-a',\n};\n", encoding="utf-8")
    return secrets, employees


class _FakeAws:
    """Stands in for subprocess.run: records each call and its JSON input file."""

    def __init__(self, fail_with="", current="unset"):
        self.calls, self.inputs, self.files, self.fail_with = [], [], [], fail_with
        self.current = current   # what ssm get-parameter answers

    def __call__(self, cmd, capture_output, text):
        self.calls.append(cmd)
        if "--cli-input-json" in cmd:
            path = Path(cmd[cmd.index("--cli-input-json") + 1].removeprefix("file://"))
            self.files.append(path)
            self.inputs.append(json.loads(path.read_text(encoding="utf-8")))
        if cmd[1:3] == ["sts", "get-caller-identity"]:
            return SimpleNamespace(returncode=0, stdout="123456789012\n", stderr="")
        if cmd[1:3] == ["ssm", "get-parameter"] and not self.fail_with:
            return SimpleNamespace(returncode=0, stdout=self.current + "\n", stderr="")
        if self.fail_with:
            return SimpleNamespace(returncode=254, stdout="", stderr=self.fail_with)
        return SimpleNamespace(returncode=0, stdout="1\n", stderr="")


def _pp_main(argv, run):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = put_parameters.main(argv, run=run)
    return code, out.getvalue() + err.getvalue()


def test_put_parameters_reads_private_files_in_order():
    with tempfile.TemporaryDirectory() as d:
        values = put_parameters.read_private_files(*_pp_private_files(d))
    assert values["webapp_url"] == _PP_URL and values["sync_secret"] == _PP_SECRET
    # NAME_TO_ID order is kept (the first matching key wins), duplicates dropped.
    assert json.loads(values["name_prefixes"]) == ["Nguyen Van A", "Tran Thi B"]


def test_put_parameters_sends_values_by_file_and_never_prints_them():
    fake = _FakeAws()
    with tempfile.TemporaryDirectory() as d:
        secrets, employees = _pp_private_files(d)
        code, said = _pp_main(["--from-private-files", "--secrets", str(secrets),
                               "--employees", str(employees), "--sync-enabled", "false"], fake)
    assert code == 0
    assert [i["Name"] for i in fake.inputs] == [
        "/calls-log/webapp_url", "/calls-log/sync_secret", "/calls-log/name_prefixes",
        "/calls-log/sync_enabled"]
    assert all(i["Type"] == "SecureString" and i["Overwrite"] is True for i in fake.inputs)
    for cmd in fake.calls:
        assert _PP_SECRET not in " ".join(cmd) and _PP_URL not in " ".join(cmd)
    assert _PP_SECRET not in said and _PP_URL not in said and "Tran Thi B" not in said
    assert "2 names, in Employees.gs order" in said
    assert not any(p.exists() for p in fake.files)   # the temporary files are gone


def test_put_parameters_writes_nothing_when_a_private_file_is_missing():
    fake = _FakeAws()
    with tempfile.TemporaryDirectory() as d:
        code, said = _pp_main(["--from-private-files", "--secrets", str(Path(d) / "none.py"),
                               "--employees", str(Path(d) / "none.gs")], fake)
    assert code == 2 and "nothing written" in said and fake.calls == []


def test_put_parameters_hides_the_value_when_the_cli_fails():
    fake = _FakeAws(fail_with=f"An error occurred (ValidationException): value {_PP_SECRET} rejected")
    with tempfile.TemporaryDirectory() as d:
        secrets, employees = _pp_private_files(d)
        code, said = _pp_main(["--from-private-files", "--secrets", str(secrets),
                               "--employees", str(employees)], fake)
    assert code == 1 and "ValidationException" in said and _PP_SECRET not in said


def test_put_parameters_alert_email_is_stored_and_subscribed():
    fake = _FakeAws()
    code, said = _pp_main(["--alert-email", "owner@example.com"], fake)
    assert code == 0
    assert fake.inputs[0] == {"Name": "/calls-log/alert_email", "Value": "owner@example.com",
                              "Type": "SecureString", "Overwrite": True}
    assert fake.inputs[1] == {"TopicArn": "arn:aws:sns:ap-southeast-2:123456789012:calls-log-alerts",
                              "Protocol": "email", "Endpoint": "owner@example.com"}
    assert "owner@example.com" not in said


def test_put_parameters_refuses_bad_usage():
    fake = _FakeAws()
    assert _pp_main([], fake)[0] == 2
    assert _pp_main(["--alert-email", "not an address"], fake)[0] == 2
    assert fake.calls == []


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

# Expected values from the real norm_() in apps_script/WebAppSync.gs:
# tools/check_norm_parity.js runs it under Node on the same cases (CI does too).
# They pin the edges where Python's defaults differ from JavaScript's: U+0085
# and U+001F are not JS whitespace, U+FEFF is, and only U+0300-U+036F marks are
# dropped.
_NORM_FROM_WEBAPP = [tuple(c) for c in json.loads(
    (Path(__file__).resolve().parent / "tools" / "norm_cases.json").read_text(encoding="utf-8"))]


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


# ---------------------------------------------------------------------------
# Run-time settings (cloud/settings.py, AD-9 and AD-10) and config.py's
# environment path. SSM is faked; every value is fictional.
# ---------------------------------------------------------------------------

import dataclasses
import logging
import sys
import types

import config as config_module
import sheets_sync
from cloud import settings

_ST_SECRET = "s3cr3t-Value-For-Tests"
_ST_URL = "https://script.google.com/macros/s/FICTIONAL-DEPLOYMENT/exec"


class _FakeSsm:
    def __init__(self, values, switch="false", lake="false"):
        self.values, self.switch, self.lake = values, switch, lake
        self.batch_calls = self.switch_calls = self.lake_calls = 0

    def get_parameters(self, Names, WithDecryption):
        assert WithDecryption is True
        self.batch_calls += 1
        found = [{"Name": n, "Value": self.values[n.removeprefix("/calls-log/")]}
                 for n in Names if n.removeprefix("/calls-log/") in self.values]
        return {"Parameters": found,
                "InvalidParameters": [n for n in Names if n.removeprefix("/calls-log/") not in self.values]}

    def get_parameter(self, Name, WithDecryption):
        assert Name in ("/calls-log/sync_enabled", "/calls-log/lake_enabled") and WithDecryption is True
        if Name == "/calls-log/lake_enabled":
            self.lake_calls += 1
            return {"Parameter": {"Name": Name, "Value": self.lake}}
        self.switch_calls += 1
        return {"Parameter": {"Name": Name, "Value": self.switch}}


_ST_VALUES = {
    "webapp_url": _ST_URL, "sync_secret": _ST_SECRET,
    "name_prefixes": '["nguyen van a", "tran thi b"]',
    "intake_passphrase_hash": "unset", "alert_email": "owner@example.com",
}
_ST_ENV = {"CALLS_LOG_BUCKET": "calls-log-data-123456789012", "CALLS_LOG_TABLE": "calls-log-folders"}


def test_settings_are_read_once_per_container():
    ssm = _FakeSsm(_ST_VALUES)
    settings.reset_cache()
    try:
        first = settings.load(ssm, _ST_ENV)
        again = settings.load(ssm, {})
    finally:
        settings.reset_cache()
    assert ssm.batch_calls == 1 and again is first
    assert (first.bucket, first.table, first.queue_url) == ("calls-log-data-123456789012", "calls-log-folders", "")
    assert first.webapp_url == _ST_URL and first.sync_secret == _ST_SECRET
    assert first.name_prefixes == ("nguyen van a", "tran thi b")
    assert first.intake_passphrase_hash == ""   # Terraform's placeholder counts as empty


def test_settings_are_read_again_after_fifteen_minutes():
    ssm, now = _FakeSsm(_ST_VALUES), [1000.0]
    settings.reset_cache()
    try:
        first = settings.load(ssm, _ST_ENV, clock=lambda: now[0])
        now[0] += settings.CACHE_SECONDS
        assert settings.load(ssm, _ST_ENV, clock=lambda: now[0]) is first and ssm.batch_calls == 1
        ssm.values = dict(_ST_VALUES, sync_secret="a-rotated-secret")
        now[0] += 1
        assert settings.load(ssm, _ST_ENV, clock=lambda: now[0]).sync_secret == "a-rotated-secret"
    finally:
        settings.reset_cache()
    assert ssm.batch_calls == 2


def test_settings_missing_parameters_are_empty():
    s = settings.read(_FakeSsm({"webapp_url": "unset"}), {})
    assert (s.webapp_url, s.sync_secret, s.name_prefixes) == ("", "", ())


def test_sync_switch_is_read_fresh_each_run():
    ssm = _FakeSsm(_ST_VALUES, switch="false")
    assert settings.sync_enabled(ssm) is False
    ssm.switch = " TRUE "
    assert settings.sync_enabled(ssm) is True
    ssm.switch = "yes"   # only "true" turns it on
    assert settings.sync_enabled(ssm) is False
    assert ssm.switch_calls == 3


def test_config_environment_wins_over_secrets_local():
    local = types.ModuleType("secrets_local")
    local.SHEETS_WEBAPP_URL, local.SHEETS_SYNC_SECRET = "https://example.invalid/local", "local-secret"
    env = {"SHEETS_WEBAPP_URL": "https://example.invalid/env", "SHEETS_SYNC_SECRET": "env-secret"}
    with mock.patch.dict(sys.modules, {"secrets_local": local}):
        assert config_module._credentials(env) == ("https://example.invalid/env", "env-secret")
        assert config_module._credentials({}) == ("https://example.invalid/local", "local-secret")
    with mock.patch.dict(sys.modules, {"secrets_local": None}):   # no file: import fails
        assert config_module._credentials({}) == ("", "")


def test_sync_with_no_credentials_raises_its_existing_error():
    target = config_module.Config()
    target.sheets_webapp_url, target.sheets_sync_secret = "", ""
    settings.configure_core(settings.Settings(), sync_on=True, config=target)
    with mock.patch.object(sheets_sync, "CONFIG", target):
        try:
            sheets_sync.sync_day("Nguyen Van A", "2026-06-01", [])
        except sheets_sync.SheetsSyncError as e:
            assert "sheets_webapp_url is empty" in str(e)
        else:
            raise AssertionError("sync_day did not refuse an empty Web App URL")


def test_settings_cannot_change_the_load_bearing_flags():
    env = dict(_ST_ENV, CALLS_LOG_OCR_STRICT_ENGINE="false", CALLS_LOG_PADDLE_ENABLE_MKLDNN="true",
               CALLS_LOG_POST_9AM_EXCEPTION_LIMIT="99", OCR_STRICT_ENGINE="false",
               PADDLE_ENABLE_MKLDNN="true", POST_9AM_EXCEPTION_LIMIT="99")
    before = config_module.Config()
    target = dataclasses.replace(before)
    with mock.patch.dict(os.environ, env):
        s = settings.read(_FakeSsm(_ST_VALUES), env)
        settings.configure_core(s, sync_on=True, config=target)
    changed = {f.name for f in dataclasses.fields(before) if getattr(before, f.name) != getattr(target, f.name)}
    assert changed <= {"sheets_webapp_url", "sheets_sync_secret", "sheets_sync_enabled"}
    assert (target.ocr_strict_engine, target.paddle_enable_mkldnn, target.post_9am_exception_limit) == (True, False, 4)
    assert (target.sheets_webapp_url, target.sheets_sync_secret, target.sheets_sync_enabled) == (_ST_URL, _ST_SECRET, True)


def test_settings_never_log_secrets_or_names():
    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Keep(level=logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    settings.reset_cache()
    try:
        s = settings.load(_FakeSsm(_ST_VALUES), _ST_ENV)
        settings.configure_core(s, sync_on=False, config=config_module.Config())
        text = "\n".join(records) + repr(s) + str(s)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        settings.reset_cache()
    assert "settings loaded" in text
    for private in (_ST_SECRET, _ST_URL, "nguyen van a", "tran thi b", "owner@example.com"):
        assert private not in text, f"{private!r} reached a log line or repr"


# ---------------------------------------------------------------------------
# Folder Status (cloud/status.py, AD-4 to AD-6). DynamoDB is faked with a
# small in-memory table that evaluates the expressions status.py sends and,
# like DynamoDB, refuses undefined or unused #names and :values.
# ---------------------------------------------------------------------------

import copy
import itertools

from cloud import status as fstatus


class _CondFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class _FakeDynamo:
    def __init__(self):
        self.items = {}
        self.writes = 0

    @staticmethod
    def _k(key):
        return key["pk"]["S"], key["sk"]["S"]

    @staticmethod
    def _v(typed):
        return int(typed["N"]) if "N" in typed else typed["S"]

    @staticmethod
    def _check_placeholders(exprs, names, values):
        text = " ".join(e for e in exprs if e)
        used_n, used_v = set(_re.findall(r"#\w+", text)), set(_re.findall(r":\w+", text))
        assert used_n == set(names), f"names used {sorted(used_n)} vs given {sorted(names)}"
        assert used_v == set(values), f"values used {sorted(used_v)} vs given {sorted(values)}"

    def _atom(self, atom, item, names, values):
        if atom == "attribute_not_exists(pk)":
            return item is None
        if item is None:
            return False
        m = _re.fullmatch(r"(#\w+) IN \((.*)\)", atom)
        if m:
            return item.get(names[m.group(1)]) in [self._v(values[v.strip()]) for v in m.group(2).split(",")]
        m = _re.fullmatch(r"(#\w+) (<|>=|=) (:\w+)", atom)
        assert m, f"unsupported condition {atom!r}"
        have, want = item.get(names[m.group(1)]), self._v(values[m.group(3)])
        if have is None:
            return False
        return {"<": have < want, ">=": have >= want, "=": have == want}[m.group(2)]

    def _holds(self, condition, item, names, values):
        return any(all(self._atom(a.strip(), item, names, values) for a in alt.split(" AND "))
                   for alt in condition.split(" OR "))

    def get_item(self, TableName, Key, ConsistentRead=False):
        item = self.items.get(self._k(Key))
        if item is None:
            return {}
        return {"Item": {k: ({"N": str(v)} if isinstance(v, int) else {"S": v}) for k, v in item.items()}}

    def put_item(self, TableName, Item, ConditionExpression):
        self._check_placeholders([ConditionExpression], {}, {})
        key = self._k(Item)
        if not self._holds(ConditionExpression, self.items.get(key), {}, {}):
            raise _CondFailed()
        self.writes += 1
        self.items[key] = {k: self._v(v) for k, v in Item.items()}

    def update_item(self, TableName, Key, UpdateExpression, ConditionExpression,
                    ExpressionAttributeNames, ExpressionAttributeValues, ReturnValues=None):
        names, values = ExpressionAttributeNames, ExpressionAttributeValues
        self._check_placeholders([UpdateExpression, ConditionExpression], names, values)
        key = self._k(Key)
        current = self.items.get(key)
        if not self._holds(ConditionExpression, current, names, values):
            raise _CondFailed()
        item = copy.deepcopy(current) if current else {"pk": key[0], "sk": key[1]}
        set_part, _, remove_part = UpdateExpression.partition(" REMOVE ")
        for clause in set_part.removeprefix("SET ").split(", "):
            target, expr = (s.strip() for s in clause.split(" = "))
            m = _re.fullmatch(r"(#\w+) \+ (:\w+)", expr)
            item[names[target]] = (item[names[m.group(1)]] + self._v(values[m.group(2)])) if m \
                else self._v(values[expr])
        for name in filter(None, (s.strip() for s in remove_part.split(","))):
            item.pop(names[name], None)
        self.writes += 1
        self.items[key] = item
        return {"Attributes": {k: ({"N": str(v)} if isinstance(v, int) else {"S": v}) for k, v in item.items()}}

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues, ConsistentRead=False,
              ExclusiveStartKey=None, IndexName=None):
        if IndexName is not None:   # the uploads-by-day index (Epic 4): upload items carrying upDay
            assert IndexName == "uploads-by-day" and KeyConditionExpression == "upDay = :d"
            assert not ConsistentRead, "DynamoDB refuses consistent reads on a global secondary index"
            day = ExpressionAttributeValues[":d"]["S"]
            chosen = [item for _, item in sorted(self.items.items(), key=lambda kv: kv[0][1])
                      if item.get("upDay") == day]
        else:
            assert KeyConditionExpression == "pk = :pk AND begins_with(sk, :up)", KeyConditionExpression
            pk, up = ExpressionAttributeValues[":pk"]["S"], ExpressionAttributeValues[":up"]["S"]
            chosen = [item for (p, sk), item in sorted(self.items.items()) if p == pk and sk.startswith(up)]
        found = [{k: ({"N": str(v)} if isinstance(v, int) else {"S": v}) for k, v in item.items()}
                 for item in chosen]
        start = int(ExclusiveStartKey["n"]["N"]) if ExclusiveStartKey else 0   # pages of 1
        out = {"Items": found[start:start + 1]}
        if start + 1 < len(found):
            out["LastEvaluatedKey"] = {"n": {"N": str(start + 1)}}
        return out

    def delete_item(self, TableName, Key, ConditionExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        self._check_placeholders([ConditionExpression], ExpressionAttributeNames, ExpressionAttributeValues)
        key = self._k(Key)
        if not self._holds(ConditionExpression, self.items.get(key), ExpressionAttributeNames,
                           ExpressionAttributeValues):
            raise _CondFailed()
        self.writes += 1
        del self.items[key]


_PD, _UP = "Nguyen Van A & 2026-06-01", "20260601T083005Z-0a1b2c3d"


def _store(clock=None):
    now = [1_780_000_000.0]
    return fstatus.FolderStatus(_FakeDynamo(), "calls-log-folders", clock or (lambda: now[0])), now


def _at(state):
    """A store holding one upload in the given state (attempts 0)."""
    store, now = _store()
    store.create(_PD, _UP, 12)
    key = ("pd#" + _PD, "up#" + _UP)
    store.client.items[key]["status"] = state.value
    return store, now, key


_ALLOWED = {   # (operation, from state) pairs the AD-4 diagram allows
    ("mark_ready", fstatus.Status.UPLOADING),
    ("begin_run", fstatus.Status.PENDING), ("begin_run", fstatus.Status.FAILED),
    ("begin_run", fstatus.Status.PROCESSING),   # a Run that timed out, its lease expired
    ("finish_ok", fstatus.Status.PROCESSING), ("finish_error", fstatus.Status.PROCESSING),
    ("requeue", fstatus.Status.DONE), ("requeue", fstatus.Status.FAILED), ("requeue", fstatus.Status.PARKED),
    ("requeue", fstatus.Status.HELD), ("requeue", fstatus.Status.SUPERSEDED),   # Story 4.6: a Re-run
}
_OPS = {
    "mark_ready": lambda s: s.mark_ready(_PD, _UP),
    "begin_run": lambda s: s.begin_run(_PD, _UP, "run-1"),
    "finish_ok": lambda s: s.finish(_PD, _UP),
    "finish_error": lambda s: s.finish(_PD, _UP, "zero sessions"),
    "requeue": lambda s: s.requeue(_PD, _UP),
}


@pytest.mark.parametrize("op,state", list(itertools.product(sorted(_OPS), list(fstatus.Status))))
def test_status_only_the_ad4_transitions_succeed(op, state):
    store, _, key = _at(state)
    before = copy.deepcopy(store.client.items[key])
    if (op, state) in _ALLOWED:
        _OPS[op](store)
        assert store.client.items[key]["status"] != state.value or op == "begin_run"
    else:
        try:
            _OPS[op](store)
        except fstatus.TransitionRefused:
            pass
        else:
            raise AssertionError(f"{op} from {state.value} was allowed")
        assert store.client.items[key] == before, "a refused change altered the item"
        assert ("pd#" + _PD, "lease") not in store.client.items, "a refused start kept the lease"


def test_status_create_twice_is_refused():
    store, _ = _store()
    store.create(_PD, _UP, 12)
    try:
        store.create(_PD, _UP, 3)
    except fstatus.TransitionRefused:
        assert store.get(_PD, _UP)["imageCount"] == 12
    else:
        raise AssertionError("an existing upload was overwritten")


def test_status_second_run_cannot_take_the_lease_or_count_an_attempt():
    store, now, key = _at(fstatus.Status.PENDING)
    assert store.begin_run(_PD, _UP, "run-1")["attempts"] == 1
    try:
        store.begin_run(_PD, _UP, "run-2")
    except fstatus.Busy:
        pass
    else:
        raise AssertionError("two Runs held one Person-Day")
    assert store.get(_PD, _UP)["attempts"] == 1
    now[0] += fstatus.LEASE_SECONDS + 1   # run-1 timed out; its lease has expired
    item = store.begin_run(_PD, _UP, "run-2")
    assert (item["status"], item["attempts"]) == ("Processing", 2)


def test_status_counts_attempts_first_and_parks_at_the_fourth_start():
    store, now, key = _at(fstatus.Status.PENDING)
    for run in (1, 2, 3):
        item = store.begin_run(_PD, _UP, f"run-{run}")
        assert (item["status"], item["attempts"]) == ("Processing", run)   # counted before any work
        store.finish(_PD, _UP, f"error in run {run}")
        store.release_lease(_PD, f"run-{run}")
    item = store.begin_run(_PD, _UP, "run-4")
    assert (item["status"], item["attempts"]) == ("Parked", 4)
    assert ("pd#" + _PD, "lease") not in store.client.items   # a parked Run doesn't keep the lease
    assert store.get(_PD, _UP)["lastError"] == "error in run 3"
    try:
        store.begin_run(_PD, _UP, "run-5")
    except fstatus.TransitionRefused:
        pass
    else:
        raise AssertionError("a Parked Folder ran again")
    item = store.requeue(_PD, _UP)
    assert (item["status"], item["attempts"]) == ("Pending", 0) and "lastError" not in item


def test_status_lease_is_released_only_by_its_holder():
    store, now = _store()
    assert store.take_lease(_PD, "run-1")
    store.release_lease(_PD, "run-2")   # not the holder: nothing happens
    assert not store.take_lease(_PD, "run-2")
    store.release_lease(_PD, "run-1")
    assert store.take_lease(_PD, "run-2")


def test_status_done_clears_the_error_and_records_times():
    store, now, key = _at(fstatus.Status.PENDING)
    store.begin_run(_PD, _UP, "run-1")
    store.finish(_PD, _UP, "Web App timed out")
    now[0] += 60
    store.begin_run(_PD, _UP, "run-1")
    item = store.finish(_PD, _UP)
    assert item["status"] == "Done" and "lastError" not in item
    assert item["updatedAt"] == "2026-05-28T20:27:40Z" and item["createdAt"] == "2026-05-28T20:26:40Z"


# ---------------------------------------------------------------------------
# The runner (cloud/runner.py, Stories 3.5 and 3.6). S3, DynamoDB, SSM and
# SNS are faked; OCR (main.extract_calls) is stubbed per image, and the real
# dedupe, session rules and xlsx writer run. urlopen fails the test if
# reached, so nothing can touch a live Sheet. All values are fictional.
# ---------------------------------------------------------------------------

import io as _io

from cloud import runner, storage as cstorage
from cloud.status import FolderStatus


class _NoKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class _FakeS3:
    """A versioned bucket in memory; lists in pages of 2 to exercise paging."""

    def __init__(self):
        self.objects, self.versions = {}, {}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]
        out = {"Contents": [{"Key": k} for k in page], "IsTruncated": start + 2 < len(keys)}
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(start + 2)
        return out

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[key])

    def put_object(self, Bucket, Key, Body, ContentType):
        data = Body if isinstance(Body, bytes) else Body.read()
        self.objects[Key] = data
        self.versions.setdefault(Key, []).append(data)
        return {"VersionId": str(len(self.versions[Key]))}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoKey()
        return {"Body": _io.BytesIO(self.objects[Key])}


class _FakeSns:
    def __init__(self):
        self.sent = []

    def publish(self, **kwargs):
        self.sent.append(kwargs)


_RUN_PD = "Nguyen Van A & 2026-06-01"
_RUN_UP = "20260601T083005Z-0a1b2c3d"
_PHONE = "090-1234-5678"   # fictional; OCR text must never reach a log line
_BY_IMAGE = {
    "Shot_01.jpg": [_sess("08:00", "08:20", "Shot_01.jpg"), _sess("08:23", "09:00", "Shot_01.jpg")],
    "shot_02.jpg": [_sess("10:00", "10:30", "shot_02.jpg")],
    "shot_03.png": [_sess("10:33", "11:00", "shot_03.png")],
}
for _recs in _BY_IMAGE.values():
    for _r in _recs:
        _r.raw_text = f"{_PHONE} 発信 {_r.start_time}"


class _Rig:
    """Everything one Run touches, faked, with the upload Pending."""

    def __init__(self, by_image=None, switch="false", lake="false"):
        self.by_image = dict(_BY_IMAGE if by_image is None else by_image)
        self.s3, self.sns = _FakeS3(), _FakeSns()
        self.ssm = _FakeSsm({}, switch=switch, lake=lake)
        self.status = FolderStatus(_FakeDynamo(), "calls-log-folders", lambda: 1_780_000_000.0)
        self.status.create(_RUN_PD, _RUN_UP, len(self.by_image))
        self.status.mark_ready(_RUN_PD, _RUN_UP)
        for name in list(self.by_image) + ["notes.txt"]:
            self.s3.objects[f"inbox/{_RUN_UP}/{name}"] = b"placeholder, never decoded"
        self.settings = settings.Settings(
            bucket="calls-log-data-123456789012", table="calls-log-folders",
            alerts_topic_arn="arn:aws:sns:ap-southeast-2:123456789012:calls-log-alerts",
            webapp_url=_ST_URL, sync_secret=_ST_SECRET)
        self.ocr_calls, self.written, self.fail_on = [], [], None

    def extract_calls(self, files):
        assert len(files) == 1, "the runner OCRs one image at a time"
        name = files[0].name
        self.ocr_calls.append(name)
        if name == self.fail_on:
            raise TimeoutError("simulated cut-off")
        return [copy.deepcopy(r) for r in self.by_image[name]], [], {"ocr_failures": 0, "ocr_empty": 0}

    def run(self, sync_effect=None, holder="run-1"):
        logs = []

        class _Keep(logging.Handler):
            def emit(self, record):
                logs.append(record.getMessage())

        keep = _Keep()
        runner.log.addHandler(keep)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as st:
            for k in ("sheets_webapp_url", "sheets_sync_secret", "sheets_sync_enabled"):
                st.enter_context(mock.patch.object(CONFIG, k, getattr(CONFIG, k)))
            st.enter_context(mock.patch.object(runner.pipeline, "extract_calls", self.extract_calls))
            self.sync = st.enter_context(mock.patch.object(runner, "sync_day", side_effect=sync_effect))
            real_write = runner.write_sessions

            def write(template, path, sessions):
                self.written.append([(s.start_time, s.end_time) for s in sessions])
                return real_write(template, path, sessions)

            st.enter_context(mock.patch.object(runner, "write_sessions", write))
            st.enter_context(mock.patch.object(
                sheets_sync.urllib.request, "urlopen",
                side_effect=AssertionError("a test reached the network")))
            st.enter_context(mock.patch.object(excel_writer, "log"))
            self.workdir = Path(tmp)
            try:
                return runner.run_folder(
                    _RUN_UP, _RUN_PD, status=self.status, storage=cstorage.S3Storage(self.s3, "b"),
                    settings=self.settings, ssm=self.ssm, sns=self.sns, holder=holder,
                    workdir=Path(tmp))
            finally:
                runner.log.removeHandler(keep)
                self.logs = logs
                self.left_over = list(Path(tmp).iterdir())

    def item(self):
        return self.status.get(_RUN_PD, _RUN_UP)

    def events(self):
        return [json.loads(line) for line in self.logs if line.startswith("{")]


def test_pc_order_matches_windows_path_sorting():
    assert cstorage.pc_order(["b.jpg", "A.jpg", "a2.JPG", "a10.jpg"]) == ["A.jpg", "a10.jpg", "a2.JPG", "b.jpg"]


def test_runner_processes_a_folder_and_stores_the_day_sheet():
    rig = _Rig()
    outcome = rig.run()
    assert (outcome.status, outcome.images, outcome.sessions) == ("Done", 3, 2)
    assert rig.ocr_calls == ["Shot_01.jpg", "shot_02.jpg", "shot_03.png"]   # the PC's order, images only
    assert rig.written == [[("08:00", "09:00"), ("10:00", "11:00")]]
    assert len(rig.s3.versions["results/Nguyen Van A & 2026-06-01.xlsx"]) == 1
    assert rig.item()["status"] == "Done" and rig.item()["attempts"] == 1
    assert not rig.sync.called   # the AWS Sync Switch is off
    assert ("pd#" + _RUN_PD, "lease") not in rig.status.client.items and rig.left_over == []
    assert [e["status"] for e in rig.events()] == ["Processing", "Done"]


def test_runner_syncs_only_when_the_switch_is_on_and_then_is_done():
    rig = _Rig(switch="true")
    assert rig.run().status == "Done"
    assert rig.sync.call_count == 1
    name, date, sessions = rig.sync.call_args.args
    assert (name, date, [(s.start_time, s.end_time) for s in sessions]) == (
        "Nguyen Van A", "2026-06-01", [("08:00", "09:00"), ("10:00", "11:00")])
    assert CONFIG.sheets_sync_enabled is not None   # restored by the rig


def test_runner_sync_failure_keeps_the_day_sheet_and_fails():
    rig = _Rig(switch="true")
    try:
        rig.run(sync_effect=sheets_sync.SheetsSyncError("Web App timed out"))
    except runner.RunFailed:
        pass
    else:
        raise AssertionError("a failed sync counted as Done")
    item = rig.item()
    assert item["status"] == "Failed" and "Web App timed out" in item["lastError"]
    assert len(rig.s3.versions["results/Nguyen Van A & 2026-06-01.xlsx"]) == 1   # stored before the sync
    assert rig.events()[-1]["dayStored"] is True
    assert ("pd#" + _RUN_PD, "lease") not in rig.status.client.items


def test_runner_zero_sessions_fails_without_a_day_sheet():
    rig = _Rig(by_image={"Shot_01.jpg": [], "shot_02.jpg": []})
    try:
        rig.run()
    except runner.RunFailed as e:
        assert "no sessions extracted from 2 image(s)" in str(e)
    else:
        raise AssertionError("zero sessions counted as Done")
    assert rig.item()["status"] == "Failed"
    assert not any(k.startswith("results/") for k in rig.s3.objects)


def test_runner_waits_when_another_run_holds_the_person_day():
    rig = _Rig()
    assert rig.status.take_lease(_RUN_PD, "someone-else")
    assert rig.run().status == "Busy"
    assert rig.item()["attempts"] == 0 and rig.ocr_calls == []


def test_runner_parks_at_the_fourth_start_and_alerts():
    rig = _Rig()
    key = ("pd#" + _RUN_PD, "up#" + _RUN_UP)
    rig.status.client.items[key].update(status="Failed", attempts=3, lastError="RuntimeError: boom")
    assert rig.run().status == "Parked"
    assert rig.item()["status"] == "Parked" and rig.ocr_calls == []
    (alert,) = rig.sns.sent
    assert alert["TopicArn"].endswith(":calls-log-alerts")
    assert _RUN_PD in alert["Message"] and "RuntimeError: boom" in alert["Message"]


def test_runner_skips_a_duplicate_message_after_done():
    rig = _Rig()
    rig.run()
    assert rig.run(holder="run-2").status == "Skipped"
    assert rig.ocr_calls == ["Shot_01.jpg", "shot_02.jpg", "shot_03.png"]   # nothing OCR'd twice


def test_runner_resumes_from_checkpoints_with_the_same_sessions():
    whole = _Rig()
    whole.run()
    cut = _Rig()
    cut.fail_on = "shot_03.png"   # the Run is cut off at the third image
    try:
        cut.run()
    except runner.RunFailed:
        pass
    assert sorted(k for k in cut.s3.objects if k.startswith("work/")) == [
        f"work/{_RUN_UP}/Shot_01.jpg.json", f"work/{_RUN_UP}/shot_02.jpg.json"]
    cut.fail_on, cut.ocr_calls = None, []
    assert cut.run(holder="run-2").status == "Done"
    assert cut.ocr_calls == ["shot_03.png"]   # only the image without a checkpoint
    assert cut.written == whole.written and cut.item()["attempts"] == 2


def test_runner_logs_json_without_ocr_text_or_secrets():
    rig = _Rig(switch="true")
    try:
        rig.run(sync_effect=sheets_sync.SheetsSyncError(f"bad secret {_ST_SECRET} at {_ST_URL}"))
    except runner.RunFailed as e:
        assert _ST_SECRET not in str(e)
    text = "\n".join(rig.logs)
    assert _PHONE not in text and _ST_SECRET not in text and _ST_URL not in text
    failed = rig.events()[-1]
    assert set(failed) >= {"uploadId", "personDay", "status", "attempt", "durationMs", "images", "error"}
    assert "***" in failed["error"] and "***" in rig.item()["lastError"]


def test_runner_handler_returns_busy_and_failed_messages():
    rig = _Rig()
    calls = []

    def fake_run(upload_id, person_day, **kw):
        calls.append(upload_id)
        if upload_id.endswith("busy0000"):
            return runner.Outcome("Busy")
        if upload_id.endswith("fail0000"):
            raise runner.RunFailed("boom")
        return runner.Outcome("Done")

    records = [
        {"messageId": "m1", "body": json.dumps({"uploadId": "20260601T083005Z-done0000", "personDay": _RUN_PD})},
        {"messageId": "m2", "body": json.dumps({"uploadId": "20260601T083005Z-busy0000", "personDay": _RUN_PD})},
        {"messageId": "m3", "body": json.dumps({"uploadId": "20260601T083005Z-fail0000", "personDay": _RUN_PD})},
        {"messageId": "m4", "body": "not json"},
        {"messageId": "m5", "body": json.dumps({"uploadId": "x", "personDay": _RUN_PD, "mode": "bogus"})},
    ]
    with mock.patch.dict(runner._aws, {"settings": rig.settings, "status": rig.status, "storage": None,
                                       "ssm": None, "sns": None}), \
            mock.patch.object(runner, "run_folder", fake_run), mock.patch.object(runner, "log"):
        out = runner.handler({"Records": records}, types.SimpleNamespace(aws_request_id="req-1"))
    assert out == {"batchItemFailures": [{"itemIdentifier": m} for m in ("m2", "m3", "m4", "m5")]}
    assert len(calls) == 3


def test_runner_local_mode_reports_sessions_like_parity_check():
    rig = _Rig()
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(runner.pipeline, "extract_calls", rig.extract_calls):
        folder = Path(d) / _RUN_PD
        folder.mkdir()
        for name in list(_BY_IMAGE) + ["correct output.png", "notes.txt"]:
            (folder / name).write_bytes(b"x")
        rig.by_image["correct output.png"] = []
        got = runner.run_local(folder, ["correct output.png"], None)
    assert got == {"images": 3, "records": 4, "countable": 4,
                   "sessions": [["08:00", "09:00"], ["10:00", "11:00"]]}


# ---------------------------------------------------------------------------
# The Lambda platform (Stories 3.1, 3.7): the AWS CLI adapter the PC tools use,
# tools/enqueue.py, the kill switch, and the benchmark. The AWS CLI, Lambda,
# SNS and S3 are faked; DynamoDB is the conditional-write fake above, reached
# through the CLI adapter. All names and values are fictional.
# ---------------------------------------------------------------------------

import base64 as _b64
import importlib as _importlib

from cloud import bench as cbench, killswitch
from tools import aws_cli, bench_lambda, enqueue

_NFD_PD = unicodedata.normalize("NFD", "Nguyễn Văn A & 2026-06-01")


def _ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class _FakeCli:
    """Stands in for subprocess.run under the AWS CLI; DynamoDB calls go to a _FakeDynamo."""

    def __init__(self, fail_on=None, keys=()):
        self.dynamo, self.log, self.inputs, self.files = _FakeDynamo(), [], [], []
        self.fail_on, self.keys = fail_on, list(keys)   # keys: the data bucket's objects

    def __call__(self, cmd, capture_output, text, encoding, errors):
        assert cmd[0] == "aws" and encoding == "utf-8" and cmd[cmd.index("--region") + 1] == "ap-southeast-2"
        payload = None
        if "--cli-input-json" in cmd:
            path = Path(cmd[cmd.index("--cli-input-json") + 1].removeprefix("file://"))
            raw = path.read_bytes()
            assert raw.isascii(), "the CLI's input file must be ASCII"
            payload = json.loads(raw)
            self.files.append(path)
            self.inputs.append(payload)
        op = f"{cmd[1]} {cmd[2]}"
        self.log.append(op)
        if op == self.fail_on:
            return SimpleNamespace(returncode=1, stdout="", stderr="upload failed: connection reset")
        if op == "sts get-caller-identity":
            return _ok('{"Account": "123456789012"}')
        if cmd[1] == "dynamodb":
            method = getattr(self.dynamo, cmd[2].replace("-", "_"))
            try:
                return _ok(json.dumps(method(**payload) or {}, ensure_ascii=False))
            except _CondFailed:
                return SimpleNamespace(returncode=254, stdout="", stderr=(
                    "An error occurred (ConditionalCheckFailedException) when calling the "
                    "UpdateItem operation: The conditional request failed"))
        if op == "s3api list-objects-v2":   # the CLI pages through and returns every key
            found = [{"Key": k} for k in sorted(self.keys) if k.startswith(payload["Prefix"])]
            return _ok(json.dumps({"Contents": found} if found else {}, ensure_ascii=False))
        if op in ("s3 cp", "sqs send-message"):
            return _ok()
        raise AssertionError(f"unexpected AWS call {cmd}")


def test_aws_cli_writes_ascii_input_reads_utf8_output_and_cleans_up():
    seen = {}

    def run(cmd, capture_output, text, encoding, errors):
        path = Path(cmd[cmd.index("--cli-input-json") + 1].removeprefix("file://"))
        seen["raw"], seen["path"] = path.read_bytes(), path
        return _ok(json.dumps({"Attributes": {"personDay": {"S": "Nguyễn Văn A & 2026-06-01"}}},
                              ensure_ascii=False))

    out = aws_cli.Client("dynamodb", run=run).update_item(TableName="t", Key={"pk": {"S": "Nguyễn"}})
    assert seen["raw"].isascii() and json.loads(seen["raw"])["Key"]["pk"]["S"] == "Nguyễn"
    assert out["Attributes"]["personDay"]["S"] == "Nguyễn Văn A & 2026-06-01"
    assert not seen["path"].exists()


def test_aws_cli_errors_carry_the_aws_code_and_hide_values():
    def run(cmd, capture_output, text, encoding, errors):
        return SimpleNamespace(returncode=254, stdout="", stderr=(
            "An error occurred (ConditionalCheckFailedException) when calling the PutItem "
            "operation: s3cr3t was in the request"))

    try:
        aws_cli.run_cli(["dynamodb", "put-item"], {"x": 1}, run=run, hide=["s3cr3t"])
    except aws_cli.AwsError as e:
        assert e.response["Error"]["Code"] == "ConditionalCheckFailedException"
        assert "s3cr3t" not in str(e)
    else:
        raise AssertionError("no AwsError")
    # cloud/status.py reads a refused condition from the CLI as it would from boto3.
    assert fstatus._refused(aws_cli.AwsError("x", "ConditionalCheckFailedException", ""))


def _enqueue_folder(root, name, files):
    folder = Path(root) / name
    folder.mkdir()
    for f in files:
        (folder / f).write_bytes(b"made-up image")
    return folder


def _enqueue(argv, cli, max_images=40):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = enqueue.main(argv, run=cli, max_images=max_images)
    return code, out.getvalue() + err.getvalue()


def test_enqueue_uploads_then_marks_pending_then_queues():
    cli = _FakeCli()
    with tempfile.TemporaryDirectory() as d:
        folder = _enqueue_folder(d, _NFD_PD, ["shot_b.jpg", "Shot_a.PNG", "notes.txt"])
        code, said = _enqueue([str(folder)], cli)
    assert code == 0, said
    assert cli.log == ["sts get-caller-identity", "dynamodb put-item", "s3 cp", "s3 cp",
                       "dynamodb query", "dynamodb update-item", "sqs send-message"]   # query: Story 4.6
    message = cli.inputs[-1]
    assert message["QueueUrl"] == "https://sqs.ap-southeast-2.amazonaws.com/123456789012/calls-log-runs"
    body = json.loads(message["MessageBody"])
    nfc = "Nguyễn Văn A & 2026-06-01"
    assert body["personDay"] == nfc and body["mode"] == "process" and naming.is_upload_id(body["uploadId"])
    (item,) = cli.dynamo.items.values()
    assert item["status"] == "Pending" and item["imageCount"] == 2 and item["personDay"] == nfc
    assert item["uploadId"] == body["uploadId"]
    assert not any(p.exists() for p in cli.files)
    assert f"upload {body['uploadId']}" in said


def test_enqueue_uploads_images_in_the_pc_order_to_the_inbox():
    calls = []
    cli = _FakeCli()

    def run(cmd, **kw):
        calls.append(cmd)
        return cli(cmd, **kw)

    with tempfile.TemporaryDirectory() as d:
        folder = _enqueue_folder(d, _RUN_PD, ["shot_b.jpg", "Shot_a.PNG"])
        assert _enqueue([str(folder)], run)[0] == 0
    copies = [c for c in calls if c[1:3] == ["s3", "cp"]]
    upload_id = json.loads(cli.inputs[-1]["MessageBody"])["uploadId"]
    assert [c[3] for c in copies] == [str(folder / "Shot_a.PNG"), str(folder / "shot_b.jpg")]
    assert [c[4] for c in copies] == [f"s3://calls-log-data-123456789012/inbox/{upload_id}/Shot_a.PNG",
                                      f"s3://calls-log-data-123456789012/inbox/{upload_id}/shot_b.jpg"]


def test_enqueue_refuses_every_folder_before_sending_anything():
    cli = _FakeCli()
    with tempfile.TemporaryDirectory() as d:
        good = _enqueue_folder(d, _RUN_PD, ["a.jpg"])
        args = [str(good), str(good),
                str(_enqueue_folder(d, "holiday photos", ["a.jpg"])),              # no Person-Day
                str(_enqueue_folder(d, "Tran Thi B & 2026-02-30", ["a.jpg"])),     # no such day
                str(_enqueue_folder(d, "Le Van C & 2026-06-02", ["notes.txt"])),   # no images
                str(_enqueue_folder(d, "Pham Thi D & 2026-06-03", ["1.jpg", "2.jpg", "3.jpg"])),
                str(Path(d) / "missing")]
        code, said = _enqueue(args, cli, max_images=2)
    assert code == 2 and cli.log == []
    assert said.count("\n  ") == 6 and "given twice" in said and "more than max_images (2)" in said


def test_enqueue_dry_run_touches_no_aws():
    cli = _FakeCli()
    with tempfile.TemporaryDirectory() as d:
        code, said = _enqueue(["--dry-run", str(_enqueue_folder(d, _RUN_PD, ["a.jpg", "b.jpg"]))], cli)
    assert code == 0 and cli.log == [] and f"would send {_RUN_PD}: 2 image(s)" in said


def test_enqueue_stops_at_an_aws_failure_and_sends_no_message():
    cli = _FakeCli(fail_on="s3 cp")
    with tempfile.TemporaryDirectory() as d:
        first = _enqueue_folder(d, _RUN_PD, ["a.jpg"])
        second = _enqueue_folder(d, "Tran Thi B & 2026-06-01", ["a.jpg"])
        code, said = _enqueue([str(first), str(second)], cli)
    assert code == 1
    assert "sqs send-message" not in cli.log and cli.log.count("dynamodb put-item") == 1
    (item,) = cli.dynamo.items.values()
    assert item["status"] == "Uploading"   # never Pending, so no Run can pick it up
    assert "Not tried: 1 folder(s)" in said


class _FakeLambda:
    def __init__(self, pages, fail=False):
        self.pages, self.fail, self.listed, self.updates = pages, fail, [], []

    def list_event_source_mappings(self, FunctionName, Marker=None):
        self.listed.append((FunctionName, Marker))
        return self.pages[int(Marker or 0)]

    def update_event_source_mapping(self, UUID, Enabled):
        if self.fail:
            raise TimeoutError("simulated")
        self.updates.append((UUID, Enabled))


_KS_SETTINGS = settings.Settings(runner_function="calls-log-runner",
                                 alerts_topic_arn="arn:aws:sns:ap-southeast-2:123456789012:calls-log-alerts")


def _alarm_event(state="ALARM"):
    # The documented shape of a CloudWatch alarm's Lambda action.
    return {"source": "aws.cloudwatch",
            "alarmArn": "arn:aws:cloudwatch:ap-southeast-2:123456789012:alarm:calls-log-runner-dlq",
            "alarmData": {"alarmName": "calls-log-runner-dlq",
                          "state": {"value": state, "reason": "Threshold Crossed: 1 datapoint was >= 1.0"}}}


def test_kill_switch_turns_the_running_trigger_off_and_emails():
    lam = _FakeLambda([
        {"EventSourceMappings": [{"UUID": "u-1", "State": "Enabled"}], "NextMarker": "1"},
        {"EventSourceMappings": [{"UUID": "u-2", "State": "Disabled"}]},
    ])
    sns = _FakeSns()
    out = killswitch.respond(killswitch.alarm_of(_alarm_event()), _KS_SETTINGS, lam, sns)
    assert lam.listed == [("calls-log-runner", None), ("calls-log-runner", "1")]
    assert lam.updates == [("u-1", False)] and out["stopped"] == [{"uuid": "u-1", "was": "Enabled"}]
    (mail,) = sns.sent
    assert mail["TopicArn"] == _KS_SETTINGS.alerts_topic_arn and "stopped" in mail["Subject"]
    assert "calls-log-runner-dlq" in mail["Message"] and "Turning the runner back on" in mail["Message"]


def test_kill_switch_ignores_an_alarm_that_is_not_in_alarm():
    lam, sns = _FakeLambda([{"EventSourceMappings": [{"UUID": "u-1", "State": "Enabled"}]}]), _FakeSns()
    assert killswitch.respond(killswitch.alarm_of(_alarm_event("OK")), _KS_SETTINGS, lam, sns)["ignored"]
    assert lam.listed == [] and sns.sent == []


def test_kill_switch_says_so_when_the_trigger_was_already_off():
    lam, sns = _FakeLambda([{"EventSourceMappings": [{"UUID": "u-1", "State": "Disabling"}]}]), _FakeSns()
    assert killswitch.respond(killswitch.alarm_of(_alarm_event()), _KS_SETTINGS, lam, sns)["stopped"] == []
    assert lam.updates == [] and "already off" in sns.sent[0]["Message"]


def test_kill_switch_emails_when_it_cannot_stop_and_fails_so_lambda_retries():
    lam, sns = _FakeLambda([{"EventSourceMappings": [{"UUID": "u-1", "State": "Enabled"}]}], fail=True), _FakeSns()
    try:
        killswitch.respond(killswitch.alarm_of(_alarm_event()), _KS_SETTINGS, lam, sns)
    except TimeoutError:
        pass
    else:
        raise AssertionError("the failure must reach Lambda, which retries the invocation")
    assert "could NOT stop" in sns.sent[0]["Subject"] and "by hand" in sns.sent[0]["Message"]


def test_kill_switch_reads_only_the_environment():
    env = {"CALLS_LOG_RUNNER_FUNCTION": "calls-log-runner", "CALLS_LOG_ALERTS_TOPIC_ARN": "arn:topic"}
    got = settings.from_environment(env)
    assert got.runner_function == "calls-log-runner" and got.alerts_topic_arn == "arn:topic"
    assert got.sync_secret == "" and got.webapp_url == ""


def _infra_text():
    """infra/*.tf, or None inside the runner image, which has no infra/ (CI mounts it in)."""
    infra = Path(__file__).resolve().parent / "infra"
    if not infra.is_dir():
        return None
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(infra.glob("*.tf")))


def test_function_environments_name_only_what_settings_reads():
    text = _infra_text()
    if text is None:
        return
    named = set(_re.findall(r"\b(CALLS_LOG_[A-Z_]+)\s*=", text))
    assert named == set(settings.ENVIRONMENT.values()), named ^ set(settings.ENVIRONMENT.values())


def test_every_function_waits_for_the_repository_policy():
    # CreateFunction is refused until the repository policy lets Lambda pull
    # the image (infra/ecr.tf); Terraform would otherwise create both at once.
    # The data lake's publisher is a zip (infra/lake.tf) and pulls no image.
    text = _infra_text()
    if text is None:
        return
    blocks = _re.findall(r'resource "aws_lambda_function" "(\w+)" \{(.*?)\n\}', text, _re.S)
    assert blocks, "no functions found"
    for name, body in blocks:
        if not _re.search(r'package_type\s*=\s*"Image"', body):
            assert "image_uri" not in body, name
            continue
        m = _re.search(r"depends_on\s*=\s*\[([^\]]*)\]", body)
        assert m and "aws_ecr_repository_policy.runner" in m.group(1), name


def test_every_function_command_names_a_real_handler():
    text = _infra_text()
    if text is None:
        return
    handlers = set(_re.findall(r'"/lambda-entrypoint\.sh",\s*"([\w.]+)"', text))
    assert handlers == {"cloud.runner.handler", "cloud.killswitch.handler", "cloud.bench.handler", "cloud.intake.handler"}
    zipped = set(_re.findall(r'^\s*handler\s*=\s*"([\w.]+)"', text, _re.M))   # zip functions (infra/lake.tf)
    assert zipped == {"cloud.publisher.handler"}
    handlers |= zipped
    for dotted in handlers:
        module, _, name = dotted.rpartition(".")
        assert callable(getattr(_importlib.import_module(module), name)), dotted


def test_bench_times_each_image_and_returns_no_names_or_text():
    rig = _Rig()
    s3 = _FakeS3()
    for name in list(_BY_IMAGE) + ["notes.txt"]:
        s3.objects[f"bench/1/{name}"] = b"made-up image"
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(runner.pipeline, "extract_calls", rig.extract_calls):
        got = cbench.measure(s3, "calls-log-data-123456789012", "bench/1/", lambda: 800_000,
                             memory_mb=2048, cold=True, workdir=Path(d))
        assert not (Path(d) / "images").exists()   # the downloaded images are gone
    assert rig.ocr_calls == ["Shot_01.jpg", "shot_02.jpg", "shot_03.png"]   # the PC's order
    assert (got["images"], got["processed"], got["records"], got["sessions"]) == (3, 3, 4, 2)
    assert got["cutOff"] is False and got["memoryMb"] == 2048 and got["cold"] is True
    text = json.dumps(got)
    assert "Shot_01" not in text and _PHONE not in text


def test_bench_stops_before_the_time_limit_and_says_so():
    rig = _Rig()
    s3 = _FakeS3()
    for name in _BY_IMAGE:
        s3.objects[f"bench/2/{name}"] = b"made-up image"
    left = iter([800_000, 800_000, cbench.STOP_MARGIN_MS - 1])
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(runner.pipeline, "extract_calls", rig.extract_calls):
        got = cbench.measure(s3, "b", "bench/2/", lambda: next(left), memory_mb=1536, cold=False,
                             workdir=Path(d))
    assert got["cutOff"] is True and got["processed"] == 2 and got["images"] == 3


def test_bench_refuses_a_prefix_outside_bench():
    for event in ({"prefix": "inbox/x/"}, {"prefix": "bench/1"}, {}):
        try:
            cbench.handler(event, None)
        except ValueError:
            continue
        raise AssertionError(f"accepted {event}")


_REPORT_COLD = ("START RequestId: r1 Version: $LATEST\nEND RequestId: r1\n"
                "REPORT RequestId: r1\tDuration: 30000.00 ms\tBilled Duration: 38000 ms\t"
                "Memory Size: 2048 MB\tMax Memory Used: 900 MB\tInit Duration: 7950.12 ms\t\n")
_REPORT_WARM = "REPORT RequestId: r2\tDuration: 24000.00 ms\tBilled Duration: 24000 ms\t" \
               "Memory Size: 2048 MB\tMax Memory Used: 910 MB\t\n"


def test_bench_report_line_is_read_cold_and_warm():
    cold = bench_lambda.parse_report(_REPORT_COLD)
    assert cold == {"durationMs": 30000.0, "billedMs": 38000, "memoryMb": 2048, "maxUsedMb": 900,
                    "initMs": 7950.12}
    assert bench_lambda.parse_report(_REPORT_COLD + _REPORT_WARM)["initMs"] is None   # the last one


def test_bench_invoke_reads_the_result_file_and_the_log_tail():
    def run(cmd, capture_output, text, encoding, errors):
        assert cmd[cmd.index("--payload") + 1] == '{"prefix": "bench/1/"}'
        assert int(cmd[cmd.index("--cli-read-timeout") + 1]) > 900
        Path(cmd[cmd.index("--output") + 2]).write_text('{"images": 12, "processed": 12, "cold": true}',
                                                         encoding="utf-8")
        return _ok(json.dumps({"StatusCode": 200, "LogResult": _b64.b64encode(_REPORT_COLD.encode()).decode()}))

    got = bench_lambda.invoke("calls-log-bench-2048", "bench/1/", run=run)
    assert got["processed"] == 12 and got["billedMs"] == 38000 and got["initMs"] == 7950.12


def _bench_run(prefix, cold, billed, images=12, median=1800, used=900):
    return {"prefix": prefix, "cold": cold, "billedMs": billed, "images": images, "processed": images,
            "medianImageMs": median, "maxUsedMb": used, "initMs": 8000.0 if cold else None}


def test_bench_picks_the_lowest_gb_s_per_image_and_projects_the_month():
    small = bench_lambda.per_size([_bench_run("bench/1/", True, 40000), _bench_run("bench/1/", False, 30000)], 1536)
    big = bench_lambda.per_size([_bench_run("bench/1/", True, 30000), _bench_run("bench/1/", False, 18000)], 3008)
    assert abs(small["gbsPerImage"] - 30 * 1.5 / 12) < 1e-9          # 3.75 GB-s an image
    assert abs(small["coldGbs"] - 10 * 1.5) < 1e-9                   # the cold run's extra 10 s
    assert abs(small["monthGbs"] - (3.75 * 12_000 + 15 * 450)) < 1e-6
    text, holds = bench_lambda.report([small, big], None, "2026-10-16")
    assert holds and "**Chosen: 1,536 MB**" in text and "`runner_memory_mb = 1536`" in text   # 3.75 < 4.41


def test_bench_says_when_the_plan_does_not_hold_or_a_folder_does_not_fit():
    slow = bench_lambda.per_size([_bench_run("bench/1/", False, 400_000)], 2048)   # 66.7 GB-s an image
    text, holds = bench_lambda.report([slow], {"error": "timed out", "images": 40, "durationMs": 900000.0,
                                               "memoryMb": 2048}, "2026-10-16")
    assert not holds and "Fargate Spot" in text and "checkpoints (AD-16)" in text


# ---------------------------------------------------------------------------
# Re-queue and re-send (Story 3.8): tools/enqueue.py --requeue, the runner's
# resync mode and tools/resync.py. AWS is faked as above; nothing reaches a
# live Sheet. All names are fictional.
# ---------------------------------------------------------------------------

from tools import resync as resync_tool

_RQ_OLD = "20260601T080000Z-00000001"
_RQ_NEW = "20260602T080000Z-00000002"


def _stored_upload(status, upload_id, state, images=2, person_day=_RUN_PD):
    """
    An upload of person_day left in the given state, through the real
    transitions. A later upload of the day is Held at Ready; unless Held is
    asked for, it is re-run (Story 4.6), as the Owner would.
    """
    status.create(person_day, upload_id, images)
    if status.mark_ready(person_day, upload_id)["status"] == "Held" and state != "Held":
        status.requeue(person_day, upload_id)
    if state in ("Done", "Failed", "Processing", "Parked"):
        status.begin_run(person_day, upload_id, "run-x")
        status.release_lease(person_day, "run-x")
        if state == "Done":
            status.finish(person_day, upload_id)
        elif state in ("Failed", "Parked"):
            status.finish(person_day, upload_id, "boom")
    if state == "Parked":
        for _ in range(fstatus.MAX_RUNS):   # two more failed Runs, then the 4th start parks it
            status.begin_run(person_day, upload_id, "run-x")
            status.release_lease(person_day, "run-x")
            if status.get(person_day, upload_id)["status"] == "Processing":
                status.finish(person_day, upload_id, "boom")
    assert status.get(person_day, upload_id)["status"] == state


def _requeue_cli(states, inbox=None):
    cli = _FakeCli()
    status = FolderStatus(cli.dynamo, "calls-log-folders", lambda: 1_780_000_000.0)
    for upload_id, state in states.items():
        _stored_upload(status, upload_id, state)
    for upload_id, n in (inbox if inbox is not None else {u: 2 for u in states}).items():
        cli.keys += [f"inbox/{upload_id}/shot_{i}.jpg" for i in range(n)]
    return cli, status


def test_status_lists_a_person_days_uploads_oldest_first_without_the_lease():
    cli, status = _requeue_cli({_RQ_NEW: "Done", _RQ_OLD: "Pending"})
    status.take_lease(_RUN_PD, "someone")
    assert [u["uploadId"] for u in status.uploads(_RUN_PD)] == [_RQ_OLD, _RQ_NEW]   # paged, in order
    assert status.uploads("Tran Thi B & 2026-06-01") == []


def test_requeue_runs_the_newest_stored_upload_again_with_attempts_reset():
    for state in ("Done", "Failed", "Parked"):
        cli, status = _requeue_cli({_RQ_OLD: "Done", _RQ_NEW: state})
        code, said = _enqueue(["--requeue", _RUN_PD], cli)
        assert code == 0, (state, said)
        item = status.get(_RUN_PD, _RQ_NEW)
        assert item["status"] == "Pending" and item["attempts"] == 0 and "lastError" not in item
        assert status.get(_RUN_PD, _RQ_OLD)["status"] == "Done"   # the older upload is left alone
        assert cli.log[-2:] == ["dynamodb update-item", "sqs send-message"]
        assert json.loads(cli.inputs[-1]["MessageBody"]) == {
            "uploadId": _RQ_NEW, "personDay": _RUN_PD, "mode": "process"}
        assert f"re-queued {_RUN_PD}: upload {_RQ_NEW}, 2 image(s)" in said


def test_requeue_can_name_an_older_upload():
    cli, status = _requeue_cli({_RQ_OLD: "Failed", _RQ_NEW: "Done"})
    assert _enqueue(["--requeue", _RUN_PD, "--upload-id", _RQ_OLD], cli)[0] == 0
    assert status.get(_RUN_PD, _RQ_OLD)["status"] == "Pending" and status.get(_RUN_PD, _RQ_NEW)["status"] == "Done"


def test_requeue_refuses_and_changes_nothing():
    cases = [
        ({_RQ_NEW: "Pending"}, None, "is Pending"),
        ({_RQ_NEW: "Processing"}, None, "is Processing"),
        ({_RQ_NEW: "Done"}, {_RQ_NEW: 1}, "1 of its 2 images are still in inbox/"),   # expired
        ({}, None, "no stored upload"),
    ]
    for states, inbox, why in cases:
        cli, status = _requeue_cli(states, inbox)
        before = copy.deepcopy(cli.dynamo.items)
        code, said = _enqueue(["--requeue", _RUN_PD], cli)
        assert code == 2 and why in said, (states, said)
        assert cli.dynamo.items == before and "sqs send-message" not in cli.log


def test_requeue_usage_is_checked_before_any_aws_call():
    cli = _FakeCli()
    with tempfile.TemporaryDirectory() as d:
        folder = str(_enqueue_folder(d, _RUN_PD, ["a.jpg"]))
        for argv in (["--requeue", _RUN_PD, folder], ["--upload-id", _RQ_OLD, folder],
                     ["--requeue", _RUN_PD, "--dry-run"], ["--requeue", "holiday photos"],
                     ["--requeue", _RUN_PD, "--upload-id", "not-an-id"], []):
            assert _enqueue(argv, cli)[0] == 2, argv
    assert cli.log == []


def _resync(rig, deliveries=1, sync_effect=None):
    with ExitStack() as st, tempfile.TemporaryDirectory() as tmp:
        for k in ("sheets_webapp_url", "sheets_sync_secret", "sheets_sync_enabled"):
            st.enter_context(mock.patch.object(CONFIG, k, getattr(CONFIG, k)))
        st.enter_context(mock.patch.object(
            sheets_sync.urllib.request, "urlopen", side_effect=AssertionError("a test reached the network")))
        rig.sync = st.enter_context(mock.patch.object(runner, "sync_day", side_effect=sync_effect))
        st.enter_context(mock.patch.object(runner, "log"))
        out = runner.resync_day(_RUN_PD, storage=cstorage.S3Storage(rig.s3, "b"), settings=rig.settings,
                                sns=rig.sns, deliveries=deliveries, workdir=Path(tmp))
        assert list(Path(tmp).iterdir()) == []
        return out, CONFIG.sheets_sync_enabled


def test_resync_sends_the_stored_day_sheet_even_with_the_switch_off():
    rig = _Rig(switch="false")
    assert rig.run().status == "Done" and not rig.sync.called   # stored, not synced
    out, enabled = _resync(rig)
    assert (out.status, out.sessions) == ("Synced", 2) and enabled is True and rig.sns.sent == []
    name, date, sessions = rig.sync.call_args.args
    assert (name, date, [(s.start_time, s.end_time) for s in sessions]) == (
        "Nguyen Van A", "2026-06-01", [("08:00", "09:00"), ("10:00", "11:00")])


def test_resync_retries_then_drops_with_one_email():
    rig = _Rig()
    rig.run()
    failure = sheets_sync.SheetsSyncError(f"bad secret {_ST_SECRET}")
    for deliveries in (1, 2):
        try:
            _resync(rig, deliveries, failure)
        except runner.RunFailed:
            continue
        raise AssertionError("an early failure must return the message")
    assert rig.sns.sent == []
    out, _ = _resync(rig, runner.RESYNC_TRIES, failure)
    assert out.status == "Dropped" and len(rig.sns.sent) == 1
    mail = rig.sns.sent[0]
    assert "failed" in mail["Subject"] and _RUN_PD in mail["Message"] and _ST_SECRET not in mail["Message"]


def test_resync_with_no_stored_day_sheet_emails_once_and_never_retries():
    rig = _Rig()
    out, _ = _resync(rig)
    assert out.status == "Skipped" and not rig.sync.called
    assert "nothing to re-send" in rig.sns.sent[0]["Subject"]


def test_runner_handler_dispatches_resync_messages_with_their_delivery_count():
    rig = _Rig()
    seen = []

    def fake_resync(person_day, *, storage, settings, sns, deliveries):
        seen.append((person_day, deliveries))
        if deliveries == 1:
            raise runner.RunFailed("Web App timed out")
        return runner.Outcome("Dropped")

    records = [
        {"messageId": "r1", "attributes": {"ApproximateReceiveCount": "1"},
         "body": json.dumps({"personDay": _RUN_PD, "mode": "resync"})},
        {"messageId": "r3", "attributes": {"ApproximateReceiveCount": "3"},
         "body": json.dumps({"personDay": _RUN_PD, "mode": "resync"})},
    ]
    with mock.patch.dict(runner._aws, {"settings": rig.settings, "status": rig.status, "storage": None,
                                       "ssm": None, "sns": None}), \
            mock.patch.object(runner, "resync_day", fake_resync), mock.patch.object(runner, "log"):
        out = runner.handler({"Records": records}, types.SimpleNamespace(aws_request_id="req-1"))
    assert seen == [(_RUN_PD, 1), (_RUN_PD, 3)]
    assert out == {"batchItemFailures": [{"itemIdentifier": "r1"}]}


_RS_KEYS = [
    "results/Nguyen Van A & 2026-06-01.xlsx",
    "results/Nguyen Van A & 2026-06-02.xlsx",
    "results/Tran Thi B & 2026-06-01.xlsx",
    "results/notes.txt",
    "results/old/Le Van C & 2026-06-01.xlsx",
    "inbox/20260601T083005Z-0a1b2c3d/shot_01.jpg",
]


def _resync_tool(argv, cli):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = resync_tool.main(argv, run=cli)
    return code, out.getvalue() + err.getvalue()


def test_resync_tool_lists_and_filters_like_resync_output_on_a_dry_run():
    cli = _FakeCli(keys=_RS_KEYS)
    code, said = _resync_tool(["--dry-run", "--name", "Nguyen"], cli)
    assert code == 0 and "sqs send-message" not in cli.log
    assert said.count("would re-send") == 2 and "2 object(s) in results/" in said
    code, said = _resync_tool(["--dry-run", "--date", "2026-6-1"], cli)   # an unpadded date counts
    assert code == 0 and "would re-send Nguyen Van A & 2026-06-01" in said and "would re-send Tran Thi B" in said
    assert "Le Van C" not in said and "2026-06-02" not in said


def test_resync_tool_queues_one_resync_message_per_day():
    cli = _FakeCli(keys=_RS_KEYS)
    code, said = _resync_tool(["--name", "Tran", "--date", "2026-06-01"], cli)
    assert code == 0, said
    (message,) = [i for i in cli.inputs if "MessageBody" in i]
    assert json.loads(message["MessageBody"]) == {"personDay": "Tran Thi B & 2026-06-01", "mode": "resync"}
    assert message["QueueUrl"].endswith("/123456789012/calls-log-runs")


def test_resync_tool_refuses_a_bad_date_and_says_when_nothing_matches():
    cli = _FakeCli(keys=_RS_KEYS)
    assert _resync_tool(["--date", "2026-02-30"], cli)[0] == 2 and cli.log == []
    code, said = _resync_tool(["--name", "Pham"], cli)
    assert code == 1 and "No stored Day Sheet matches" in said and "sqs send-message" not in cli.log


# ---------------------------------------------------------------------------
# Epic 4: the upload and status page (cloud/intake.py, cloud/passphrase.py,
# cloud/static/upload.html). AWS is faked; the passphrase hashes use few
# PBKDF2 rounds so the tests stay fast.
# ---------------------------------------------------------------------------

import hashlib
import shutil as _shutil
import urllib.parse
import subprocess as _subprocess

from cloud import intake, passphrase as cpass

_PASS = "correct horse battery staple"
_PASS_HASH = cpass.hash_passphrase(_PASS, iterations=1000)
_IN_BUCKET = "calls-log-data-123456789012"


def test_passphrase_hash_matches_only_the_same_passphrase():
    assert cpass.verify(_PASS, _PASS_HASH)
    assert not cpass.verify(_PASS + " ", _PASS_HASH) and not cpass.verify("", _PASS_HASH)
    assert cpass.hash_passphrase(_PASS, iterations=1000) != _PASS_HASH   # a new salt every time
    assert cpass.hash_passphrase(_PASS).split("$")[1] == "600000"
    for broken in ("", "unset", "pbkdf2_sha256$x$AAAA$AAAA", "md5$1000$AAAA$AAAA",
                   _PASS_HASH.replace("pbkdf2_sha256", "pbkdf2_sha1"), _PASS_HASH + "$extra",
                   "pbkdf2_sha256$1000$not-base64!$AAAA", "pbkdf2_sha256$0$AAAA$AAAA"):
        assert not cpass.verify(_PASS, broken), broken


def test_passphrase_must_be_long_enough_and_unpadded():
    assert cpass.weak("short") and cpass.weak(" " + _PASS) and cpass.weak(_PASS + " ")
    assert cpass.weak(_PASS) is None


def test_passphrase_must_be_plain_ascii_since_the_page_sends_it_in_a_header():
    # fetch() refuses a header value with letters past U+00FF, and Latin-1 ones arrive
    # as other bytes: either way the page could never send it, so it is refused up front.
    for typed in ("Mật khẩu quản lý 2026", "café au lait 2026"):
        assert "unaccented" in cpass.weak(typed)
    assert cpass.weak("Mat khau quan ly 2026!") is None


def test_put_parameters_stores_only_the_passphrase_hash():
    aws = _FakeAws()
    answers = iter([_PASS, _PASS])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = put_parameters.main(["--passphrase"], run=aws, ask=lambda prompt: next(answers))
    said = out.getvalue() + err.getvalue()
    assert code == 0, said
    (stored,) = aws.inputs
    assert stored["Name"] == "/calls-log/intake_passphrase_hash" and stored["Type"] == "SecureString"
    assert cpass.verify(_PASS, stored["Value"]) and _PASS not in stored["Value"]
    assert _PASS not in said and _PASS not in " ".join(" ".join(c) for c in aws.calls)


def test_put_parameters_refuses_a_short_or_mistyped_passphrase():
    for answers in (["short", "short"], [_PASS, _PASS + "!"]):
        aws, it = _FakeAws(), iter(answers)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = put_parameters.main(["--passphrase"], run=aws, ask=lambda prompt: next(it))
        assert code == 2 and aws.calls == [] and "nothing written" in err.getvalue()


class _InS3(_FakeS3):
    """The bucket plus the two presigning calls the page needs (local computations in boto3)."""

    def __init__(self):
        super().__init__()
        self.posts, self.gets, self.modified = [], [], {}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        assert Bucket == _IN_BUCKET
        out = super().list_objects_v2(Bucket, Prefix, ContinuationToken)
        for obj in out["Contents"]:
            if obj["Key"] in self.modified:
                obj["LastModified"] = self.modified[obj["Key"]]
        return out

    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):
        self.posts.append({"Bucket": Bucket, "Key": Key, "Fields": Fields, "Conditions": Conditions,
                           "ExpiresIn": ExpiresIn})
        return {"url": f"https://{Bucket}.s3.ap-southeast-2.amazonaws.com/",
                "fields": {**Fields, "key": Key, "policy": "signed"}}

    def generate_presigned_url(self, method, Params, ExpiresIn):
        self.gets.append({"method": method, "Params": Params, "ExpiresIn": ExpiresIn})
        return f"https://{Params['Bucket']}.s3.ap-southeast-2.amazonaws.com/signed-{len(self.gets)}"


class _FakeSqs:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_message(self, QueueUrl, MessageBody):
        if self.fail:
            raise TimeoutError("simulated")
        self.sent.append({"QueueUrl": QueueUrl, "body": json.loads(MessageBody)})


class _Tripwire:
    """Any call on it fails the test: the passphrase must be checked before data is touched."""

    def __getattr__(self, name):
        raise AssertionError(f"{name} was called before the passphrase was checked")


_IN_VALUES = dict(_ST_VALUES, intake_passphrase_hash=_PASS_HASH)
_IN_TODAY = _dt.date(2026, 6, 8)


def _intake(values=None, sqs=None, switch="false"):
    ssm = _FakeSsm(values or _IN_VALUES, switch=switch)
    current = settings.read(ssm, dict(_ST_ENV, CALLS_LOG_QUEUE_URL="https://sqs/calls-log-runs"))
    dynamo, s3 = _FakeDynamo(), _InS3()
    svc = intake.Services(current, fstatus.FolderStatus(dynamo, "calls-log-folders"),
                          cstorage.S3Storage(s3, _IN_BUCKET), s3, sqs or _FakeSqs(), ssm, 40,
                          today=lambda: _IN_TODAY)
    return svc, dynamo, s3


def _call(svc, path, body=None, passphrase=_PASS, method="POST", raw=None, owner=None):
    headers = {"content-type": "application/json"}
    if passphrase is not None:
        headers["x-calls-log-passphrase"] = passphrase
    if owner is not None:
        headers["x-calls-log-owner"] = owner
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": headers,
             "body": raw if raw is not None else json.dumps(body or {}), "isBase64Encoded": False}
    res = intake.handle(event, lambda: svc)
    out = json.loads(res["body"]) if res["body"] else None
    for refusal in [out] + ((out or {}).get("folders") or []):   # every code has a string on the page
        if isinstance(refusal, dict) and refusal.get("ok") is False and "code" in refusal:
            assert refusal["code"] in intake.CODES, refusal["code"]
    return res["statusCode"], out


def _files(*names, size=1000):
    return [{"name": n, "size": size} for n in names]


def test_intake_serves_the_page_with_a_csp_that_allows_only_its_own_blocks():
    def no_services():
        raise AssertionError("GET / must not read settings or touch AWS")

    with mock.patch.dict(os.environ, {"CALLS_LOG_BUCKET": _IN_BUCKET, "AWS_REGION": "ap-southeast-2"}):
        res = intake.handle({"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}, no_services)
    assert res["statusCode"] == 200 and res["headers"]["Content-Type"].startswith("text/html")
    csp = res["headers"]["Content-Security-Policy"]
    html = res["body"]
    for tag in ("script", "style"):
        (block,) = _re.findall(rf"<{tag}>(.*?)</{tag}>", html, _re.S)
        digest = _b64.b64encode(hashlib.sha256(block.encode("utf-8")).digest()).decode()
        assert f"'sha256-{digest}'" in csp, tag
    assert "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert f"connect-src 'self' https://{_IN_BUCKET}.s3.ap-southeast-2.amazonaws.com" in csp
    assert 'style="' not in html and " on" + "click=" not in html   # inline attributes would be blocked
    # The passphrases stay in memory: the browser stores only the chosen language.
    stored = _re.findall(r'localStorage\.(\w+)\("([^"]+)"', html)
    assert {key for _, key in stored} == {"calls-log-lang"} and html.count("localStorage") == len(stored)
    assert "sessionStorage" not in html and "indexedDB" not in html and "document.cookie" not in html


def test_intake_checks_the_passphrase_before_touching_any_data():
    svc, _, _ = _intake()
    svc.status, svc.storage, svc.s3, svc.sqs = _Tripwire(), _Tripwire(), _Tripwire(), _Tripwire()
    for given in (None, "", "wrong passphrase", _PASS.upper()):
        code, body = _call(svc, "/api/status", passphrase=given)
        assert code == 401 and body == {"ok": False, "error": "Wrong passphrase.", "code": "wrong_passphrase",
                                        "args": {}}


def test_intake_says_so_when_no_passphrase_is_set():
    svc, _, _ = _intake(values=dict(_ST_VALUES))   # intake_passphrase_hash is the placeholder
    code, body = _call(svc, "/api/hello")
    assert code == 503 and "put_parameters.py --passphrase" in body["error"]


def test_intake_remembers_the_right_passphrase_but_rechecks_wrong_ones():
    svc, _, _ = _intake()
    intake._verified.clear()
    with mock.patch.object(intake.passphrase_mod, "verify", wraps=cpass.verify) as verify:
        assert _call(svc, "/api/hello")[0] == 200
        assert _call(svc, "/api/hello")[0] == 200
        assert verify.call_count == 1   # the second call skipped the PBKDF2
        assert _call(svc, "/api/hello", passphrase="guess one")[0] == 401
        assert _call(svc, "/api/hello", passphrase="guess two")[0] == 401
        assert _call(svc, "/api/hello", passphrase="guess two")[0] == 401   # a wrong one is never remembered
        assert verify.call_count == 4   # every wrong one pays for it


def test_intake_hello_gives_the_limits_and_the_sync_switch_read_only():
    svc, _, _ = _intake(switch="true")
    code, body = _call(svc, "/api/hello")
    assert code == 200 and body == {"ok": True, "maxImages": 40, "maxImageMb": 15, "syncEnabled": True}
    assert not any(hasattr(svc.ssm, m) for m in ("put_parameter", "delete_parameter"))


def test_the_image_limit_is_50_and_the_page_falls_back_to_it():
    # The Owner raised it from 40 (2026-10-16); the page shows its fallback until /api/hello answers.
    page = (Path(__file__).resolve().parent / "cloud" / "static" / "upload.html").read_text(encoding="utf-8")
    assert CONFIG.max_images == 50
    assert f"let limits = {{maxImages: {CONFIG.max_images}, maxImageMb: 15}};" in page


def test_intake_refuses_bad_folders_in_plain_words_and_writes_nothing():
    svc, dynamo, s3 = _intake()
    folders = [
        ("Nguyen Van A & 2026-06-01", _files("b.jpg", "A.png", "notes.txt"), None),
        ("holiday photos", _files("a.jpg"), "isn't \"<Name> & <yyyy-mm-dd>\""),
        ("Nguyen Van A & 2026-02-30", _files("a.jpg"), "real date"),
        ("Le Van C & 2026-06-01", _files("a.jpg"), "Le Van C is not a registered employee"),
        ("Tran Thi B & 2026-06-02", _files(*[f"{i:02}.jpg" for i in range(41)]), "41 images, more than 40"),
        ("Tran Thi B & 2026-06-03", _files("a.HEIC", "b.heic"), "only HEIC photos"),
        ("Tran Thi B & 2026-06-04", _files("readme.txt"), "no images"),
        ("Tran Thi B & 2026-06-05", _files("a.jpg", size=0), "a.jpg is empty"),
        ("Tran Thi B & 2026-06-06", _files("a.jpg", size=16 * 1024 * 1024), "more than 15 MB"),
        ("Tran Thi B & 2026-06-07", _files("a.jpg", size=int(15.3 * 1024 * 1024)), "a.jpg is 16 MB, more"),
        ("Nguyen Van A 2026-6-1", _files("a.jpg"), "same Person-Day as Nguyen Van A & 2026-06-01"),
    ]
    code, body = _call(svc, "/api/check", {"folders": [{"folder": f, "files": files} for f, files, _ in folders]})
    assert code == 200
    for (folder, _, why), got in zip(folders, body["folders"]):
        if why is None:
            assert got["ok"] and got["images"] == 2 and got["ignored"] == ["notes.txt"], got
            assert got["personDay"] == "Nguyen Van A & 2026-06-01"
        else:
            assert not got["ok"] and why in got["error"], (folder, got)
    assert dynamo.writes == 0 and s3.posts == [] and svc.sqs.sent == []


def test_intake_refuses_an_upload_the_check_would_refuse():
    svc, dynamo, s3 = _intake()
    code, body = _call(svc, "/api/uploads", {"folder": "Le Van C & 2026-06-01", "files": _files("a.jpg")})
    assert code == 400 and "not a registered employee" in body["error"] and "Nothing was uploaded" in body["error"]
    for files in ([{"name": "../a.jpg", "size": 1}], [{"name": "a.jpg", "size": "1"}], "a.jpg",
                  [{"name": "a.jpg", "size": True}]):
        code, body = _call(svc, "/api/uploads", {"folder": _RUN_PD, "files": files})
        assert code == 400, files
    assert dynamo.writes == 0 and s3.posts == []


def test_intake_upload_starts_uploading_with_one_exact_presigned_form_per_image():
    svc, dynamo, s3 = _intake()
    files = _files("shot_10.jpg", "Shot_02.PNG", "shot_03.webp", "clip.mov")
    code, body = _call(svc, "/api/uploads", {"folder": "Nguyễn Văn A & 2026-06-01", "files": files})
    assert code == 200, body
    up, pd = body["uploadId"], body["personDay"]
    assert naming.is_upload_id(up) and pd == unicodedata.normalize("NFC", "Nguyễn Văn A & 2026-06-01")
    item = svc.status.get(pd, up)
    assert item["status"] == "Uploading" and item["imageCount"] == 3 and item["upDay"] == fstatus.up_day(up)
    assert [f["name"] for f in body["forms"]] == ["Shot_02.PNG", "shot_03.webp", "shot_10.jpg"]   # the PC's order
    assert body["ignored"] == ["clip.mov"] and body["expiresInSeconds"] == 900
    types = {"Shot_02.PNG": "image/png", "shot_03.webp": "image/webp", "shot_10.jpg": "image/jpeg"}
    for post in s3.posts:
        name = post["Key"].rsplit("/", 1)[1]
        assert post["Key"] == f"inbox/{up}/{name}" and post["ExpiresIn"] == 900
        assert post["Fields"] == {"Content-Type": types[name]}
        assert {"Content-Type": types[name]} in post["Conditions"]
        assert ["content-length-range", 1, 15 * 1024 * 1024] in post["Conditions"]
    assert svc.sqs.sent == []   # nothing is processed before Ready


def _upload(svc, s3, names, folder=_RUN_PD):
    code, body = _call(svc, "/api/uploads", {"folder": folder, "files": _files(*names)})
    assert code == 200, body
    return body


def test_intake_ready_needs_every_image_then_queues_the_folder():
    svc, dynamo, s3 = _intake()
    up = _upload(svc, s3, ["a.jpg", "b.jpg"])
    ids = {"personDay": up["personDay"], "uploadId": up["uploadId"]}
    s3.objects[f"inbox/{up['uploadId']}/a.jpg"] = b"x"            # the upload broke after one image
    code, body = _call(svc, "/api/ready", ids)
    assert code == 409 and "Only 1 of 2 images arrived" in body["error"]
    assert svc.status.get(**{"person_day": ids["personDay"], "upload_id": ids["uploadId"]})["status"] == "Uploading"
    assert svc.sqs.sent == []
    s3.objects[f"inbox/{up['uploadId']}/b.jpg"] = b"x"
    code, body = _call(svc, "/api/ready", ids)
    assert code == 200 and body["status"] == "Pending"
    (message,) = svc.sqs.sent
    assert message == {"QueueUrl": "https://sqs/calls-log-runs",
                       "body": {"uploadId": up["uploadId"], "personDay": _RUN_PD, "mode": "process"}}


def test_intake_ready_again_requeues_a_pending_folder_and_refuses_a_finished_one():
    sqs = _FakeSqs(fail=True)
    svc, dynamo, s3 = _intake(sqs=sqs)
    up = _upload(svc, s3, ["a.jpg"])
    ids = {"personDay": up["personDay"], "uploadId": up["uploadId"]}
    s3.objects[f"inbox/{up['uploadId']}/a.jpg"] = b"x"
    code, body = _call(svc, "/api/ready", ids)
    assert code == 502 and "Click Ready again" in body["error"]
    sqs.fail = False
    code, body = _call(svc, "/api/ready", ids)                  # Pending with no message: queue it now
    assert code == 200 and body["queuedAgain"] and len(sqs.sent) == 1
    svc.status.begin_run(_RUN_PD, up["uploadId"], "run-1")
    svc.status.finish(_RUN_PD, up["uploadId"])
    code, body = _call(svc, "/api/ready", ids)
    assert code == 409 and "already Done" in body["error"] and "upload the whole folder again" in body["error"]
    assert len(sqs.sent) == 1


def test_intake_ready_refuses_what_is_not_an_upload():
    svc, _, _ = _intake()
    assert _call(svc, "/api/ready", {"personDay": _RUN_PD, "uploadId": "../etc"})[0] == 400
    assert _call(svc, "/api/ready", {"personDay": _RUN_PD, "uploadId": _RUN_UP})[0] == 404


def test_status_recent_reads_one_day_from_the_index():
    store, _ = _store()
    store.create(_RUN_PD, "20260601T083005Z-0a1b2c3d", 2)
    store.create(_RUN_PD, "20260602T010000Z-00000001", 2)
    store.create("Tran Thi B & 2026-06-01", "20260601T235959Z-ffffffff", 1)
    assert [i["uploadId"] for i in store.recent("2026-06-01")] == ["20260601T083005Z-0a1b2c3d",
                                                                     "20260601T235959Z-ffffffff"]
    assert store.recent("2026-05-31") == []


def test_intake_status_shows_the_newest_upload_of_each_person_day_this_week():
    svc, _, _ = _intake(switch="true")
    svc.status.create(_RUN_PD, "20260602T080000Z-00000001", 3)
    svc.status.create(_RUN_PD, "20260607T080000Z-00000002", 4)          # re-uploaded: this one shows
    svc.status.create("Tran Thi B & 2026-06-05", "20260605T080000Z-00000003", 1)
    svc.status.create("Tran Thi B & 2026-05-20", "20260530T080000Z-00000004", 1)   # 9 days ago: not shown
    code, body = _call(svc, "/api/status")
    assert code == 200 and body["syncEnabled"] is True
    rows = body["folders"]
    assert [(r["personDay"], r["uploadId"], r["imageCount"], r["uploads"]) for r in rows] == [
        (_RUN_PD, "20260607T080000Z-00000002", 4, 2),
        ("Tran Thi B & 2026-06-05", "20260605T080000Z-00000003", 1, 1)]
    assert all(r["status"] == "Uploading" and "lastError" in r and "lastRunAt" in r for r in rows)


def test_intake_finds_day_sheets_by_folded_name_and_date_with_short_links():
    svc, _, s3 = _intake()
    for key in ("results/Nguyễn Văn A & 2026-06-01.xlsx", "results/Nguyen Van An & 2026-06-01.xlsx",
                "results/Nguyễn Văn A & 2026-06-02.xlsx", "results/Tran Thi B & 2026-06-01.xlsx",
                "results/old/Nguyen Van A & 2026-06-01.xlsx", "results/notes.txt"):
        s3.objects[unicodedata.normalize("NFC", key)] = b"x"
    s3.modified[unicodedata.normalize("NFC", "results/Nguyễn Văn A & 2026-06-02.xlsx")] = \
        _dt.datetime(2026, 6, 2, 10, 0, tzinfo=_dt.timezone.utc)
    code, body = _call(svc, "/api/sheets", {"name": "nguyen van a", "date": ""})
    assert code == 200 and not body["more"]
    assert [s["personDay"] for s in body["sheets"]] == [
        unicodedata.normalize("NFC", "Nguyễn Văn A & 2026-06-02"),
        "Nguyen Van An & 2026-06-01", unicodedata.normalize("NFC", "Nguyễn Văn A & 2026-06-01")]
    assert body["sheets"][0]["updatedAt"] == "2026-06-02T10:00:00Z"
    get = s3.gets[0]
    assert get["method"] == "get_object" and get["ExpiresIn"] == 300
    disposition = get["Params"]["ResponseContentDisposition"]
    assert disposition.startswith('attachment; filename="Nguyen Van A & 2026-06-02.xlsx"; filename*=UTF-8\'\'')
    assert urllib.parse.unquote(disposition.split("''", 1)[1]) == unicodedata.normalize(
        "NFC", "Nguyễn Văn A & 2026-06-02.xlsx")
    code, body = _call(svc, "/api/sheets", {"name": "", "date": "2026-06-01"})
    assert len(body["sheets"]) == 3 and "Tran Thi B & 2026-06-01" in [s["personDay"] for s in body["sheets"]]
    assert _call(svc, "/api/sheets", {"name": "", "date": ""})[0] == 400
    assert _call(svc, "/api/sheets", {"name": "a", "date": "1/6/2026"})[0] == 400


def test_intake_answers_bad_requests_in_the_web_apps_shape():
    svc, _, _ = _intake()
    assert _call(svc, "/api/hello", raw="{not json")[0] == 400
    assert _call(svc, "/api/hello", raw="[1, 2]")[0] == 400
    code, body = _call(svc, "/api/check", raw=json.dumps({"folders": [{"folder": "x" * 1_100_000}]}))
    assert code == 413 and body["ok"] is False
    assert _call(svc, "/api/nothing")[0] == 404
    assert _call(svc, "/api/status", method="GET")[0] == 404
    assert _call(svc, "/api/check", {"folders": []})[0] == 400

    def broken():
        raise RuntimeError("Nguyen Van A secret detail")

    res = intake.handle({"rawPath": "/api/hello", "requestContext": {"http": {"method": "POST"}},
                         "headers": {"x-calls-log-passphrase": _PASS}, "body": "{}"}, broken)
    assert res["statusCode"] == 500 and "Nguyen" not in res["body"] and "secret" not in res["body"]


def test_intake_logs_no_passphrase_name_or_link():
    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Keep(level=logging.DEBUG)
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        svc, _, s3 = _intake()
        up = _upload(svc, s3, ["a.jpg"])
        s3.objects[f"inbox/{up['uploadId']}/a.jpg"] = b"x"
        _call(svc, "/api/ready", {"personDay": up["personDay"], "uploadId": up["uploadId"]})
        _call(svc, "/api/hello", passphrase="a wrong guess here")
        s3.objects["results/Nguyen Van A & 2026-06-01.xlsx"] = b"x"
        _call(svc, "/api/sheets", {"name": "nguyen", "date": ""})
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    text = "\n".join(records)
    assert up["uploadId"] in text and '"result": "401"' in text
    for private in (_PASS, "a wrong guess here", "Nguyen", "nguyen", "signed-", "amazonaws"):
        assert private not in text, private


def test_intake_s3_client_presigns_the_regional_endpoint():
    try:
        import boto3  # noqa: F401  -- in the runner image; the PC has none
    except ImportError:
        return
    with mock.patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIDOFFLINETEST", "AWS_SECRET_ACCESS_KEY": "offline",
                                      "AWS_SESSION_TOKEN": "offline"}):
        s3 = intake.s3_client("ap-southeast-2")
        post = s3.generate_presigned_post(Bucket=_IN_BUCKET, Key="inbox/x/a.jpg", ExpiresIn=60)
        url = s3.generate_presigned_url("get_object", Params={"Bucket": _IN_BUCKET, "Key": "results/a.xlsx"},
                                        ExpiresIn=60)
    assert post["url"] == f"https://{_IN_BUCKET}.s3.ap-southeast-2.amazonaws.com/"
    assert url.startswith(f"https://{_IN_BUCKET}.s3.ap-southeast-2.amazonaws.com/results/a.xlsx?")


def test_upload_page_script_is_valid_javascript():
    node = _shutil.which("node")
    if node is None:   # not in the runner image; CI's Node step and the PC run it
        return
    html = intake.PAGE.read_text(encoding="utf-8")
    (script,) = _re.findall(r"<script>(.*?)</script>", html, _re.S)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "page.js"
        path.write_text(script, encoding="utf-8")
        done = _subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


class _ThrottleLambda(_FakeLambda):
    def __init__(self, reserved=2, fail=False):
        super().__init__([])
        self.reserved, self.fail_put, self.puts = reserved, fail, []

    def get_function_concurrency(self, FunctionName):
        return {"ReservedConcurrentExecutions": self.reserved}

    def put_function_concurrency(self, FunctionName, ReservedConcurrentExecutions):
        if self.fail_put:
            raise TimeoutError("simulated")
        self.puts.append((FunctionName, ReservedConcurrentExecutions))


_KS_BOTH = dataclasses.replace(_KS_SETTINGS, intake_function="calls-log-intake")


def _intake_alarm():
    return {"alarmData": {"alarmName": "calls-log-intake-runaway",
                          "state": {"value": "ALARM", "reason": "Threshold Crossed: 1 datapoint was >= 600.0"}}}


def test_kill_switch_turns_the_upload_page_off_for_its_alarm_only():
    lam, sns = _ThrottleLambda(), _FakeSns()
    out = killswitch.respond(killswitch.alarm_of(_intake_alarm()), _KS_BOTH, lam, sns)
    assert lam.puts == [("calls-log-intake", 0)] and lam.listed == [] and lam.updates == []
    assert out["intake"] == {"function": "calls-log-intake", "was": 2}
    (mail,) = sns.sent
    assert "upload page is off" in mail["Subject"] and "Turning the upload page back on" in mail["Message"]
    lam2, sns2 = _ThrottleLambda(), _FakeSns()
    lam2.pages = [{"EventSourceMappings": [{"UUID": "u-1", "State": "Enabled"}]}]
    killswitch.respond(killswitch.alarm_of(_alarm_event()), _KS_BOTH, lam2, sns2)
    assert lam2.puts == [] and lam2.updates == [("u-1", False)]   # a runner alarm leaves the page alone


def test_kill_switch_turns_the_page_off_for_the_slow_guesser_alarm_too():
    lam, sns = _ThrottleLambda(), _FakeSns()
    event = _intake_alarm()
    event["alarmData"]["alarmName"] = "calls-log-intake-busy"
    killswitch.respond(killswitch.alarm_of(event), _KS_BOTH, lam, sns)
    assert lam.puts == [("calls-log-intake", 0)] and lam.updates == []


def test_kill_switch_emails_when_it_cannot_turn_the_page_off():
    lam, sns = _ThrottleLambda(fail=True), _FakeSns()
    try:
        killswitch.respond(killswitch.alarm_of(_intake_alarm()), _KS_BOTH, lam, sns)
    except TimeoutError:
        pass
    else:
        raise AssertionError("the failure must reach Lambda, which retries the invocation")
    assert "could NOT stop the upload page" in sns.sent[0]["Subject"]


# ---------------------------------------------------------------------------
# Epic 5: importing the PC's history, comparing PC and AWS, the writer
# switches and the turnaround report (tools/import_outputs.py,
# compare_outputs.py, switches.py, turnaround.py). Day Sheets are made up.
# ---------------------------------------------------------------------------

from session_builder import Session as _Session

from tools import compare_outputs, import_outputs, switches, turnaround


class _ScanDynamo(_FakeDynamo):
    def scan(self, TableName, FilterExpression, ExpressionAttributeValues, ProjectionExpression):
        assert FilterExpression == "imported = :t" and ProjectionExpression == "personDay"
        want = ExpressionAttributeValues[":t"]["S"]
        return {"Items": [{"personDay": {"S": i["personDay"]}} for i in self.items.values()
                          if i.get("imported") == want]}


class _HistoryCli(_FakeCli):
    """Adds results/ versions with metadata and SHA-256, downloads, a scan and the SSM switch."""

    def __init__(self, fail_after=None, switch="false"):
        super().__init__()
        self.dynamo = _ScanDynamo()
        self.versions, self.switch, self.fail_after = {}, switch, fail_after

    def put_version(self, key, data, meta=None):
        self.versions.setdefault(key, []).append((data, dict(meta or {})))
        self.keys = sorted(set(self.keys) | {key})

    def __call__(self, cmd, capture_output, text, encoding, errors):
        assert cmd[cmd.index("--region") + 1] == "ap-southeast-2"
        op = f"{cmd[1]} {cmd[2]}"

        def arg(flag):
            return cmd[cmd.index(flag) + 1]

        if op == "s3api put-object":
            self.log.append(op)
            if self.fail_after is not None:
                if self.fail_after == 0:
                    return SimpleNamespace(returncode=1, stdout="", stderr="connection reset")
                self.fail_after -= 1
            assert arg("--checksum-algorithm") == "SHA256"
            name, _, value = arg("--metadata").partition("=")
            self.put_version(arg("--key"), Path(arg("--body")).read_bytes(), {name: value})
            return _ok('{"ETag": "\\"x\\""}')
        if op == "s3api head-object":
            self.log.append(op)
            assert arg("--checksum-mode") == "ENABLED"
            data, meta = self.versions[arg("--key")][-1]
            return _ok(json.dumps({"Metadata": meta, "ChecksumSHA256": _b64.b64encode(
                hashlib.sha256(data).digest()).decode()}))
        if op == "s3 cp" and cmd[3].startswith("s3://"):
            self.log.append("s3 cp down")
            Path(cmd[4]).write_bytes(self.versions[cmd[3].split("/", 3)[3]][-1][0])
            return _ok()
        if op == "ssm get-parameter":
            self.log.append(op)
            return _ok(json.dumps({"Parameter": {"Name": "/calls-log/sync_enabled", "Value": self.switch}}))
        return super().__call__(cmd, capture_output, text, encoding, errors)


def _day_sheet(path, pairs):
    sessions = [_Session(n, a, b, 30, 1) for n, (a, b) in enumerate(pairs, 1)]
    with mock.patch.object(excel_writer, "log"):
        excel_writer.write_sessions(_TEMPLATE, path, sessions)
    return path


def _run_tool(module, argv, cli, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv, run=cli, **kw)
    return code, out.getvalue() + err.getvalue()


def _history(folder):
    out = Path(folder)
    _day_sheet(out / "Nguyen Van A & 2026-06-01.xlsx", [("08:00", "09:00")])
    _day_sheet(out / "Nguyen Van A & 2026-06-01(1).xlsx", [("08:00", "09:30")])
    _day_sheet(out / "Nguyen Van A & 2026-06-01(2).xlsx", [("08:00", "10:00")])
    _day_sheet(out / "Tran Thi B & 2026-06-02(3).xlsx", [("13:00", "14:00")])
    _day_sheet(out / "Tran Thi B & 2026-06-02(1).xlsx", [("13:00", "15:00")])
    (out / "notes.xlsx").write_bytes(b"not a day sheet")
    (out / "~$Tran Thi B & 2026-06-02(1).xlsx").write_bytes(b"lock file")
    return out


def test_import_orders_versions_so_resync_outputs_choice_is_current():
    import resync_output

    with tempfile.TemporaryDirectory() as d:
        out = _history(d)
        plans = import_outputs.plan(out)
        chosen = resync_output.collect_files(out)
        assert [(p.day.key, [f.name for f in p.files]) for p in plans] == [
            ("Nguyen Van A & 2026-06-01", ["Nguyen Van A & 2026-06-01(1).xlsx",
                                           "Nguyen Van A & 2026-06-01(2).xlsx", "Nguyen Van A & 2026-06-01.xlsx"]),
            ("Tran Thi B & 2026-06-02", ["Tran Thi B & 2026-06-02(3).xlsx", "Tran Thi B & 2026-06-02(1).xlsx"])]
        for p in plans:
            assert p.current == chosen[(p.day.name, p.day.date)]


def test_import_uploads_every_version_marks_done_and_a_second_run_changes_nothing():
    cli = _HistoryCli()
    with tempfile.TemporaryDirectory() as d:
        out = _history(d)
        code, said = _run_tool(import_outputs, ["--output", str(out)], cli)
        assert code == 0, said
        key = "results/Nguyen Van A & 2026-06-01.xlsx"
        assert [v[0] for v in cli.versions[key]] == [(out / n).read_bytes() for n in (
            "Nguyen Van A & 2026-06-01(1).xlsx", "Nguyen Van A & 2026-06-01(2).xlsx", "Nguyen Van A & 2026-06-01.xlsx")]
        items = list(cli.dynamo.items.values())
        assert sorted(i["personDay"] for i in items) == ["Nguyen Van A & 2026-06-01", "Tran Thi B & 2026-06-02"]
        assert all(i["status"] == "Done" and i["imported"] == "true" and "upDay" not in i for i in items)
        assert "PC Person-Days: 2 = imported 2 + imported before 0 + processed in AWS 0" in said
        puts, writes = cli.log.count("s3api put-object"), cli.dynamo.writes
        code, said = _run_tool(import_outputs, ["--output", str(out)], cli)
        assert code == 0 and cli.log.count("s3api put-object") == puts and cli.dynamo.writes == writes
        assert "= imported 0 + imported before 2 + processed in AWS 0" in said


def test_import_leaves_a_day_aws_processed_itself_alone():
    cli = _HistoryCli()
    cli.put_version("results/Tran Thi B & 2026-06-02.xlsx", b"the runner's own Day Sheet")
    with tempfile.TemporaryDirectory() as d:
        code, said = _run_tool(import_outputs, ["--output", str(_history(d))], cli)
    assert code == 0 and cli.versions["results/Tran Thi B & 2026-06-02.xlsx"] == [(b"the runner's own Day Sheet", {})]
    assert "left alone, AWS processed it itself: Tran Thi B & 2026-06-02" in said
    assert "= imported 1 + imported before 0 + processed in AWS 1" in said


def test_import_cut_short_is_finished_by_the_next_run():
    cli = _HistoryCli(fail_after=1)
    with tempfile.TemporaryDirectory() as d:
        out = _history(d)
        code, said = _run_tool(import_outputs, ["--output", str(out)], cli)
        assert code == 1 and "Run it again" in said and cli.dynamo.writes == 0
        cli.fail_after = None
        code, said = _run_tool(import_outputs, ["--output", str(out)], cli)
        assert code == 0, said
        key = "results/Nguyen Van A & 2026-06-01.xlsx"
        assert cli.versions[key][-1][0] == (out / "Nguyen Van A & 2026-06-01.xlsx").read_bytes()
        assert len([i for i in cli.dynamo.items.values() if i["personDay"] == _RUN_PD]) == 1


def test_import_dry_run_changes_nothing():
    cli = _HistoryCli()
    with tempfile.TemporaryDirectory() as d:
        code, said = _run_tool(import_outputs, ["--output", str(_history(d)), "--dry-run"], cli)
    assert code == 0 and "s3api put-object" not in cli.log and cli.dynamo.writes == 0
    assert "would import Nguyen Van A & 2026-06-01: 3 version(s), current Nguyen Van A & 2026-06-01.xlsx" in said


def test_import_verify_checks_the_count_and_the_checksums():
    cli = _HistoryCli()
    with tempfile.TemporaryDirectory() as d:
        out = _history(d)
        _run_tool(import_outputs, ["--output", str(out)], cli)
        code, said = _run_tool(import_outputs, ["--output", str(out), "--verify"], cli)
        assert code == 0 and "with a Day Sheet in AWS: 2" in said and "2 of 2 sampled imports match" in said
        cli.put_version("results/Tran Thi B & 2026-06-02.xlsx", b"changed since", {"import-sha256": "x"})
        code, said = _run_tool(import_outputs, ["--output", str(out), "--verify"], cli)
        assert code == 1 and "DIFFERENT Tran Thi B & 2026-06-02" in said
        _day_sheet(out / "Le Van C & 2026-06-03.xlsx", [("09:00", "10:00")])
        code, said = _run_tool(import_outputs, ["--output", str(out), "--verify"], cli)
        assert code == 1 and "missing in AWS: Le Van C & 2026-06-03" in said


def test_compare_shows_labels_dates_and_times_only():
    cli = _HistoryCli()
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as private:
        out = Path(d)
        same = _day_sheet(out / "Nguyen Van A & 2026-06-01.xlsx", [("08:00", "09:00"), ("10:00", "11:00")])
        _day_sheet(out / "Tran Thi B & 2026-06-01.xlsx", [("08:00", "09:00"), ("10:00", "11:00")])
        _day_sheet(out / "Le Van C & 2026-06-02.xlsx", [("08:00", "09:00")])
        _day_sheet(out / "Nguyen Van A & 2026-05-31.xlsx", [("08:00", "09:00")])   # before --since
        other = _day_sheet(Path(private) / "aws.xlsx", [("08:00", "09:00"), ("10:00", "11:05")])
        cli.put_version("results/Nguyen Van A & 2026-06-01.xlsx", same.read_bytes())
        cli.put_version("results/Tran Thi B & 2026-06-01.xlsx", other.read_bytes())
        cli.put_version("results/Pham Van D & 2026-06-02.xlsx", same.read_bytes())
        key = Path(private) / "key.txt"
        code, said = _run_tool(compare_outputs, ["--output", str(out), "--since", "2026-06-01",
                                                 "--key", str(key)], cli)
        assert code == 1
        lines = said.strip().splitlines()
        assert lines == ["F1 2026-06-01: 2 sessions -> IDENTICAL",
                         "F2 2026-06-01: session 2: PC 10:00-11:00, AWS 10:00-11:05 -> DIFFERENT",
                         "F3 2026-06-02: MISSING IN AWS",
                         "F4 2026-06-02: MISSING ON THE PC",
                         "1 of 4 day(s) IDENTICAL"]
        for name in ("Nguyen", "Tran", "Le Van", "Pham"):
            assert name not in said
        assert key.read_text(encoding="utf-8").splitlines()[1] == "F2\tTran Thi B & 2026-06-01"
        code, said = _run_tool(compare_outputs, ["--output", str(out), "--date", "2026-6-1"], cli)
        assert code == 1 and said.count("-> ") == 2
        inside = Path(__file__).resolve().parent / "compare-key.txt"
        try:
            assert _run_tool(compare_outputs, ["--output", str(out), "--date", "2026-06-01", "--key", str(inside)],
                             cli)[0] == 2 and not inside.exists()
        finally:
            inside.unlink(missing_ok=True)   # a broken check must not leave names in the repo


def test_compare_all_identical_is_exit_zero():
    cli = _HistoryCli()
    with tempfile.TemporaryDirectory() as d:
        sheet = _day_sheet(Path(d) / "Nguyen Van A & 2026-06-01.xlsx", [("08:00", "09:00")])
        cli.put_version("results/Nguyen Van A & 2026-06-01.xlsx", sheet.read_bytes())
        code, said = _run_tool(compare_outputs, ["--output", d, "--date", "2026-06-01"], cli)
    assert code == 0 and "1 of 1 day(s) IDENTICAL" in said


def test_switches_show_both_writers_and_change_neither():
    cli = _HistoryCli(switch="false")
    code, said = _run_tool(switches, [], cli, pc_on=True)
    assert code == 0
    assert "PC      on                   config.py: sheets_sync_enabled = False" in said
    assert "AWS     off                  tools/put_parameters.py --sync-enabled true" in said
    cli.switch = "true"
    assert "Both write" in _run_tool(switches, [], cli, pc_on=True)[1]
    assert "Nobody writes" in _run_tool(switches, [], _HistoryCli(switch="no"), pc_on=False)[1]
    assert cli.log == ["ssm get-parameter", "ssm get-parameter"] and cli.dynamo.writes == 0


def test_switches_say_unknown_when_aws_is_unreadable():
    def run(cmd, capture_output, text, encoding, errors):
        return SimpleNamespace(returncode=255, stdout="", stderr="Unable to locate credentials")

    code, said = _run_tool(switches, [], run, pc_on=False)
    assert code == 1 and "AWS     unknown" in said


def test_status_records_ready_time_on_ready_and_requeue():
    store, now = _store()
    store.create(_RUN_PD, _RUN_UP, 2)
    assert "readyAt" not in store.get(_RUN_PD, _RUN_UP)
    store.mark_ready(_RUN_PD, _RUN_UP)
    first = store.get(_RUN_PD, _RUN_UP)["readyAt"]
    store.begin_run(_RUN_PD, _RUN_UP, "run-1")
    store.finish(_RUN_PD, _RUN_UP)
    now[0] += 3600
    store.requeue(_RUN_PD, _RUN_UP)
    assert store.get(_RUN_PD, _RUN_UP)["readyAt"] > first


def test_status_imported_day_is_done_and_kept_out_of_the_page_view():
    store, _ = _store()
    store.record_import(_RUN_PD, _RUN_UP)
    item = store.get(_RUN_PD, _RUN_UP)
    assert item["status"] == "Done" and item["imported"] == "true" and item["imageCount"] == 0
    assert store.recent(fstatus.up_day(_RUN_UP)) == []
    try:
        store.record_import(_RUN_PD, _RUN_UP)
    except fstatus.TransitionRefused:
        pass
    else:
        raise AssertionError("the same import item twice must be refused")


def test_turnaround_reports_median_and_p95_against_two_hours():
    cli = _HistoryCli()
    store = fstatus.FolderStatus(cli.dynamo, "calls-log-folders")
    waits = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]   # minutes
    for n, minutes in enumerate(waits):
        up = f"20260605T08{n:02d}00Z-0000000{n}"
        store.create(f"Nguyen Van A & 2026-06-{n + 1:02d}", up, 1)
        item = cli.dynamo.items[(f"pd#Nguyen Van A & 2026-06-{n + 1:02d}", f"up#{up}")]
        ready = _dt.datetime(2026, 6, 5, 9, 0, tzinfo=_dt.timezone.utc)
        item.update(status="Done", readyAt=ready.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    updatedAt=(ready + _dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    store.create("Tran Thi B & 2026-06-05", "20260605T120000Z-0000000a", 1)   # still Uploading
    code, said = _run_tool(turnaround, ["--days", "7"], cli, today=_dt.date(2026, 6, 8))
    assert code == 0, said
    assert "Uploads in the last 7 day(s): 11  Done 10  Uploading 1" in said
    assert "median 50 min, p95 1 h 40 min (within the 2 h target), slowest 1 h 40 min" in said
    assert "Nguyen" not in said and "Tran" not in said
    item["updatedAt"] = (ready + _dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _run_tool(turnaround, [], cli, today=_dt.date(2026, 6, 8))[0] == 3


class _WebApp:
    """The Web App's doPost order: the secret first, then the name (apps_script/WebAppSync.gs)."""

    def __init__(self, secret, fail=False):
        self.secret, self.fail, self.bodies = secret, fail, []

    def __call__(self, req, timeout):
        if self.fail:
            raise OSError("unreachable")
        body = json.loads(req.data)
        self.bodies.append(body)
        if body["secret"] != self.secret:
            answer = {"ok": False, "error": "bad secret"}
        else:
            answer = {"ok": False, "error": "unknown name: " + body["name"]}
        return _io.BytesIO(json.dumps(answer).encode("utf-8"))


def test_check_secret_probes_without_reaching_a_sheet_and_never_prints_it():
    from tools import check_secret

    cfg = SimpleNamespace(sheets_webapp_url="https://script.google.com/macros/s/FICTIONAL/exec",
                          sheets_sync_secret="the-new-secret")
    app = _WebApp("the-new-secret")
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        assert check_secret.main([], opener=app, config=cfg) == 0
        assert check_secret.main(["--old"], opener=app, config=cfg, ask=lambda p: "the-old-secret") == 0
        assert check_secret.main(["--old"], opener=app, config=cfg, ask=lambda p: "the-new-secret") == 1
        assert check_secret.main([], opener=_WebApp("x", fail=True), config=cfg) == 1
    said = out.getvalue()
    assert "ACCEPTED (expected ACCEPTED)" in said and "REFUSED (expected REFUSED)" in said
    assert all(b["name"] == "zz-secret-check" and b["sessions"] == [] for b in app.bodies)
    assert "the-new-secret" not in said and "the-old-secret" not in said


# ---------------------------------------------------------------------------
# The Owner tab (spec-4-5): routes under /api/owner/ open with the owner
# password only (owner_passphrase_hash, x-calls-log-owner); re-run, re-send,
# runner stop/start and timings reuse cloud/requeue.py, the resync message,
# cloud/killswitch.py and cloud/timing.py.
# ---------------------------------------------------------------------------

from cloud import requeue as crequeue, timing as ctiming, killswitch as ckill

_OWNER = "owner password, quite long"
_OWNER_HASH = cpass.hash_passphrase(_OWNER, iterations=1000)


class _EsmLambda:
    """The runner's queue trigger(s); an update takes effect at once."""

    def __init__(self, states=("Enabled",), fail=False):
        self.mappings = [{"UUID": f"u{i}", "State": s} for i, s in enumerate(states)]
        self.fail, self.updates, self.functions = fail, [], []

    def list_event_source_mappings(self, FunctionName, Marker=None):
        if self.fail:
            raise TimeoutError("simulated")
        self.functions.append(FunctionName)
        return {"EventSourceMappings": [dict(m) for m in self.mappings]}

    def update_event_source_mapping(self, UUID, Enabled):
        self.updates.append((UUID, Enabled))
        for m in self.mappings:
            if m["UUID"] == UUID:
                m["State"] = "Enabled" if Enabled else "Disabled"


def _owner_intake(owner_hash=_OWNER_HASH, lam=None, runner="calls-log-runner", sqs=None):
    svc, dynamo, s3 = _intake(values=dict(_IN_VALUES, owner_passphrase_hash=owner_hash), sqs=sqs)
    svc.settings = dataclasses.replace(svc.settings, runner_function=runner)
    svc.lambda_client = lam if lam is not None else _EsmLambda()
    return svc, dynamo, s3


def _owner(svc, path, body=None, owner=_OWNER, passphrase=None):
    return _call(svc, path, body, passphrase=passphrase, owner=owner)


def test_owner_routes_open_with_the_owner_password_only():
    svc, _, _ = _owner_intake()
    svc.status = svc.storage = svc.s3 = svc.sqs = svc.lambda_client = _Tripwire()
    for path in intake.OWNER_ROUTES:
        for given, upload in ((None, None), ("", _PASS), (_PASS, None), (_PASS, _PASS), (_OWNER.upper(), None)):
            code, body = _owner(svc, path, owner=given, passphrase=upload)
            assert code == 403 and body["code"] == "wrong_owner", (path, given)
    # and the owner password never opens the upload routes
    code, body = _call(svc, "/api/status", passphrase=_OWNER)
    assert code == 401 and body["code"] == "wrong_passphrase"


def test_owner_tab_says_so_until_its_password_is_set():
    svc, _, _ = _owner_intake(owner_hash="unset")
    code, body = _owner(svc, "/api/owner/hello")
    assert code == 503 and body["code"] == "owner_not_set" and "--owner-passphrase" in body["error"]


def test_both_passwords_stay_remembered_side_by_side():
    svc, _, _ = _owner_intake()
    intake._verified.clear()
    with mock.patch.object(intake.passphrase_mod, "verify", wraps=cpass.verify) as verify:
        for _ in range(3):
            assert _call(svc, "/api/hello")[0] == 200
            assert _owner(svc, "/api/owner/hello")[0] == 200
        assert verify.call_count == 2   # one PBKDF2 each; alternating doesn't evict the other


def test_owner_hello_shows_the_switch_and_the_runner():
    svc, _, _ = _owner_intake(lam=_EsmLambda(("Disabled",)))
    code, body = _owner(svc, "/api/owner/hello")
    assert code == 200 and body == {"ok": True, "syncEnabled": False, "runner": "stopped"}
    svc.lambda_client = _EsmLambda(fail=True)
    assert _owner(svc, "/api/owner/hello")[1]["runner"] is None   # unreadable: shown as unknown, the tab opens


def _inbox(s3, upload_id, n):
    for i in range(n):
        s3.objects[f"inbox/{upload_id}/shot_{i}.jpg"] = b"x"


def test_owner_requeue_runs_a_stored_folder_again():
    sqs = _FakeSqs()
    svc, _, s3 = _owner_intake(sqs=sqs)
    _stored_upload(svc.status, _RQ_OLD, "Done")
    _stored_upload(svc.status, _RQ_NEW, "Failed")
    _inbox(s3, _RQ_OLD, 2)
    _inbox(s3, _RQ_NEW, 2)
    code, body = _owner(svc, "/api/owner/requeue", {"personDay": _RUN_PD})      # the newest by default
    assert code == 200 and body == {"ok": True, "uploadId": _RQ_NEW, "images": 2, "status": "Pending"}
    item = svc.status.get(_RUN_PD, _RQ_NEW)
    assert item["status"] == "Pending" and item["attempts"] == 0 and "lastError" not in item
    assert sqs.sent[-1]["body"] == {"uploadId": _RQ_NEW, "personDay": _RUN_PD, "mode": "process"}
    code, body = _owner(svc, "/api/owner/requeue", {"personDay": _RUN_PD, "uploadId": _RQ_OLD})
    assert code == 200 and body["uploadId"] == _RQ_OLD and len(sqs.sent) == 2
    code, body = _owner(svc, "/api/owner/requeue", {"personDay": _RUN_PD})      # Pending: queued again
    assert code == 200 and body["queuedAgain"] and len(sqs.sent) == 3


def test_owner_requeue_refuses_and_changes_nothing():
    sqs = _FakeSqs()
    svc, _, s3 = _owner_intake(sqs=sqs)
    _stored_upload(svc.status, _RQ_NEW, "Processing")
    _inbox(s3, _RQ_NEW, 2)
    code, body = _owner(svc, "/api/owner/requeue", {"personDay": _RUN_PD})
    assert code == 409 and body["code"] == "not_requeueable" and body["args"]["status"] == "Processing"
    svc2, _, s3b = _owner_intake(sqs=sqs)
    _stored_upload(svc2.status, _RQ_NEW, "Done")
    _inbox(s3b, _RQ_NEW, 1)                                                    # one of two images left
    code, body = _owner(svc2, "/api/owner/requeue", {"personDay": _RUN_PD})
    assert code == 409 and body["code"] == "images_gone" and body["args"] == {"arrived": 1, "declared": 2}
    assert svc2.status.get(_RUN_PD, _RQ_NEW)["status"] == "Done"
    assert _owner(svc2, "/api/owner/requeue", {"personDay": "Tran Thi B & 2026-06-01"})[1]["code"] == "no_upload"
    assert _owner(svc2, "/api/owner/requeue", {"personDay": "holiday photos"})[1]["code"] == "bad_day"
    assert _owner(svc2, "/api/owner/requeue", {"personDay": _RUN_PD, "uploadId": "../x"})[0] == 400
    assert sqs.sent == []


def test_owner_requeue_and_the_pc_tool_share_one_rule():
    assert enqueue.REQUEUEABLE == crequeue.REQUEUEABLE == ("Done", "Failed", "Parked", "Held", "Superseded")


def test_owner_resend_queues_only_a_stored_day_sheet():
    sqs = _FakeSqs()
    svc, _, s3 = _owner_intake(sqs=sqs)
    s3.objects[naming.result_key(naming.parse(_RUN_PD))] = b"xlsx"
    s3.objects["results/Nguyen Van A & 2026-06-01 (1).xlsx"] = b"not the current one"
    code, body = _owner(svc, "/api/owner/resend", {"personDay": _RUN_PD})
    assert code == 200 and sqs.sent == [{"QueueUrl": "https://sqs/calls-log-runs",
                                         "body": {"personDay": _RUN_PD, "mode": "resync"}}]
    code, body = _owner(svc, "/api/owner/resend", {"personDay": "Nguyen Van A & 2026-06-02"})
    assert code == 404 and body["code"] == "no_sheet" and len(sqs.sent) == 1


def test_owner_stops_and_starts_only_the_runner_trigger():
    lam = _EsmLambda(("Enabled",))
    svc, _, _ = _owner_intake(lam=lam)
    assert _owner(svc, "/api/owner/runner", {"action": "status"})[1] == {"ok": True, "runner": "running", "changed": 0}
    assert _owner(svc, "/api/owner/runner", {"action": "stop"})[1] == {"ok": True, "runner": "stopped", "changed": 1}
    assert _owner(svc, "/api/owner/runner", {"action": "stop"})[1]["changed"] == 0     # already off
    assert _owner(svc, "/api/owner/runner", {"action": "start"})[1] == {"ok": True, "runner": "running", "changed": 1}
    assert lam.updates == [("u0", False), ("u0", True)] and set(lam.functions) == {"calls-log-runner"}
    assert _owner(svc, "/api/owner/runner", {"action": "delete"})[1]["code"] == "bad_action"
    svc.lambda_client = _EsmLambda(fail=True)
    assert _owner(svc, "/api/owner/runner", {"action": "stop"})[1]["code"] == "runner_failed"
    svc2, _, _ = _owner_intake(runner="")
    assert _owner(svc2, "/api/owner/runner", {"action": "start"})[0:2] == (
        503, {"ok": False, "error": "The page doesn't know the runner (CALLS_LOG_RUNNER_FUNCTION is not set).",
              "code": "no_runner", "args": {}})


def test_runner_state_names_the_mixed_and_missing_cases():
    assert ckill.runner_state(_EsmLambda(()), "f") == "missing"
    assert ckill.runner_state(_EsmLambda(("Disabling",)), "f") == "changing"
    assert ckill.runner_state(_EsmLambda(("Enabled", "Disabled")), "f") == "stopped"
    lam = _EsmLambda(("Disabled", "Enabled", "Disabling"))
    assert [u for u, _ in [(s["uuid"], s["was"]) for s in ckill.start_runner(lam, "f")]] == ["u0", "u2"]


def test_owner_timings_are_the_turnaround_tools_numbers():
    svc, _, _ = _owner_intake()
    svc.now = lambda: _dt.datetime(2026, 6, 8, 6, 30, tzinfo=_dt.timezone.utc)
    items = [{"uploadId": f"20260608T00{m:02d}00Z-0a1b2c3d", "status": "Done", "readyAt": "2026-06-08T01:00:00Z",
              "updatedAt": f"2026-06-08T01:{m:02d}:00Z"} for m in (5, 10, 40)]
    items += [{"uploadId": "20260605T063000Z-0a1b2c3d", "status": "Failed"},     # 3 x 24 h ago exactly: in
              {"uploadId": "20260607T120000Z-0a1b2c3d", "status": "Pending"}]
    before = {"uploadId": "20260605T062959Z-0a1b2c3d", "status": "Done",       # a second too early: out
              "readyAt": "2026-06-05T07:00:00Z", "updatedAt": "2026-06-05T09:00:00Z"}
    by_day = {"2026-06-08": items[:3], "2026-06-07": items[4:], "2026-06-05": [before, items[3]]}
    asked = []
    svc.status.recent = lambda day: asked.append(day) or by_day.get(day, [])
    code, body = _owner(svc, "/api/owner/timings", {"days": 3})
    # The last 3 x 24 hours, not 3 UTC dates: the oldest date is read too, then cut at the hour.
    assert code == 200 and asked == ["2026-06-08", "2026-06-07", "2026-06-06", "2026-06-05"]
    assert body == {"ok": True, "days": 3, "targetSeconds": 7200, **turnaround.summary(items)}
    assert body["uploads"] == 5 and body["counts"] == {"Done": 3, "Failed": 1, "Pending": 1}
    assert body["median"] == 600 and body["p95"] == body["slowest"] == 2400
    for days in (0, 29, "7", True):
        assert _owner(svc, "/api/owner/timings", {"days": days})[1]["code"] == "days_range"
    assert ctiming.summary is turnaround.summary


def test_put_parameters_stores_the_owner_password_hash_apart():
    aws = _FakeAws()
    answers, asked = iter([_OWNER, _OWNER]), []
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = put_parameters.main(["--owner-passphrase"], run=aws,
                                   ask=lambda prompt: asked.append(prompt) or next(answers))
    assert code == 0, out.getvalue() + err.getvalue()
    (stored,) = aws.inputs
    assert stored["Name"] == "/calls-log/owner_passphrase_hash" and cpass.verify(_OWNER, stored["Value"])
    assert "owner password" in asked[0] and _OWNER not in out.getvalue() + err.getvalue()


def test_settings_read_the_owner_password_hash_but_never_show_it():
    ssm = _FakeSsm(dict(_ST_VALUES, owner_passphrase_hash=_OWNER_HASH))
    got = settings.read(ssm, _ST_ENV)
    assert got.owner_passphrase_hash == _OWNER_HASH and _OWNER_HASH not in repr(got)
    assert settings.read(_FakeSsm(_ST_VALUES), _ST_ENV).owner_passphrase_hash == ""   # missing: not set up


def _page_words():
    page = intake.PAGE.read_text(encoding="utf-8")
    script = _re.search(r"<script>(.*?)</script>", page, _re.S).group(1)

    def keys(name):
        block = _re.search(rf"const {name} = \{{\n(.*?)\n  \}};", script, _re.S).group(1)
        return [m.group(1) for m in _re.finditer(r"^    (\w+): ", block, _re.M)]

    return page, script, keys("EN"), keys("VI")


def test_page_has_english_and_vietnamese_for_every_word_and_code():
    page, script, en, vi = _page_words()
    assert len(en) == len(set(en)) and len(vi) == len(set(vi)), "a key is defined twice"
    assert set(en) == set(vi), set(en) ^ set(vi)
    missing = [c for c in intake.CODES if f"e_{c}" not in en]
    assert not missing, missing
    # and every word is used somewhere: a key spelled out in the code, a data-i18n, or a family built at run time
    code = script[script.index("const WORDS"):]
    spelled = set(_re.findall(r'"(\w+)"', code)) | set(_re.findall(r'data-i18n="(\w+)"', page))
    unused = [k for k in en if k not in spelled and not _re.match(r"(e|st|run)_", k)]
    assert not unused, unused
    used = set(_re.findall(r'data-i18n="(\w+)"', page))
    used |= set(_re.findall(r'\bt\("(\w+)"[,)]', script)) | set(_re.findall(r'\{k: "(\w+)"[,}]', script))
    used |= {f"run_{s}" for s in ("running", "stopped", "changing", "missing", "unknown")}
    used |= {f"st_{s.value}" for s in fstatus.Status if s.value not in ("Skipped",)}
    assert used <= set(en), used - set(en)


def test_page_stays_inside_its_csp():
    page = intake.PAGE.read_text(encoding="utf-8")
    assert len(_re.findall(r"<script>", page)) == 1 and len(_re.findall(r"<style>", page)) == 1
    assert " style=" not in page and "javascript:" not in page and "<script src" not in page
    assert not _re.search(r"https?://", page), "the page loads nothing from elsewhere"


# ----- the Owner tab's settings (Story 4.5 part B) ------------------------------------------

from cloud import owner_settings as osettings  # noqa: E402

_TOPIC = "arn:aws:sns:ap-southeast-2:123456789012:calls-log-alerts"


class _WritableSsm(_FakeSsm):
    """The settings store, writable for the five Owner-tab names only (as the policy allows)."""

    def __init__(self, values, switch="false"):
        super().__init__(dict(values), switch)
        self.puts = []

    def put_parameter(self, Name, Value, Type, Overwrite):
        name = Name.removeprefix("/calls-log/")
        assert name in osettings.WRITABLE and Type == "SecureString" and Overwrite is True, Name
        self.puts.append(name)
        if name == "sync_enabled":
            self.switch = Value
        else:
            self.values[name] = Value


class _AlertsSns:
    def __init__(self, subs):
        self.subs, self.calls = list(subs), []

    def list_subscriptions_by_topic(self, TopicArn, NextToken=None):
        assert TopicArn == _TOPIC
        half = len(self.subs) // 2   # two pages, to cover NextToken
        if NextToken is None:
            return {"Subscriptions": self.subs[:half], "NextToken": "next"}
        return {"Subscriptions": self.subs[half:]}

    def subscribe(self, TopicArn, Protocol, Endpoint):
        self.calls.append(("subscribe", TopicArn, Protocol, Endpoint))
        self.subs.append({"Protocol": Protocol, "Endpoint": Endpoint, "SubscriptionArn": "PendingConfirmation"})

    def unsubscribe(self, SubscriptionArn):
        self.calls.append(("unsubscribe", SubscriptionArn))
        self.subs = [s for s in self.subs if s["SubscriptionArn"] != SubscriptionArn]


def _sub(address, n=None):
    return {"Protocol": "email", "Endpoint": address,
            "SubscriptionArn": f"{_TOPIC}:0000000{n}" if n is not None else "PendingConfirmation"}


def _settings_intake(subs=None):
    svc, _, _ = _owner_intake()
    svc.ssm = _WritableSsm(dict(_IN_VALUES, owner_passphrase_hash=_OWNER_HASH))
    svc.settings = dataclasses.replace(svc.settings, alerts_topic_arn=_TOPIC)
    svc.sns = _AlertsSns(subs if subs is not None else [_sub("owner@example.com", 1), _sub("new@example.com")])
    return svc


def test_owner_tab_writes_only_its_four_settings():
    assert set(osettings.WRITABLE) == {"sync_enabled", "intake_passphrase_hash", "owner_passphrase_hash",
                                       "name_prefixes"}
    svc = _settings_intake()
    try:
        intake._put(svc, "webapp_url", "https://example.com")
        raise AssertionError("webapp_url was written")
    except RuntimeError:
        pass
    assert svc.ssm.puts == []


def test_infra_grants_exactly_the_owner_tabs_four_settings():
    # infra/ isn't in the runner image (CI mounts it in), as for _infra_text().
    infra = Path(__file__).resolve().parent / "infra"
    if not infra.is_dir():
        return
    lists = []
    for tf in (infra / "intake.tf", infra / "bootstrap" / "main.tf"):
        text = tf.read_text(encoding="utf-8")
        lists += [set(_re.findall(r'"(\w+)"', m)) for m in _re.findall(r'\[("sync_enabled"[^\]]*)\]', text)]
    assert len(lists) == 2 and all(found == set(osettings.WRITABLE) for found in lists), lists


def test_owner_settings_show_the_switch_the_employees_and_the_alert_addresses():
    svc = _settings_intake()
    code, body = _owner(svc, "/api/owner/settings")
    assert code == 200 and body["syncEnabled"] is False and body["employees"] == ["nguyen van a", "tran thi b"]
    assert body["alerts"] == [
        {"address": "owner@example.com", "confirmed": True, "id": f"{_TOPIC}:00000001"},
        {"address": "new@example.com", "confirmed": False, "id": None}]
    assert svc.ssm.puts == []


def test_owner_turns_the_sync_switch_on_and_off():
    svc = _settings_intake()
    code, body = _owner(svc, "/api/owner/sync", {"on": True})
    assert code == 200 and body["syncEnabled"] is True and svc.ssm.switch == "true"
    code, body = _owner(svc, "/api/owner/sync", {"on": False})
    assert code == 200 and body["syncEnabled"] is False and svc.ssm.switch == "false"
    for bad in ("true", 1, None):
        assert _owner(svc, "/api/owner/sync", {"on": bad})[1]["code"] == "bad_switch"
    assert svc.ssm.puts == ["sync_enabled", "sync_enabled"]


def test_owner_changes_a_password_only_to_a_strong_and_different_one():
    svc = _settings_intake()
    for new, why in (("short", "password_short"), (" " + _OWNER + "x", "password_spaces"),
                     ("Mật khẩu quản lý 2026", "password_accents"), (None, "password_short")):
        assert _owner(svc, "/api/owner/password", {"which": "owner", "new": new})[1]["code"] == why
    assert _owner(svc, "/api/owner/password", {"which": "boss", "new": "a fine new one 2026"})[1]["code"] == "bad_which"
    # the upload passphrase can't become the owner password, nor the other way round
    assert _owner(svc, "/api/owner/password", {"which": "owner", "new": _PASS})[1]["code"] == "same_as_other"
    assert _owner(svc, "/api/owner/password", {"which": "upload", "new": _OWNER})[1]["code"] == "same_as_other"
    assert svc.ssm.puts == []
    intake._services = object()   # stands for this container's cached settings
    code, body = _owner(svc, "/api/owner/password", {"which": "upload", "new": "a fine new one 2026"})
    assert code == 200 and svc.ssm.puts == ["intake_passphrase_hash"] and intake._services is None
    assert cpass.verify("a fine new one 2026", svc.ssm.values["intake_passphrase_hash"])
    assert not cpass.verify(_PASS, svc.ssm.values["intake_passphrase_hash"])


def test_a_password_changed_elsewhere_works_after_one_reread():
    svc = _settings_intake()
    fresh = _settings_intake()
    fresh.settings = dataclasses.replace(fresh.settings, owner_passphrase_hash=cpass.hash_passphrase(
        "the new owner one", iterations=1000))
    event = {"rawPath": "/api/owner/hello", "requestContext": {"http": {"method": "POST"}},
             "headers": {"x-calls-log-owner": "the new owner one"}, "body": "{}"}
    asked = []
    res = intake.handle(event, lambda: svc, reload=lambda: asked.append(1) or fresh)
    assert res["statusCode"] == 200 and asked == [1]
    res = intake.handle(event, lambda: svc, reload=lambda: None)   # read too recently: refused as before
    assert res["statusCode"] == 403
    # a request that passes never re-reads
    event["headers"] = {"x-calls-log-owner": _OWNER}
    assert intake.handle(event, lambda: svc, reload=lambda: asked.append(2) or fresh)["statusCode"] == 200
    assert asked == [1]


def test_reread_happens_at_most_once_per_period():
    before = (intake._services, intake._loaded_at)
    try:
        intake._services, intake._loaded_at = object(), 100.0
        assert intake._reload(clock=lambda: 105.0) is None and intake._services is not None
    finally:
        intake._services, intake._loaded_at = before


def test_employee_list_is_saved_the_way_the_web_app_keys_it():
    assert osettings.employees(["  Nguyễn  Văn  An ", "Nguyen Van A", "TRẦN THỊ B"]) == [
        "nguyen van an", "nguyen van a", "tran thi b"]
    cases = [
        ([], "employees_empty"), ("nguyen van a", "employees_invalid"), (["a", 3], "employees_invalid"),
        (["nguyen van a", "  "], "employee_blank"), (["x" * 101], "employee_long"),
        (["Trần Thị B", "tran thi b"], "employee_twice"),
        (["nguyen van a", "nguyen van an"], "prefix_clash"),   # the first would also match the second
        ([f"person {i:03}" for i in range(201)], "employees_many"),
        ([f"{i:03} " + "x" * 30 for i in range(150)], "employees_too_long"),   # 150 x 36 bytes: over 4 KB
    ]
    for names, code in cases:
        try:
            osettings.employees(names)
            raise AssertionError(f"accepted {names!r}")
        except osettings.Invalid as e:
            assert e.code == code, (names, code, e.code)
    svc = _settings_intake()
    code, body = _owner(svc, "/api/owner/employees", {"names": ["nguyen van a", "nguyen van an"]})
    assert code == 400 and body["code"] == "prefix_clash" and body["args"] == {
        "first": "nguyen van a", "later": "nguyen van an"}
    code, body = _owner(svc, "/api/owner/employees", {"names": ["Nguyen Van An", "Nguyen Van A", "Le Van C"]})
    assert code == 200 and body["employees"] == ["nguyen van an", "nguyen van a", "le van c"]
    assert json.loads(svc.ssm.values["name_prefixes"]) == body["employees"] and svc.ssm.puts == ["name_prefixes"]


def test_owner_adds_and_removes_alert_addresses():
    svc = _settings_intake([_sub("owner@example.com", 1), _sub("second@example.com", 2), _sub("waiting@example.com")])
    assert _owner(svc, "/api/owner/alerts", {"action": "add", "address": "not an address"})[1]["code"] == "bad_email"
    code, body = _owner(svc, "/api/owner/alerts", {"action": "add", "address": " third@example.com "})
    assert code == 200 and svc.sns.calls == [("subscribe", _TOPIC, "email", "third@example.com")]
    assert svc.ssm.puts == []   # the subscriptions are the record; alert_email stays CloudShell's
    assert {"address": "third@example.com", "confirmed": False, "id": None} in body["alerts"]
    # only a confirmed subscription of this topic, and never the last one
    for sub in (None, "PendingConfirmation", "arn:aws:sns:ap-southeast-2:123456789012:other-topic:00000001"):
        assert _owner(svc, "/api/owner/alerts", {"action": "remove", "id": sub})[1]["code"] == "not_confirmed"
    code, body = _owner(svc, "/api/owner/alerts", {"action": "remove", "id": f"{_TOPIC}:00000002"})
    assert code == 200 and svc.sns.calls[-1] == ("unsubscribe", f"{_TOPIC}:00000002")
    assert _owner(svc, "/api/owner/alerts", {"action": "remove", "id": f"{_TOPIC}:00000001"})[1]["code"] == "last_alert"
    assert _owner(svc, "/api/owner/alerts", {"action": "list"})[1]["code"] == "bad_action_alerts"
    svc.settings = dataclasses.replace(svc.settings, alerts_topic_arn="")
    assert _owner(svc, "/api/owner/alerts", {"action": "add", "address": "x@example.com"})[1]["code"] == "no_topic"


def test_owner_settings_never_reach_a_log_line():
    svc = _settings_intake()
    lines = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    keep = Keep()
    intake.log.addHandler(keep)
    try:
        _owner(svc, "/api/owner/password", {"which": "owner", "new": "a brand new owner pw"})
        _owner(svc, "/api/owner/employees", {"names": ["Pham Thi D", "Vo Van E"]})
        _owner(svc, "/api/owner/alerts", {"action": "add", "address": "secret.person@example.com"})
        _owner(svc, "/api/owner/alerts", {"action": "secret.person@example.com"})
    finally:
        intake.log.removeHandler(keep)
    text = "\n".join(lines)
    assert lines and not any(v in text for v in ("a brand new owner pw", "pham thi d", "Pham", "secret.person"))


# ----- a day's second upload waits for a Re-run (Story 4.6) ---------------------------------

_HELD_OLD = "20260601T080000Z-00000001"
_HELD_NEW = "20260601T090000Z-00000002"


def _two_uploads(old_state="Done"):
    """A store with an older upload in old_state and a newer one Held, the clock at 1_780_000_000."""
    store, now = _store()
    _stored_upload(store, _HELD_OLD, old_state)
    store.create(_RUN_PD, _HELD_NEW, 2)
    assert store.mark_ready(_RUN_PD, _HELD_NEW)["status"] == "Held"
    return store, now


def test_second_upload_of_a_day_is_held_without_ready_time():
    store, _ = _two_uploads()
    item = store.get(_RUN_PD, _HELD_NEW)
    assert item["status"] == "Held" and "readyAt" not in item   # out of the turnaround until a Re-run
    assert store.get(_RUN_PD, _HELD_OLD)["status"] == "Done"     # the earlier upload stays the day's
    try:
        store.begin_run(_RUN_PD, _HELD_NEW, "run-1")              # a stray message can't start it
    except fstatus.TransitionRefused:
        pass
    else:
        raise AssertionError("a Held upload started")
    assert ("pd#" + _RUN_PD, "lease") not in store.client.items


def test_an_unfinished_upload_does_not_hold_the_next_but_an_import_does():
    store, _ = _store()
    store.create(_RUN_PD, _HELD_OLD, 2)                                  # never made Ready
    store.create(_RUN_PD, _HELD_NEW, 2)
    assert store.mark_ready(_RUN_PD, _HELD_NEW)["status"] == "Pending"
    store, _ = _store()
    store.record_import(_RUN_PD, _HELD_OLD)                             # the PC already sent this day
    store.create(_RUN_PD, _HELD_NEW, 2)
    assert store.mark_ready(_RUN_PD, _HELD_NEW)["status"] == "Held"


def test_without_a_rerun_the_earlier_upload_still_runs_and_stays():
    for old in ("Pending", "Failed"):                                    # not yet in the Sheet
        store, _ = _two_uploads(old)
        assert store.begin_run(_RUN_PD, _HELD_OLD, "run-1")["status"] == "Processing"
        store.finish(_RUN_PD, _HELD_OLD)
        store.release_lease(_RUN_PD, "run-1")
        assert store.get(_RUN_PD, _HELD_NEW)["status"] == "Held"


def test_a_rerun_makes_the_later_upload_the_days_choice_and_the_earlier_steps_aside():
    for old in ("Pending", "Failed"):
        store, now = _two_uploads(old)
        now[0] += 60
        store.requeue(_RUN_PD, _HELD_NEW)                                # Re-run: Held -> Pending
        assert store.chosen_later(_RUN_PD, _HELD_OLD) == _HELD_NEW
        try:
            store.begin_run(_RUN_PD, _HELD_OLD, "run-1")
        except fstatus.Superseded:
            pass
        else:
            raise AssertionError(f"an older {old} upload ran after a later one was chosen")
        assert store.get(_RUN_PD, _HELD_OLD)["status"] == "Superseded"
        assert ("pd#" + _RUN_PD, "lease") not in store.client.items
        assert store.begin_run(_RUN_PD, _HELD_NEW, "run-2")["status"] == "Processing"


def test_a_rerun_while_the_earlier_one_runs_waits_then_writes_last():
    store, now = _two_uploads("Pending")
    store.begin_run(_RUN_PD, _HELD_OLD, "run-1")                         # the earlier one is running
    now[0] += 60
    store.requeue(_RUN_PD, _HELD_NEW)
    try:
        store.begin_run(_RUN_PD, _HELD_NEW, "run-2")
    except fstatus.Busy:
        pass
    else:
        raise AssertionError("two Runs of one day at once")
    store.finish(_RUN_PD, _HELD_OLD)
    store.release_lease(_RUN_PD, "run-1")
    assert store.begin_run(_RUN_PD, _HELD_NEW, "run-2")["status"] == "Processing"   # runs after: its sheet wins


def test_re_running_an_older_upload_makes_it_the_choice_again():
    store, now = _two_uploads("Done")
    now[0] += 60
    store.requeue(_RUN_PD, _HELD_NEW)
    store.begin_run(_RUN_PD, _HELD_NEW, "run-1")
    store.finish(_RUN_PD, _HELD_NEW, "boom")                             # Failed, would retry by itself
    store.release_lease(_RUN_PD, "run-1")
    now[0] += 60
    store.requeue(_RUN_PD, _HELD_OLD)                                    # the Owner goes back to the first
    try:
        store.begin_run(_RUN_PD, _HELD_NEW, "run-2")
    except fstatus.Superseded:
        pass
    else:
        raise AssertionError("the retried later upload overwrote the Owner's choice")
    assert store.begin_run(_RUN_PD, _HELD_OLD, "run-3")["status"] == "Processing"


def test_runner_steps_aside_for_a_later_choice_without_a_day_sheet_or_sync():
    rig = _Rig(switch="true")
    rig.status.create(_RUN_PD, "20260601T090000Z-0000000f", 1)
    assert rig.status.mark_ready(_RUN_PD, "20260601T090000Z-0000000f")["status"] == "Held"
    rig.status.clock = lambda: 1_780_000_100.0
    rig.status.requeue(_RUN_PD, "20260601T090000Z-0000000f")
    outcome = rig.run()
    assert outcome.status == "Superseded" and rig.ocr_calls == [] and not rig.sync.called
    assert not any(k.startswith("results/") for k in rig.s3.objects)
    assert rig.item()["status"] == "Superseded" and rig.item()["attempts"] == 0
    assert [e["status"] for e in rig.events()] == ["Superseded"]
    assert ("pd#" + _RUN_PD, "lease") not in rig.status.client.items


def _intake_two_uploads():
    """Two uploads of one day, every image arrived, neither Ready. Their uploadIds carry today's date."""
    svc, _, s3 = _intake()
    svc.today = lambda: _dt.datetime.now(_dt.timezone.utc).date()
    ids = []
    for _ in range(2):
        up = _upload(svc, s3, ["a.jpg"])
        s3.objects[f"inbox/{up['uploadId']}/a.jpg"] = b"x"
        ids.append({"personDay": up["personDay"], "uploadId": up["uploadId"]})
    return svc, s3, ids


def test_intake_ready_holds_a_days_second_upload_and_queues_nothing():
    svc, s3, (first, second) = _intake_two_uploads()
    assert _call(svc, "/api/ready", first)[1]["status"] == "Pending"
    code, body = _call(svc, "/api/ready", second)
    assert code == 200 and body["status"] == "Held"
    code, body = _call(svc, "/api/ready", second)                        # Ready again: still Held, no message
    assert code == 200 and body["status"] == "Held"
    assert [m["body"]["uploadId"] for m in svc.sqs.sent] == [first["uploadId"]]


def test_intake_status_lists_a_held_upload_under_its_days_row():
    svc, s3, (first, second) = _intake_two_uploads()
    _call(svc, "/api/ready", first)
    _call(svc, "/api/ready", second)
    rows = _call(svc, "/api/status")[1]["folders"]
    assert [(r["uploadId"], r["status"], r["uploads"]) for r in rows] == [
        (first["uploadId"], "Pending", 2), (second["uploadId"], "Held", 2)]


def test_intake_rerun_with_the_passphrase_uses_a_held_upload_only():
    svc, s3, (first, second) = _intake_two_uploads()
    _call(svc, "/api/ready", first)
    _call(svc, "/api/ready", second)
    assert _call(svc, "/api/rerun", second, passphrase="wrong guess here")[0] == 401
    assert _call(svc, "/api/rerun", second, passphrase=None)[0] == 401
    svc.status.begin_run(_RUN_PD, first["uploadId"], "run-1")
    svc.status.finish(_RUN_PD, first["uploadId"])
    svc.status.release_lease(_RUN_PD, "run-1")
    code, body = _call(svc, "/api/rerun", first)                         # Done: only the Owner re-runs it
    assert code == 409 and body["code"] == "not_requeueable" and body["args"]["status"] == "Done"
    code, body = _call(svc, "/api/rerun", second)
    assert code == 200 and body["status"] == "Pending" and body["images"] == 1
    assert svc.sqs.sent[-1]["body"] == {"uploadId": second["uploadId"], "personDay": _RUN_PD, "mode": "process"}
    assert svc.status.get(_RUN_PD, second["uploadId"])["status"] == "Pending"
    assert _call(svc, "/api/rerun", {"personDay": _RUN_PD, "uploadId": "../x"})[0] == 400


def test_intake_rerun_refuses_a_held_upload_whose_images_are_gone():
    svc, s3, (first, second) = _intake_two_uploads()
    _call(svc, "/api/ready", first)
    _call(svc, "/api/ready", second)
    del s3.objects[f"inbox/{second['uploadId']}/a.jpg"]      # expired after 30 days
    code, body = _call(svc, "/api/rerun", second)
    assert code == 409 and body["code"] == "images_gone"
    assert svc.status.get(_RUN_PD, second["uploadId"])["status"] == "Held"


def test_owner_rerun_also_takes_a_held_or_superseded_upload():
    for state in ("Held", "Superseded"):
        store, _ = _two_uploads("Pending")
        if state == "Superseded":
            store.requeue(_RUN_PD, _HELD_NEW)
            store.client.items[("pd#" + _RUN_PD, "up#" + _HELD_OLD)]["status"] = "Superseded"
            target = _HELD_OLD
        else:
            target = _HELD_NEW
        sent = []
        crequeue.requeue(naming.parse(_RUN_PD), target, status=store,
                         list_images=lambda uid: ["a.jpg", "b.jpg"], send=sent.append)
        assert store.get(_RUN_PD, target)["status"] == "Pending" and len(sent) == 1


def test_enqueue_holds_a_days_second_folder_and_says_how_to_use_it():
    cli = _FakeCli()
    with tempfile.TemporaryDirectory() as d:
        folder = _enqueue_folder(d, _RUN_PD, ["a.jpg"])
        assert _enqueue([str(folder)], cli)[0] == 0
        sent = cli.log.count("sqs send-message")
        code, said = _enqueue([str(folder)], cli)
    assert code == 0 and cli.log.count("sqs send-message") == sent == 1
    assert "Held" in said and "--requeue" in said and "--upload-id" in said
    assert sorted(i["status"] for i in cli.dynamo.items.values()) == ["Held", "Pending"]


def test_requeue_without_an_upload_takes_the_days_current_one_not_a_held_one():
    for old in ("Done", "Parked"):
        store, _ = _two_uploads(old)                                     # the later upload waits, Held
        sent = []
        uid, _n = crequeue.requeue(naming.parse(_RUN_PD), None, status=store,
                                   list_images=lambda uid: ["a.jpg", "b.jpg"], send=sent.append)
        assert uid == _HELD_OLD and store.get(_RUN_PD, _HELD_NEW)["status"] == "Held"


def test_runner_command_line_counts_a_superseded_run_as_done():
    with mock.patch.object(runner, "_clients", return_value={}), \
            mock.patch.object(runner, "run_folder", return_value=runner.Outcome("Superseded")):
        assert runner.main(["--upload-id", _RUN_UP, "--person-day", _RUN_PD]) == 0


# ---------------------------------------------------------------------------
# The data lake's records (cloud/lake.py, ADR 9, Data System Plan Phase 1):
# per-field confidences in the shared core, the staged envelope and its
# checks, and the runner's best-effort hand-over. All values are fictional.
# ---------------------------------------------------------------------------

from cloud import lake
from ocr import OcrLine, row_line_spans, row_text
from parser import parse_duration_ex, parse_start_time_ex
from tools import check_lake_parity as lake_parity

_LAKE_SALT = "fictional-salt-for-tests"
_LAKE_PREFIXES = ("nguyen van a", "tran thi b")


def _row(*parts):
    return [OcrLine(text, conf, (0.0, 0.0, 1.0, 1.0)) for text, conf in parts]


def _lake_rec(src, *parts):
    """One call row as OCR lines (a fictional phone number first), parsed like extract_calls does."""
    row = _row((_PHONE, 0.9), *parts)
    return parse_row(src, row_text(row), ocr.row_confidence(row), line_spans=row_line_spans(row))


_LAKE_BY_IMAGE = {
    "Shot_01.jpg": [_lake_rec("Shot_01.jpg", ("08:00AM", 0.97), ("発信", 0.95), ("20分0秒", 0.93)),
                    _lake_rec("Shot_01.jpg", ("08:21AM", 0.97), ("発信", 0.95), ("0秒", 0.93)),
                    _lake_rec("Shot_01.jpg", ("08:23AM", 0.97), ("発信", 0.95), ("37分0秒", 0.93))],
    "shot_02.jpg": [_lake_rec("shot_02.jpg", ("09:30AM", 0.96), ("不在着信", 0.95)),
                    _lake_rec("shot_02.jpg", ("10:00AM", 0.97), ("発信", 0.95), ("30分0秒", 0.62))],
    "shot_03.png": [_lake_rec("shot_03.png", ("10:33AM", 0.97), ("発信", 0.95), ("27分0秒", 0.93))],
}


def _lake_payload(**changes):
    recs = [copy.deepcopy(r) for rs in _LAKE_BY_IMAGE.values() for r in rs]
    kept, sessions, _stats = runner.calls_and_sessions(recs)
    upload = {"image_count": 3, "attempts": 1, "ready_at": "2026-06-01T02:00:00Z",
              "started_at": "2026-06-01T02:01:00Z", "finished_at": "2026-06-01T02:03:00Z",
              "status": "Done", "runner_memory_mb": 2048}
    return lake.payload(naming.PersonDay("Nguyen Van A", "2026-06-01"), _RUN_UP, image_names=list(_LAKE_BY_IMAGE),
                        records=kept, sessions=sessions, sheet={"sessions": 2, "span_min": 120},
                        upload=upload, salt=_LAKE_SALT, prefixes=_LAKE_PREFIXES,
                        staged_at="2026-06-01T02:03:01Z", **changes)


def _lake_rig(lake_on="true", **settings_changes):
    rig = _Rig(by_image=_LAKE_BY_IMAGE, lake=lake_on)
    rig.settings = dataclasses.replace(rig.settings, **{"person_salt": _LAKE_SALT, "name_prefixes": _LAKE_PREFIXES,
                                                        **settings_changes})
    return rig


def _staged(rig):
    return {k: json.loads(v) for k, v in rig.s3.objects.items() if k.startswith("staging/")}


def test_row_line_spans_point_at_each_line_in_the_row_text():
    for row in (_row(("03:08 PM", 0.9), ("発信", 0.8), ("1分25秒", 0.7)),
                _row((" 08:00", 0.9), ("x", 0.5)), _row(("only", 0.6))):
        text = row_text(row)
        assert [text[a:b] for a, b, _ in row_line_spans(row)] == [ln.text.strip() for ln in row]
        assert [c for _, _, c in row_line_spans(row)] == [ln.confidence for ln in row]


def test_parse_duration_ex_points_at_the_duration_it_read():
    for text, secs, read in (("03:08PM発信(32秒)", 32, "32秒"), ("8:00 AM 1 min 25 sec outgoing", 85, "1 min 25 sec"),
                             ("03:08 PM 発信 01:25", 85, "01:25"), ("10:15 発信 0秒", 0, "0秒"),
                             ("09:00 発信 00:01:05", 65, "00:01:05"), ("02:23 発信", None, None)):
        start, _w, span = parse_start_time_ex(text)
        got, warnings, at = parse_duration_ex(text, start, span)
        assert (got, at and text[at[0]:at[1]]) == (secs, read), text
        assert parse_duration(text, start, span) == (got, warnings)   # the old function is unchanged
    assert parse_duration_ex("8:00 AM 1 min", "08:00")[2] is None     # the value-only path has no span


def test_parse_row_gives_each_field_the_confidence_of_its_own_line():
    rec = _lake_rec("a.jpg", ("03:08PM", 0.97), ("発信", 0.95), ("1分25秒", 0.62))
    assert (rec.start_time, rec.raw_duration_seconds, rec.start_conf, rec.duration_conf) == ("15:08", 85, 0.97, 0.62)
    plain = parse_row("a.jpg", rec.raw_text, rec.ocr_confidence)
    assert (plain.start_conf, plain.duration_conf) == (None, None)
    assert plain.to_dict() == rec.to_dict()   # to_dict (processing_log.json) leaves them out
    recovered = _lake_rec("a.jpg", ("03:08PM", 0.97), ("発信", 0.95))
    assert recovered.raw_duration_seconds == 0 and recovered.duration_conf is None


def test_person_key_is_salted_one_per_employee_and_names_nobody():
    key = lake.person_key("Nguyen Van A", _LAKE_SALT, _LAKE_PREFIXES)
    assert _re.fullmatch(r"[0-9a-f]{16}", key)
    assert lake.person_key("Nguyễn Văn  A", _LAKE_SALT, _LAKE_PREFIXES) == key        # accents and spaces
    assert lake.person_key("Nguyen Van A sales", _LAKE_SALT, _LAKE_PREFIXES) == key   # the Web App's prefix match
    assert lake.person_key("Tran Thi B", _LAKE_SALT, _LAKE_PREFIXES) != key
    assert lake.person_key("Nguyen Van A", "another salt", _LAKE_PREFIXES) != key
    try:
        lake.person_key("Nguyen Van A", "", _LAKE_PREFIXES)
    except ValueError:
        pass
    else:
        raise AssertionError("an unset salt must never hash a name")


def test_every_warning_the_parser_writes_has_a_lake_code():
    root = Path(__file__).resolve().parent
    text = (root / "parser.py").read_text(encoding="utf-8") + (root / "session_builder.py").read_text(encoding="utf-8")
    written = set(_re.findall(r'(?:append\(\s*|\[\s*)f?"([a-z]+(?:_[a-z0-9]+)+_?)', text))
    written |= {"possible_fuzzy_duplicate_with_"}
    written -= {"overlap_with_"}   # a session's warning, not a call's
    assert len(written) > 15, written
    for code in written:
        assert lake.flag_code(code + "x" if code.endswith("_") else code) != "other", code
    assert lake.flag_code("low_ocr_confidence_0.72") == "low_ocr_confidence"
    assert lake.flag_code("possible_fuzzy_duplicate_with_shot_02.jpg@08:01") == "possible_fuzzy_duplicate"
    assert lake.flag_code(f"something new about {_PHONE}") == "other"


def test_lake_payload_passes_its_checks_and_holds_codes_not_text():
    p = _lake_payload()
    assert lake.check(p) == []
    assert tuple(p) == lake.ENVELOPE_KEYS and p["schema_version"] == 1
    assert p["person_key"] == lake.person_key("Nguyen Van A", _LAKE_SALT, _LAKE_PREFIXES)
    assert [(c["call_seq"], c["image_key"], c["start_ts"][11:16], c["end_ts"] and c["end_ts"][11:16],
             c["duration_s"], c["countable"]) for c in p["calls"]] == [
        (1, "01", "08:00", "08:20", 1200, True), (2, "01", "08:21", "08:21", 0, True),   # 0 s: end == start
        (3, "01", "08:23", "09:00", 2220, True), (4, "02", "09:30", None, None, False),
        (5, "02", "10:00", "10:30", 1800, True), (6, "03", "10:33", "11:00", 1620, True)]
    assert [(s["session_no"], s["start_ts"], s["end_ts"], s["span_min"], s["call_count"], s["source"])
            for s in p["sessions"]] == [
        (1, "2026-06-01T08:00:00", "2026-06-01T09:00:00", 60, 3, "runner"),
        (2, "2026-06-01T10:00:00", "2026-06-01T11:00:00", 60, 2, "runner")]
    missed, weak = p["calls"][3], p["calls"][4]
    assert (missed["flagged"], missed["flag_field"], missed["flag_reason"]) == (True, "duration", "duration_not_found")
    assert (weak["flagged"], weak["flag_field"], weak["flag_reason"], weak["duration_conf"]) == (
        True, "duration", "low_field_confidence", 0.62)
    assert p["calls"][0]["flagged"] is False and p["calls"][0]["flag_reason"] is None
    assert {c["parser_version"] for c in p["calls"]} == {lake.parser_version()}
    text = json.dumps(p, ensure_ascii=False)
    for private in (_PHONE, "Nguyen Van A", "nguyen van a", "Shot_01.jpg", "発信", _LAKE_SALT):
        assert private not in text, private


def test_lake_check_names_each_fault_by_position_only():
    def broken(change):
        p = _lake_payload()
        change(p)
        return "\n".join(lake.check(p))

    faults = [
        (lambda p: p["calls"][0].update(end_ts="2026-06-01T07:59:00"), "calls[0]: end_ts before start_ts"),
        (lambda p: p["calls"][0].update(duration_s=60), "calls[0]: end_ts - start_ts disagrees"),
        (lambda p: p["calls"][0].update(flag_reason=f"call from {_PHONE}"), "calls[0]: flag columns"),
        (lambda p: p["calls"][0].update(name="Nguyen Van A"), "calls[0]: columns"),
        (lambda p: p["calls"][0].update(image_key="Shot_01.jpg"), "calls[0]: image_key"),
        (lambda p: p["sessions"][1].update(start_ts="2026-06-01T08:30:00"), "sessions[1]: overlaps"),
        (lambda p: p["sessions"][0].update(call_count=0), "sessions[0]: no calls"),
        (lambda p: p["sessions"][0].update(span_min=59), "sessions[0]: span_min disagrees"),
        (lambda p: p["sessions"].pop(), "call_count total"),
        (lambda p: p["day_sheet"].update(span_min=119), "Day Sheet"),
        (lambda p: [c.update(row_conf=0.5) for c in p["calls"]], "below the confidence threshold"),
        (lambda p: p["uploads"][0].update(status="Failed"), "not a Done upload"),
        (lambda p: p.update(person_key="Nguyen Van A"), "person_key"),
        (lambda p: p.update(schema_version=2), "schema_version"),
        (lambda p: p.pop("day_sheet"), "envelope keys"),
        (lambda p: p.update(calls=[None]), "malformed envelope"),
    ]
    for change, said in faults:
        found = broken(change)
        assert said in found, (said, found)
        assert _PHONE not in found and "Nguyen Van A" not in found


def test_lake_switch_off_stages_nothing():
    rig = _lake_rig("false")
    assert rig.run().status == "Done"
    assert _staged(rig) == {} and rig.sns.sent == [] and rig.ssm.lake_calls == 1
    assert rig.events()[-1]["lake"] == "off"


def test_lake_a_done_run_stages_its_records_once_after_done():
    rig = _lake_rig()
    states = []
    real_put = rig.s3.put_object

    def put(**kw):
        if kw["Key"].startswith("staging/"):
            states.append(rig.item()["status"])
        return real_put(**kw)

    rig.s3.put_object = put
    with mock.patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "2048"}):
        assert rig.run().status == "Done"
    staged = _staged(rig)
    assert list(staged) == [f"staging/{_RUN_PD}/{_RUN_UP}.json"] and states == ["Done"]
    p = staged[f"staging/{_RUN_PD}/{_RUN_UP}.json"]
    assert lake.check(p) == [] and p["upload_id"] == _RUN_UP and p["work_date"] == "2026-06-01"
    assert p["day_sheet"] == {"sessions": 2, "span_min": 120}   # read back from the stored Day Sheet
    up = p["uploads"][0]
    assert (up["status"], up["image_count"], up["attempts"], up["runner_memory_mb"]) == ("Done", 3, 1, 2048)
    assert up["ready_at"] and up["started_at"] and up["finished_at"]
    text = json.dumps(p, ensure_ascii=False)
    assert _PHONE not in text and "Nguyen Van A" not in text and _LAKE_SALT not in text
    assert rig.events()[-1]["lake"] == "staged" and rig.sns.sent == []


def test_lake_failure_leaves_the_run_done_and_emails_once():
    for case in ("no salt", "put refused", "alert fails too"):
        rig = _lake_rig(**({"person_salt": ""} if case == "no salt" else {}))
        if case != "no salt":
            real_put = rig.s3.put_object

            def put(real_put=real_put, **kw):
                if kw["Key"].startswith("staging/"):
                    raise RuntimeError(f"AccessDenied near {_LAKE_SALT} and {_ST_SECRET}")
                return real_put(**kw)

            rig.s3.put_object = put
        if case == "alert fails too":
            rig.sns.publish = mock.Mock(side_effect=RuntimeError("SNS down"))
        outcome = rig.run(holder=f"run-{case}")
        assert outcome.status == "Done" and rig.item()["status"] == "Done", case
        assert len(rig.s3.versions[f"results/{_RUN_PD}.xlsx"]) == 1 and _staged(rig) == {}
        done = rig.events()[-1]
        assert (done["status"], done["lake"]) == ("Done", "failed") and done["lakeError"], case
        text = "\n".join(rig.logs)
        assert _LAKE_SALT not in text and _ST_SECRET not in text
        if case == "alert fails too":
            assert rig.sns.publish.call_count == 1
        else:
            assert len(rig.sns.sent) == 1 and "data lake" in rig.sns.sent[0]["Subject"]
            assert _LAKE_SALT not in rig.sns.sent[0]["Message"]


def test_runner_reads_checkpoints_written_before_or_after_its_own_version():
    rig = _Rig(by_image=_LAKE_BY_IMAGE)
    for i, (name, recs) in enumerate(_LAKE_BY_IMAGE.items()):
        rows = [dataclasses.asdict(r) for r in recs]
        if i == 0:   # an older checkpoint: no per-field confidences
            rows = [{k: v for k, v in r.items() if k not in ("start_conf", "duration_conf")} for r in rows]
        else:        # a newer one: a field this version doesn't know
            rows = [{**r, "added_later": 1} for r in rows]
        rig.s3.objects[f"work/{_RUN_UP}/{name}.json"] = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    assert rig.run().status == "Done" and rig.ocr_calls == []
    assert rig.written == [[("08:00", "09:00"), ("10:00", "11:00")]]


def test_settings_keep_the_person_salt_private_and_read_the_lake_switch_fresh():
    ssm = _FakeSsm({**_ST_VALUES, "person_salt": _LAKE_SALT}, lake="true")
    current = settings.read(ssm, _ST_ENV)
    assert current.person_salt == _LAKE_SALT and _LAKE_SALT not in repr(current)
    assert settings.read(_FakeSsm({**_ST_VALUES, "person_salt": "unset"}), _ST_ENV).person_salt == ""
    assert settings.lake_enabled(ssm) is True and ssm.lake_calls == 1
    ssm.lake = "false"
    assert settings.lake_enabled(ssm) is False and ssm.lake_calls == 2 and ssm.switch_calls == 0


def test_put_parameters_sets_the_person_salt_once_and_never_shows_it():
    fake = _FakeAws()
    code, said = _pp_main(["--new-person-salt", "--lake-enabled", "false"], fake)
    assert code == 0, said
    assert [i["Name"] for i in fake.inputs] == [
        "/calls-log/person_salt", "/calls-log/person_salt", "/calls-log/lake_enabled"]   # read, then two writes
    salt = fake.inputs[1]["Value"]
    assert _re.fullmatch(r"[0-9a-f]{64}", salt) and salt not in said and fake.inputs[2]["Value"] == "false"
    refused = _FakeAws(current="0" * 64)
    code, said = _pp_main(["--new-person-salt", "--lake-enabled", "true"], refused)
    assert code == 2 and "already set" in said and not any("Value" in i for i in refused.inputs)


def test_contract_doc_lists_exactly_the_lake_columns_and_codes():
    doc = Path(__file__).resolve().parent / "docs" / "aws" / "data-contract.md"
    if not doc.is_file():   # docs/ isn't in the runner image
        return
    text = doc.read_text(encoding="utf-8")
    assert "`schema_version = 1`" in text
    for table, columns, publisher in (("calls", lake.CALL_COLUMNS, ["ingested_at"]),
                                      ("sessions", lake.SESSION_COLUMNS, ["ingested_at"]),
                                      ("uploads", lake.UPLOAD_COLUMNS, []),
                                      ("labels", lake.LABEL_COLUMNS, [])):
        section = text.split(f"### `{table}`")[1].split("\n### ")[0].split("\n## ")[0]
        listed = _re.findall(r"^\| `([a-z_]+)` \| (\w+) \|", section, _re.M)
        assert [c for c, _ in listed] == list(columns) + publisher, (table, listed)
        assert dict(listed) == lake.COLUMN_TYPES[table], (table, listed)   # the types the Glue tables use
    for code in lake.FLAG_REASONS:
        assert f"`{code}`" in text, code


def test_check_lake_parity_compares_staged_calls_with_the_truth():
    rig = _Rig(by_image=_LAKE_BY_IMAGE)
    truth = [{"start": "08:00", "kind": "out", "secs": 1200}, {"start": "08:21", "kind": "out", "secs": 0},
             {"start": "08:23", "kind": "out", "secs": 2220}, {"start": "09:30", "kind": "miss", "secs": 0},
             {"start": "10:00", "kind": "out", "secs": 1800}, {"start": "10:33", "kind": "out", "secs": 1620}]
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(runner.pipeline, "extract_calls", rig.extract_calls), \
            mock.patch.object(excel_writer, "log"):
        folder = Path(d) / _RUN_PD
        folder.mkdir()
        for name in _LAKE_BY_IMAGE:
            (folder / name).write_bytes(b"x")
        assert lake_parity.check_folder(folder, truth) == ([], [])
        problems, _notes = lake_parity.check_folder(folder, truth[1:])
        assert problems == ["calls differ from the truth: 0 missing, 1 extra"]
        (Path(d) / "truth.json").write_text(json.dumps({"sets": {"screenshots": {"dir": _RUN_PD, "calls": truth}}}),
                                            encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            assert lake_parity.main(["--dir", d]) == 0
            assert lake_parity.main(["--dir", str(folder)]) == 2
        assert "screenshots: 6 calls -> OK" in out.getvalue()
        emitted = Path(d) / "emitted"
        runner.run_local(folder, [], None, emit=emitted)
        staged = json.loads((emitted / f"{_RUN_PD}.json").read_text(encoding="utf-8"))
        assert lake.check(staged) == [] and staged["person_key"] != lake.person_key(
            "Nguyen Van A", _LAKE_SALT, _LAKE_PREFIXES)   # the local salt matches no real key


# ---------------------------------------------------------------------------
# The data lake's publisher (cloud/publisher.py, ADR 9 Phase 2). S3 and
# DynamoDB are faked, and Parquet is replaced by a JSON stand-in: pyarrow is
# in the Lambda layer, not here (tools/check_lake_parquet.py covers it in CI).
# ---------------------------------------------------------------------------

from cloud import publisher

_STAGED_KEY = f"staging/{_RUN_PD}/{_RUN_UP}.json"


def _pub_status():
    """Folder Status with _RUN_UP Done, as the runner leaves it before staging."""
    store = FolderStatus(_FakeDynamo(), "calls-log-folders", lambda: 1_780_000_000.0)
    store.create(_RUN_PD, _RUN_UP, 3)
    store.mark_ready(_RUN_PD, _RUN_UP)
    store.begin_run(_RUN_PD, _RUN_UP, "run-1")
    store.finish(_RUN_PD, _RUN_UP)
    store.release_lease(_RUN_PD, "run-1")
    return store


def _publish(status, s3, key=_STAGED_KEY, envelope=None, body=None, minute=3):
    if body is None:
        body = json.dumps(_lake_payload() if envelope is None else envelope, ensure_ascii=False).encode("utf-8")
    s3.objects[key] = body
    written = {}

    def write(table, rows):
        written[table] = rows
        return json.dumps(rows, default=str).encode("utf-8")

    with redirect_stdout(io.StringIO()) as out:
        outcome = publisher.publish(key, s3=s3, bucket="calls-log-data-123456789012", status=status, write=write,
                                    now=lambda: _dt.datetime(2026, 6, 1, 2, minute, 5, 123456, tzinfo=_dt.timezone.utc))
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    return outcome, written, lines


def _lake_objects(s3):
    return sorted(k for k in s3.objects if k.startswith(("lake/", "quarantine/")))


def test_publisher_writes_the_days_choice_to_three_fixed_keys_and_rewrites_them():
    status, s3 = _pub_status(), _FakeS3()
    outcome, written, lines = _publish(status, s3)
    key = lake.person_key("Nguyen Van A", _LAKE_SALT, _LAKE_PREFIXES)
    want = [f"lake/{t}/work_month=2026-06/{key}_2026-06-01.parquet" for t in ("calls", "sessions", "uploads")]
    assert (outcome.status, list(outcome.keys)) == ("published", want) and _lake_objects(s3) == sorted(want)
    assert {t: len(rows) for t, rows in written.items()} == {"calls": 6, "sessions": 2, "uploads": 1}
    assert written["calls"][0]["ingested_at"] == _dt.datetime(2026, 6, 1, 2, 3, 5, 123000)   # UTC, milliseconds
    assert lines == [{**lines[0], "Published": 1, "Dropped": 0, "Quarantined": 0, "outcome": "published",
                      "uploadId": _RUN_UP, "reason": None, "problems": []}]
    metrics = lines[0]["_aws"]["CloudWatchMetrics"][0]
    assert (metrics["Namespace"], metrics["Dimensions"], lines[0]["Function"]) == (
        "calls-log/lake", [["Function"]], "calls-log-publisher")
    text = _infra_text()
    if text is not None:   # the alarm watches exactly this metric
        assert 'dimensions          = { Function = local.names.publisher }' in text
        assert 'publisher  = "calls-log-publisher"' in text
    again, _written, _lines = _publish(status, s3, minute=9)   # a replay or a Re-run: same keys, new versions
    assert again.keys == outcome.keys and all(len(s3.versions[k]) == 2 for k in want)
    for k in want:
        assert "Nguyen" not in k and "nguyen" not in k


def test_publisher_drops_what_is_not_the_days_done_choice():
    later = "20260601T090000Z-0b1c2d3e"
    cases = {}
    fresh = FolderStatus(_FakeDynamo(), "calls-log-folders", lambda: 1_780_000_000.0)
    fresh.create(_RUN_PD, _RUN_UP, 3)
    cases["Uploading"] = fresh
    cases["no item"] = FolderStatus(_FakeDynamo(), "calls-log-folders", lambda: 1_780_000_000.0)
    rerun = _pub_status()
    rerun.create(_RUN_PD, later, 2)
    rerun.mark_ready(_RUN_PD, later)              # Held: it waits for a Re-run, so ours stays the choice
    assert _publish(rerun, _FakeS3())[0].status == "published"
    chosen = _pub_status()
    chosen.create(_RUN_PD, later, 2)
    chosen.mark_ready(_RUN_PD, later)
    chosen.requeue(_RUN_PD, later)                # its Re-run makes it the day's choice
    cases["chosen later"] = chosen
    for name, store in cases.items():
        s3 = _FakeS3()
        outcome, written, lines = _publish(store, s3)
        assert outcome.status == "dropped" and written == {} and _lake_objects(s3) == [], name
        assert lines[0]["Dropped"] == 1, name
    s3 = _FakeS3()
    for key in ("staging/notes.json", f"staging/{_RUN_PD}/not-an-upload.json", f"lake/{_RUN_PD}/{_RUN_UP}.json",
                f"staging/{_RUN_PD}/{_RUN_UP}.json.bak"):
        assert _publish(_pub_status(), s3, key=key)[0].reason == "not a staged envelope's key", key
    assert not [k for k in s3.objects if k.endswith(".parquet") or k.startswith("quarantine/")]
    # The held upload itself is still Held, and never published either.
    assert _publish(rerun, _FakeS3(), key=f"staging/{_RUN_PD}/{later}.json")[0].status == "dropped"


def test_publisher_quarantines_what_fails_the_checks_without_naming_anyone():
    bad = _lake_payload()
    bad["day_sheet"]["span_min"] += 1
    other = _lake_payload()
    other["upload_id"] = other["calls"][0]["upload_id"] = "20260601T090000Z-0b1c2d3e"
    for c in other["calls"] + other["sessions"] + other["uploads"]:
        c["upload_id"] = other["upload_id"]
    for name, kwargs, said in (("fails a check", {"envelope": bad}, "Day Sheet"),
                               ("not JSON", {"body": b"\xff not json"}, "not a JSON object"),
                               ("another upload", {"envelope": other}, "differs from the staged key")):
        status, s3 = _pub_status(), _FakeS3()
        outcome, written, lines = _publish(status, s3, **kwargs)
        assert outcome.status == "quarantined" and written == {}, name
        assert _lake_objects(s3) == [f"quarantine/{_RUN_PD}/{_RUN_UP}.json"], name
        held = json.loads(s3.objects[f"quarantine/{_RUN_PD}/{_RUN_UP}.json"])
        assert any(said in p for p in held["problems"]), (name, held["problems"])
        assert lines[0]["Quarantined"] == 1 and any(said in p for p in lines[0]["problems"]), name
        text = json.dumps(lines)
        assert "Nguyen" not in text and _PHONE not in text, name


def test_publisher_tables_type_every_contract_column():
    rows = publisher.tables(_lake_payload(), _dt.datetime(2026, 6, 1, 2, 3, 5))
    assert {t: list(r[0]) for t, r in rows.items()} == {t: list(lake.COLUMN_TYPES[t]) for t in lake.ENVELOPE_TABLES}
    call, missed, session, upload = rows["calls"][0], rows["calls"][3], rows["sessions"][0], rows["uploads"][0]
    assert call["work_date"] == _dt.date(2026, 6, 1) and call["start_ts"] == _dt.datetime(2026, 6, 1, 8, 0)
    assert call["countable"] is True and isinstance(call["row_conf"], float) and call["duration_s"] == 1200
    assert (missed["end_ts"], missed["duration_s"], missed["duration_conf"]) == (None, None, None)
    assert session["end_ts"] == _dt.datetime(2026, 6, 1, 9, 0) and session["span_min"] == 60
    assert upload["ready_at"] == _dt.datetime(2026, 6, 1, 2, 0) and upload["ready_at"].tzinfo is None   # UTC, naive
    assert upload["runner_memory_mb"] == 2048 and "ingested_at" not in upload


def test_publisher_handler_decodes_s3_keys_and_ignores_other_buckets():
    status, s3 = _pub_status(), _FakeS3()
    s3.objects[_STAGED_KEY] = json.dumps(_lake_payload(), ensure_ascii=False).encode("utf-8")
    encoded = "staging/Nguyen+Van+A+%26+2026-06-01/" + _RUN_UP + ".json"
    records = [{"s3": {"bucket": {"name": "calls-log-data-123456789012"}, "object": {"key": encoded}}},
               {"s3": {"bucket": {"name": "someone-else"}, "object": {"key": encoded}}}]
    with mock.patch.dict(publisher._aws, {"bucket": "calls-log-data-123456789012", "s3": s3, "status": status}), \
            mock.patch.object(publisher, "parquet_bytes", lambda table, rows: b"parquet"), \
            redirect_stdout(io.StringIO()):
        out = publisher.handler({"Records": records}, None)
    assert out == {"outcomes": ["published"]} and len(_lake_objects(s3)) == 3


def test_publisher_zip_holds_everything_it_imports():
    infra = Path(__file__).resolve().parent / "infra" / "lake.tf"
    if not infra.is_file():   # infra/ isn't in the runner image
        return
    files = _re.findall(r'"([\w/]+\.py)"', infra.read_text(encoding="utf-8").split("publisher_files = [")[1].split("]")[0])
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as d:
        for f in files:
            (Path(d) / f).parent.mkdir(parents=True, exist_ok=True)
            (Path(d) / f).write_bytes((root / f).read_bytes())
        done = _subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {d!r}); "
                               "import cloud.publisher, cloud.lake; print(cloud.publisher.__file__)"],
                              capture_output=True, text=True, cwd=d)
    assert done.returncode == 0 and done.stdout.startswith(d), done.stderr[-2000:]


def test_glue_tables_in_infra_match_the_lake_columns():
    text = _infra_text()
    if text is None:
        return
    block = text.split("lake_columns = {")[1].split("\n  }")[0]
    tables = {t: _re.findall(r'\["(\w+)", "(\w+)"\]', body)
              for t, body in _re.findall(r"(\w+) = \[(.*?)\n    \]", block, _re.S)}
    assert tables == {t: list(c.items()) for t, c in lake.COLUMN_TYPES.items()}, tables



# ---------------------------------------------------------------------------
# The data lake's history and views (ADR 9 Phases 4 and 5): the backfill
# from stored Day Sheets, the Athena views tool and the SQL files. AWS is a
# fake CLI; Parquet is a JSON stand-in (tools/check_lake_views.py runs the
# real SQL on DuckDB in CI). All values are fictional.
# ---------------------------------------------------------------------------

from tools import backfill_lake, lake_views

_BF_SALT = "fictional-backfill-salt"
_BF_TEMPLATE = Path(__file__).resolve().parent / "sprs.xlsx"


def _day_sheet_bytes(pairs):
    sessions = [Session(i, a, b, (int(b[:2]) * 60 + int(b[3:])) - (int(a[:2]) * 60 + int(a[3:])), 1)
                for i, (a, b) in enumerate(pairs, 1)]
    with tempfile.TemporaryDirectory() as d, mock.patch.object(excel_writer, "log"):
        path = Path(d) / "day.xlsx"
        excel_writer.write_sessions(_BF_TEMPLATE, path, sessions)
        return path.read_bytes()


class _FakeBackfillCli:
    """subprocess.run under the AWS CLI: SSM, S3 objects and Folder Status in memory."""

    def __init__(self, status_dynamo, objects, salt=_BF_SALT):
        self.dynamo, self.objects, self.salt = status_dynamo, dict(objects), salt
        self.puts, self.log = {}, []

    def __call__(self, cmd, capture_output, text, encoding, errors):
        payload = None
        if "--cli-input-json" in cmd:
            payload = json.loads(Path(cmd[cmd.index("--cli-input-json") + 1].removeprefix("file://")).read_bytes())
        op = f"{cmd[1]} {cmd[2]}"
        self.log.append(op)
        if op == "sts get-caller-identity":
            return _ok('{"Account": "123456789012"}')
        if op == "ssm get-parameters":
            return _ok(json.dumps({"Parameters": [
                {"Name": "/calls-log/person_salt", "Value": self.salt},
                {"Name": "/calls-log/name_prefixes", "Value": json.dumps(list(_LAKE_PREFIXES))}]}))
        if cmd[1] == "dynamodb":
            return _ok(json.dumps(getattr(self.dynamo, cmd[2].replace("-", "_"))(**payload), ensure_ascii=False))
        assert payload is None or payload.get("Bucket") == "calls-log-data-123456789012", payload
        if op == "s3api list-objects-v2":
            return _ok(json.dumps({"Contents": [{"Key": k} for k in sorted(self.objects)
                                                if k.startswith(payload["Prefix"])]}, ensure_ascii=False))
        if op == "s3api get-object":
            Path(cmd[5]).write_bytes(self.objects[payload["Key"]])
            return _ok("{}")
        if op == "s3api head-object":
            if payload["Key"] in self.objects or payload["Key"] in self.puts:
                return _ok("{}")
            return SimpleNamespace(returncode=254, stdout="", stderr=(
                "An error occurred (404) when calling the HeadObject operation: Not Found"))
        if op == "s3api put-object":
            self.puts[payload["Key"]] = json.loads(Path(cmd[cmd.index("--body") + 1]).read_bytes())
            return _ok("{}")
        raise AssertionError(f"unexpected AWS call {cmd}")


def _bf_world():
    dyn = _FakeDynamo()
    store = FolderStatus(dyn, "calls-log-folders", lambda: 1_780_000_000.0)
    a, b, c, d = ("Nguyen Van A & 2026-06-01", "Tran Thi B & 2026-06-02",
                  "Nguyen Van A & 2026-06-03", "Tran Thi B & 2026-06-04")
    store.record_import(a, "20260520T000000Z-00000001")          # the PC's copy, older
    store.create(a, _RUN_UP, 3)                                  # then an upload: Held, the day has one
    store.mark_ready(a, _RUN_UP)
    store.requeue(a, _RUN_UP)                                    # its Re-run makes it the day's choice
    store.begin_run(a, _RUN_UP, "h")
    store.finish(a, _RUN_UP)
    store.release_lease(a, "h")
    store.record_import(b, "20260520T000000Z-00000002")          # a PC day only
    store.create(c, "20260603T080000Z-0c0c0c0c", 2)              # never Done
    store.mark_ready(c, "20260603T080000Z-0c0c0c0c")
    store.record_import(d, "20260520T000000Z-00000004")          # already in the lake
    key_d = lake.person_key("Tran Thi B", _BF_SALT, _LAKE_PREFIXES)
    objects = {f"results/{a}.xlsx": _day_sheet_bytes([("08:00", "09:00"), ("10:00", "11:30")]),
               f"results/{b}.xlsx": _day_sheet_bytes([("09:00", "12:00")]),
               f"results/{c}.xlsx": _day_sheet_bytes([("08:00", "08:30")]),
               f"results/{d}.xlsx": _day_sheet_bytes([("08:00", "08:30")]),
               "results/notes.txt": b"not a Day Sheet",
               f"lake/sessions/work_month=2026-06/{key_d}_2026-06-04.parquet": b"published"}
    return dyn, objects


def _bf_run(argv, cli):
    write = lambda table, rows: json.dumps(rows, default=str).encode("utf-8")
    with redirect_stdout(io.StringIO()) as out:
        code = backfill_lake.main(argv, run=cli, write=write)
    return code, out.getvalue()


def test_lake_check_sessions_allows_backfill_rows_and_catches_faults():
    rows = lake.backfill_rows(naming.PersonDay("Nguyen Van A", "2026-06-01"), {"uploadId": _RUN_UP, "imported": "true"},
                              [("08:00", "09:00"), ("10:00", "11:30")], salt=_BF_SALT)
    assert rows["uploads"] == [] and lake.check_sessions(rows["sessions"]) == []
    assert {(s["source"], s["call_count"]) for s in rows["sessions"]} == {("xlsx_backfill", None)}
    assert [s["span_min"] for s in rows["sessions"]] == [60, 90]
    overlap = copy.deepcopy(rows["sessions"])
    overlap[1]["start_ts"] = "2026-06-01T08:30:00"
    assert any("overlaps" in p for p in lake.check_sessions(overlap))
    assert any("span_min" in p for p in lake.check_sessions([{**rows["sessions"][0], "span_min": 59}]))
    p = _lake_payload()
    p["sessions"][0]["call_count"] = None   # a runner envelope must still count its calls
    assert any("no calls" in x for x in lake.check(p))


def test_backfill_writes_history_once_without_overwriting_or_naming_anyone():
    dyn, objects = _bf_world()
    cli = _FakeBackfillCli(dyn, objects)
    code, said = _bf_run([], cli)
    key_a = lake.person_key("Nguyen Van A", _BF_SALT, _LAKE_PREFIXES)
    key_b = lake.person_key("Tran Thi B", _BF_SALT, _LAKE_PREFIXES)
    assert code == 0, said
    assert sorted(cli.puts) == sorted([
        f"lake/sessions/work_month=2026-06/{key_a}_2026-06-01.parquet",
        f"lake/uploads/work_month=2026-06/{key_a}_2026-06-01.parquet",   # a Run: its upload row too
        f"lake/sessions/work_month=2026-06/{key_b}_2026-06-02.parquet"])  # a PC import: sessions only
    a_sessions = cli.puts[f"lake/sessions/work_month=2026-06/{key_a}_2026-06-01.parquet"]
    assert [(s["upload_id"], s["source"], s["call_count"], s["span_min"]) for s in a_sessions] == [
        (_RUN_UP, "xlsx_backfill", None, 60), (_RUN_UP, "xlsx_backfill", None, 90)]   # the Run, not the import
    assert list(a_sessions[0]) == list(lake.COLUMN_TYPES["sessions"])
    assert json.loads(said.strip().splitlines()[-1]) == {
        "written": 2, "in_lake": 1, "no_upload": 1, "failed_check": 0, "not_a_day_sheet": 1}
    for private in ("Nguyen", "Tran", _BF_SALT):
        assert private not in said and not any(private in k for k in cli.puts), private
    assert not any(k.startswith("results/") for k in cli.puts)   # the Day Sheets are only read
    again = _FakeBackfillCli(dyn, {**objects, **{k: b"x" for k in cli.puts}})
    assert _bf_run([], again)[0] == 0 and again.puts == {}       # a second run writes nothing
    dry = _FakeBackfillCli(dyn, objects)
    code, said = _bf_run(["--dry-run"], dry)
    assert code == 0 and dry.puts == {} and "would write" in said


def test_backfill_needs_the_salt_and_reports_aws_failures():
    dyn, objects = _bf_world()
    try:
        _bf_run([], _FakeBackfillCli(dyn, objects, salt="unset"))
    except SystemExit as e:
        assert "person_salt is unset" in str(e)
    else:
        raise AssertionError("no salt must stop the backfill")
    broken = _FakeBackfillCli(dyn, objects)
    real = broken.__call__

    def refuse_lists(cmd, **kw):
        if cmd[1:3] == ["s3api", "list-objects-v2"]:
            return SimpleNamespace(returncode=254, stdout="", stderr="An error occurred (AccessDenied) when calling")
        return real(cmd, **kw)

    with redirect_stderr(io.StringIO()):
        assert backfill_lake.main([], run=refuse_lists, write=lambda t, r: b"") == 1


def test_lake_views_creates_each_view_in_order_and_reads_it():
    if not lake_views.VIEWS_DIR.is_dir():   # sql/ isn't in the runner image
        return
    sent, states = [], {}

    def run(cmd, capture_output, text, encoding, errors):
        payload = json.loads(Path(cmd[cmd.index("--cli-input-json") + 1].removeprefix("file://")).read_bytes())
        if cmd[1:3] == ["athena", "start-query-execution"]:
            assert (payload["WorkGroup"], payload["QueryExecutionContext"]["Database"]) == ("calls-log", "calls_log")
            sent.append(payload["QueryString"])
            qid = f"q{len(sent)}"
            states[qid] = ("FAILED", "line 1: Column 'x' cannot be resolved") if "ocr_quality LIMIT" in sent[-1] \
                else ("SUCCEEDED", "")
            return _ok(json.dumps({"QueryExecutionId": qid}))
        if cmd[1:3] == ["athena", "get-query-execution"]:
            state, why = states[payload["QueryExecutionId"]]
            return _ok(json.dumps({"QueryExecution": {"Status": {"State": state, "StateChangeReason": why}}}))
        raise AssertionError(cmd)

    with redirect_stdout(io.StringIO()) as out:
        code = lake_views.main([], run=run, sleep=lambda s: None)
    creates = [q for q in sent if q.startswith("CREATE")]
    assert [q.split()[4] for q in creates] == [f"calls_log.{v}" for v in lake_views.VIEWS]
    assert all(not q.lstrip().startswith("--") and not q.endswith(";") for q in sent)
    assert code == 1 and "calls_log.ocr_quality: FAILED: line 1" in out.getvalue()
    assert "calls_log.day_summary: created, reads OK" in out.getvalue()
    with redirect_stdout(io.StringIO()) as listed:
        assert lake_views.main(["--list"], run=lambda *a, **k: (_ for _ in ()).throw(AssertionError())) == 0
    assert "calls_log.weekly_attendance" in listed.getvalue()


def test_lake_sql_creates_only_views_and_reads_only_the_lake():
    root = Path(__file__).resolve().parent / "sql"
    if not root.is_dir():   # sql/ isn't in the runner image
        return
    views = sorted(p.stem for p in (root / "views").glob("*.sql"))
    assert views == sorted(lake_views.VIEWS)
    known = set(lake.COLUMN_TYPES) | set(lake_views.VIEWS)
    forbidden = _re.compile(r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|MERGE|MSCK|UNLOAD|GRANT)\b", _re.I)
    for path in sorted(root.glob("*/*.sql")):
        sql = lake_views.statement(path)
        assert not forbidden.search(sql), path
        assert set(_re.findall(r"calls_log\.(\w+)", sql)) <= known, path
        if path.parent.name == "views":
            assert sql.startswith(f"CREATE OR REPLACE VIEW calls_log.{path.stem} AS\n"), path
            assert sql.count("CREATE") == 1, path
        else:
            assert sql.upper().startswith("SELECT"), path


# ---------------------------------------------------------------------------
# Checking the lake's choice (Phase 3) and hand labels for OCR accuracy
# (Phase 4.5): tools/check_lake_choice.py and tools/label_calls.py. AWS is
# the fake CLI above; Parquet is a JSON stand-in. All values are fictional.
# ---------------------------------------------------------------------------

import csv
import zipfile

from tools import check_lake_choice, label_calls


def _json_rows(path):
    return json.loads(Path(path).read_bytes())


def test_status_chosen_is_the_upload_made_ready_last():
    done_old = {"uploadId": "20260601T010000Z-00000001", "status": "Done", "readyAt": "2026-06-01T01:00:00Z"}
    imported = {"uploadId": "20260520T000000Z-00000002", "status": "Done"}
    held = {"uploadId": "20260601T090000Z-00000003", "status": "Held", "readyAt": "2026-06-01T09:00:00Z"}
    rerun = {"uploadId": "20260601T020000Z-00000004", "status": "Pending", "readyAt": "2026-06-01T05:00:00Z"}
    assert fstatus.chosen([imported, done_old, held]) is done_old       # Held isn't chosen; an import ranks last
    assert fstatus.chosen([imported, done_old, held, rerun]) is rerun  # a Re-run takes over before it is Done
    assert fstatus.chosen([held]) is None and fstatus.chosen([]) is None


class _ScanStatus(_FakeDynamo):
    def scan(self, TableName, ConsistentRead, FilterExpression, ExpressionAttributeValues):
        assert FilterExpression == "begins_with(sk, :up)" and ConsistentRead
        up = ExpressionAttributeValues[":up"]["S"]
        return {"Items": [{k: ({"N": str(v)} if isinstance(v, int) else {"S": v}) for k, v in item.items()}
                          for (_pk, sk), item in sorted(self.items.items()) if sk.startswith(up)]}


class _AthenaCli(_FakeBackfillCli):
    """The backfill's fake CLI, plus Athena answering the lake's (person_key, work_date, upload_id) rows."""

    def __init__(self, status_dynamo, objects, lake_rows, state="SUCCEEDED"):
        super().__init__(status_dynamo, objects)
        self.lake_rows, self.state, self.sql = lake_rows, state, []

    def __call__(self, cmd, capture_output, text, encoding, errors):
        if cmd[1] != "athena":
            return super().__call__(cmd, capture_output, text, encoding, errors)
        self.log.append(f"athena {cmd[2]}")
        payload = json.loads(Path(cmd[cmd.index("--cli-input-json") + 1].removeprefix("file://")).read_bytes())
        if cmd[2] == "start-query-execution":
            assert payload["WorkGroup"] == "calls-log"
            self.sql.append(payload["QueryString"])
            return _ok('{"QueryExecutionId": "q1"}')
        if cmd[2] == "get-query-execution":
            return _ok(json.dumps({"QueryExecution": {"Status": {"State": self.state, "StateChangeReason": "no"}}}))
        if cmd[2] == "get-query-results":
            header = {"Data": [{"VarCharValue": c} for c in ("person_key", "work_date", "upload_id")]}
            return _ok(json.dumps({"ResultSet": {"Rows": [header] + [
                {"Data": [{"VarCharValue": v} for v in row]} for row in self.lake_rows]}}))
        raise AssertionError(cmd)


def test_check_lake_choice_reports_each_day_by_key_never_by_name():
    dyn, _objects = _bf_world()
    status = _ScanStatus()
    status.items = dyn.items
    store = FolderStatus(status, "calls-log-folders", lambda: 1_780_000_000.0)
    store.record_import("Tran Thi B & 2026-06-05", "20260520T000000Z-00000005")   # Done, never reached the lake
    key_a = lake.person_key("Nguyen Van A", _BF_SALT, _LAKE_PREFIXES)
    key_b = lake.person_key("Tran Thi B", _BF_SALT, _LAKE_PREFIXES)
    lake_rows = [
        [key_a, "2026-06-01", "20260520T000000Z-00000001"],   # the PC's copy, though the Run's Re-run is Done
        [key_b, "2026-06-02", "20260520T000000Z-00000002"],   # the day's only (imported) upload
        ["0123456789abcdef", "2026-06-09", _RUN_UP]]           # no such Person-Day
    objects = {"quarantine/Tran Thi B & 2026-06-04/20260520T000000Z-00000004.json": b"{}"}
    cli = _AthenaCli(status, objects, lake_rows)
    with redirect_stdout(io.StringIO()) as out:
        code = check_lake_choice.main([], run=cli, sleep=lambda s: None)
    said = out.getvalue()
    assert code == 1, said
    assert json.loads(said.strip().splitlines()[-2]) == {
        "ok": 1, "waiting": 1, "stale": 1, "missing": 1, "quarantined": 1, "orphan": 1}
    assert f"stale {key_a}_2026-06-01: the lake holds 20260520T000000Z-00000001 (Done); " \
           f"the day's choice is {_RUN_UP} (Done)" in said
    assert f"quarantined {key_b}_2026-06-04" in said and f"missing {key_b}_2026-06-05" in said
    assert "orphan 0123456789abcdef_2026-06-09" in said
    for private in ("Nguyen", "Tran", _BF_SALT):
        assert private not in said, private
    assert cli.sql == [check_lake_choice.LAKE_DAYS] and cli.puts == {}
    assert not any(op.endswith(("put-object", "put-item", "update-item", "get-object")) for op in cli.log)
    assert sum(op.startswith("dynamodb") for op in cli.log) == 1   # one scan, whatever the number of days
    fixed = _AthenaCli(status, {}, [[key_a, "2026-06-01", _RUN_UP], [key_b, "2026-06-02", "20260520T000000Z-00000002"]])
    for day in ("Tran Thi B & 2026-06-04", "Tran Thi B & 2026-06-05"):
        for k in [k for k in status.items if k[0] == f"pd#{day}"]:
            del status.items[k]
    with redirect_stdout(io.StringIO()) as out:
        assert check_lake_choice.main([], run=fixed, sleep=lambda s: None) == 0, out.getvalue()
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
        assert check_lake_choice.main([], run=_AthenaCli(status, {}, [], state="FAILED"), sleep=lambda s: None) == 1
    assert "FAILED" in err.getvalue()


def test_label_values_read_what_a_person_types():
    for text, want in (("8:05", "08:05"), ("08:05", "08:05"), ("8:05 PM", "20:05"), ("12:10am", "00:10"),
                       ("23:59", "23:59"), ("8:05:00", "08:05")):
        assert label_calls.clock(text) == want, text
    for text in ("25:00", "8.05", "13:00 PM", "", "8:60"):
        try:
            label_calls.clock(text)
        except label_calls.LabelError:
            continue
        raise AssertionError(text)
    for text, want in (("", 0), ("83", 83), ("1:23", 83), ("1:02:03", 3723), ("1m 23s", 83), ("2h", 7200),
                       ("45s", 45), ("20m 00s", 1200)):
        assert label_calls.seconds(text) == want, text
    for text in ("1.5", "abc", "1:2"):
        try:
            label_calls.seconds(text)
        except label_calls.LabelError:
            continue
        raise AssertionError(text)
    assert label_calls.shown(83) == "1m 23s" and label_calls.shown(None) == "none"
    assert label_calls.seconds(label_calls.shown(1200)) == 1200


_SHOTS = {"A.png": b"image A", "b.png": b"image b", "c.png": b"image c"}   # the PC's order, case folded
_BATCH = "labels-20260603T010203Z"
_NOW = _dt.datetime(2026, 6, 3, 1, 2, 3, tzinfo=_dt.timezone.utc)


def _label_world():
    calls = _lake_payload()["calls"]
    key = calls[0]["person_key"]
    objects = {f"lake/calls/work_month=2026-06/{key}_2026-06-01.parquet": json.dumps(calls).encode("utf-8"),
               f"inbox/{_RUN_UP}/notes.txt": b"",
               **{f"inbox/{_RUN_UP}/{name}": body for name, body in _SHOTS.items()}}
    return calls, _FakeBackfillCli(_FakeDynamo(), objects)


def _sample(cli, folder, argv=("--count", "3", "--seed", "1"), now=_NOW):
    with redirect_stdout(io.StringIO()) as out:
        code = label_calls.main(["sample", *argv], run=cli, read=_json_rows, now=now, out_dir=folder)
    return code, out.getvalue()


def test_label_calls_sample_zips_a_worksheet_with_its_screenshots():
    calls, cli = _label_world()
    with tempfile.TemporaryDirectory() as d:
        code, said = _sample(cli, Path(d))
        with zipfile.ZipFile(Path(d) / f"{_BATCH}.zip") as z:
            names = sorted(z.namelist())
            text = z.read(f"{_BATCH}/{_BATCH}.csv").decode("utf-8-sig")
            shots = {n.rsplit("/", 1)[1]: z.read(n) for n in names if not n.endswith(".csv")}
        side = json.loads((Path(d) / f"{_BATCH}.json").read_text(encoding="utf-8"))
    assert code == 0 and f"download: {Path(d) / _BATCH}.zip" in said, said
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 3 and tuple(rows[0]) == label_calls.WORKSHEET
    by_ref = {label_calls.ref(c): c for c in calls}
    for row in rows:
        call = by_ref[row["ref"]]
        assert shots[row["image"]] == list(_SHOTS.values())[int(call["image_key"]) - 1], row
        assert row["ocr_start"] == call["start_ts"][11:16] and row["true_start"] == ""
        assert side["calls"][row["ref"]]["ocr_duration_s"] == call["duration_s"]
    assert all(_re.fullmatch(rf"{_BATCH}/(\d{{2}}\.png|{_BATCH}\.csv)", n) for n in names), names   # no file names
    assert len(shots) == len({r["image"] for r in rows})                                            # each image once
    assert side["batch"] == _BATCH and set(side["calls"]) == {r["ref"] for r in rows}
    assert any(by_ref[r["ref"]]["flagged"] for r in rows)   # the doubtful half
    for private in ("Nguyen", "Tran", _PHONE):
        assert private not in text and private not in json.dumps(side), private
    assert not any(op.endswith("put-object") for op in cli.log)
    fetched = cli.log.count("s3api get-object")
    with tempfile.TemporaryDirectory() as d:
        later = _NOW + _dt.timedelta(days=30)
        assert _sample(cli, Path(d), argv=(), now=later)[0] == 0
        assert cli.log.count("s3api get-object") == fetched   # a day out of range is never downloaded
        with zipfile.ZipFile(Path(d) / later.strftime("labels-%Y%m%dT%H%M%SZ.zip")) as z:
            assert z.namelist() == [later.strftime("labels-%Y%m%dT%H%M%SZ/labels-%Y%m%dT%H%M%SZ.csv")]   # too old


def test_label_calls_import_writes_checked_labels_and_nothing_on_a_mistake():
    calls, cli = _label_world()
    write = lambda table, rows: json.dumps(rows, default=str).encode("utf-8")
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "calls-log-labels"
        _sample(cli, folder, argv=("--count", "4", "--seed", "2"))
        rows = list(csv.DictReader(io.StringIO((folder / _BATCH / f"{_BATCH}.csv").read_text(encoding="utf-8-sig"))))
        sheet = Path(d) / f"{_BATCH} (1).csv"   # downloaded, filled in and uploaded back: a browser renamed it

        def fill(values, delimiter=","):
            with open(sheet, "w", encoding="utf-8-sig", newline="") as f:   # as a spreadsheet saves it
                w = csv.DictWriter(f, fieldnames=label_calls.WORKSHEET, delimiter=delimiter)
                w.writeheader()
                w.writerows({**r, **v} for r, v in zip(rows, values))

        def run(argv):
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                code = label_calls.main(argv, run=cli, write=write, now=_NOW, out_dir=folder)
            return code, out.getvalue() + err.getvalue()

        fill([{"true_start": "8:00", "true_duration": "20m 00s"}, {"not_a_call": "x"},
              {"true_start": "25:00"}, {}])
        code, said = run(["import", str(sheet)])
        assert code == 2 and "line 4: '25:00' is not a time of day" in said and cli.puts == {}, said
        fill([{"true_start": "8:00", "true_duration": "20m 00s"}, {"not_a_call": "x"},
              {"true_start": "9:31 AM", "true_duration": ""}, {}], delimiter=";")   # Excel, comma-decimal locale
        code, said = run(["import", str(sheet), "--dry-run"])
        assert code == 0 and cli.puts == {} and "would write" in said, said
        fill([{"true_start": "8:00", "true_duration": "20m 00s"}, {"not_a_call": "x"},
              {"true_start": "9:31 AM", "true_duration": ""}, {}])
        code, said = run(["import", str(sheet), "--dry-run"])
        assert code == 0 and cli.puts == {} and "would write" in said, said
        assert (folder / f"{_BATCH}.zip").is_file()
        code, said = run(["import", str(sheet)])
        assert code == 0, said
        assert not (folder / f"{_BATCH}.zip").exists() and not (folder / _BATCH).exists()   # screenshots gone
        assert (folder / f"{_BATCH}.json").is_file()
        assert run(["import", str(Path(d) / "nowhere.csv")])[0] == 2
    assert json.loads(said.strip().splitlines()[-1]) == {"labelled": 3, "not_a_call": 1, "unlabelled": 1}
    [(key, labels)] = cli.puts.items()
    assert key == f"lake/labels/work_month=2026-06/{_BATCH}.parquet"
    assert [list(r) for r in labels] == [list(lake.LABEL_COLUMNS)] * 3
    first, second, third = labels
    assert (first["real_call"], first["true_start_ts"], first["true_duration_s"]) == (True, "2026-06-01 08:00:00", 1200)
    assert (second["real_call"], second["true_start_ts"], second["true_duration_s"]) == (False, None, None)
    assert third["true_start_ts"] == "2026-06-01 09:31:00" and third["true_duration_s"] == 0
    assert all(r["batch"] == _BATCH and r["labelled_at"] == "2026-06-03 01:02:03" for r in labels)
    for private in ("Nguyen", "Tran", _PHONE):
        assert private not in json.dumps(labels), private
