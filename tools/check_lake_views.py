"""
Do the data lake's views and example queries compute what they should? (ADR 9, Phase 5)

Athena isn't reachable from CI, so this runs the same SQL on DuckDB over the
publisher's real Parquet: the made-up day of tools/check_lake_parquet.py as
the runner publishes it, plus a second person's day as tools/backfill_lake.py
writes it (sessions only, call_count null). Trino's approx_percentile is the
one function DuckDB lacks, so a macro stands in. Every sql/views file is
created in order, every sql/examples file is run, and the views' numbers are
compared with the envelopes they came from. Athena itself checks the SQL again
when the Owner runs tools/lake_views.py.

Needs pyarrow and duckdb (CI pins both); writes only to a temporary folder.
Exit code 0 = every view matches, 1 = a difference.
"""

from __future__ import annotations

import datetime as _dt
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # run as a script from tools/: the pipeline lives one level up

import duckdb  # noqa: E402

from cloud import lake, naming, publisher  # noqa: E402
from tools.check_lake_parquet import made_up_day, made_up_labels  # noqa: E402
from tools.lake_views import VIEWS, VIEWS_DIR, statement  # noqa: E402

EXAMPLES_DIR = ROOT / "sql" / "examples"
INGESTED = _dt.datetime(2026, 6, 2, 9, 0, 0)
BACKFILLED = naming.PersonDay("Tran Thi B", "2026-06-02")
BACKFILLED_UPLOAD = {"uploadId": "20260602T010000Z-1a2b3c4d", "status": "Done", "imageCount": 3, "attempts": 2,
                     "readyAt": "2026-06-02T01:00:00Z", "lastRunAt": "2026-06-02T01:05:00Z",
                     "updatedAt": "2026-06-02T01:07:00Z"}
BACKFILLED_PAIRS = [("08:00", "09:30"), ("13:00", "14:00")]


def write_lake(folder: Path) -> tuple:
    runner_day = made_up_day()
    backfilled = lake.backfill_rows(BACKFILLED, BACKFILLED_UPLOAD, BACKFILLED_PAIRS, salt=lake.LOCAL_SALT)
    sources = [{**publisher.tables(runner_day, INGESTED), "labels": made_up_labels(runner_day, INGESTED)},
               {t: publisher.typed_rows(t, rows, INGESTED) for t, rows in backfilled.items()}]
    for n, tables in enumerate(sources):
        for table, rows in tables.items():
            if rows:
                (folder / table).mkdir(exist_ok=True)
                (folder / table / f"{n}.parquet").write_bytes(publisher.parquet_bytes(table, rows))
    return runner_day, backfilled


def main() -> int:
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        runner_day, backfilled = write_lake(folder)
        con = duckdb.connect()
        con.execute("CREATE SCHEMA calls_log")
        con.execute("CREATE MACRO approx_percentile(x, p) AS quantile_cont(x, p)")
        for table in lake.COLUMN_TYPES:
            con.execute(f"CREATE TABLE calls_log.{table} AS SELECT *, '2026-06' AS work_month "
                        f"FROM read_parquet('{folder / table}/*.parquet')")
        for view in VIEWS:
            con.execute(statement(VIEWS_DIR / f"{view}.sql"))
        for example in sorted(EXAMPLES_DIR.glob("*.sql")):
            con.execute(statement(example)).fetchall()
            print(f"sql/examples/{example.name}: runs")

        def rows(sql: str) -> list:
            return con.execute(sql).fetchall()

        runner_key, back_key = runner_day["person_key"], backfilled["sessions"][0]["person_key"]
        calls = runner_day["calls"]
        runner_span = sum(s["span_min"] for s in runner_day["sessions"])
        want_days = sorted([
            (runner_key, "runner", len(runner_day["sessions"]), runner_span, round(runner_span / 60, 2),
             len(calls), sum(c["countable"] for c in calls), sum(c["flagged"] for c in calls)),
            (back_key, "xlsx_backfill", 2, 150, 2.5, None, None, None)])
        got_days = sorted(rows("SELECT person_key, source, sessions, span_min, CAST(hours AS DOUBLE), calls, "
                               "countable_calls, flagged_calls FROM calls_log.day_summary"))
        if got_days != want_days:
            problems.append(f"day_summary: {got_days} != {want_days}")
        got_weeks = sorted(rows("SELECT person_key, CAST(week_start AS date), days_worked, hours "
                                "FROM calls_log.weekly_attendance"))
        want_weeks = sorted([(runner_key, _dt.date(2026, 6, 1), 1, round(runner_span / 60, 2)),
                             (back_key, _dt.date(2026, 6, 1), 1, 2.5)])   # both days in the week of Mon 1 June
        if got_weeks != want_weeks:
            problems.append(f"weekly_attendance: {got_weeks} != {want_weeks}")
        got_runs = rows("SELECT finished_date, uploads, images, retried, turnaround_p50_s, run_p50_s, est_gb_s "
                        "FROM calls_log.pipeline_metrics ORDER BY finished_date")
        want_runs = [(_dt.date(2026, 6, 1), 1, 2, 0, 180.0, 120.0, 240.0),
                     (_dt.date(2026, 6, 2), 1, 3, 1, 420.0, 120.0, None)]
        if [tuple(r) for r in got_runs] != want_runs:
            problems.append(f"pipeline_metrics: {got_runs} != {want_runs}")
        got_ocr = rows("SELECT calls, flagged, low_duration_conf FROM calls_log.ocr_quality")
        want_ocr = [(len(calls), sum(c["flagged"] for c in calls),
                     sum(1 for c in calls if c["duration_conf"] is not None and c["duration_conf"] < 0.85))]
        if got_ocr != want_ocr:
            problems.append(f"ocr_quality: {got_ocr} != {want_ocr}")
        # made_up_labels: 5 calls labelled, the 5th not a call; 1 start and 1 duration (off by 6 s) misread
        want_acc = [("high", 5, 1, 4, 3, 3, 2, 0.75, 0.75, 1.5)]
        got_acc = rows("SELECT conf_band, labelled, not_a_call, calls, start_exact, duration_exact, both_exact, "
                       "start_exact_rate, duration_exact_rate, duration_mean_error_s FROM calls_log.ocr_accuracy")
        if [tuple(r) for r in got_acc] != want_acc:
            problems.append(f"ocr_accuracy: {got_acc} != {want_acc}")
        for view in VIEWS:
            print(f"calls_log.{view}: " + ("OK" if not any(p.startswith(view) for p in problems) else "DIFFERENT"))
    for line in problems:
        print(f"    {line}")
    print("Lake views: " + ("compute what they should." if not problems else "DIFFERENT."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
