"""
Create or update the data lake's views in Athena (ADR 9, Phase 5).

The views are plain SQL in sql/views/, one CREATE OR REPLACE VIEW per file.
This runs each in the calls-log workgroup, in dependency order, then reads one
row from it to prove it works. Run it from CloudShell after the views change
(docs/TECHNICAL.md section 11): the views live in the Glue catalog, which
Terraform leaves alone, so the deploy role needs no Athena query rights.

    python3 tools/lake_views.py          # create or replace them all
    python3 tools/lake_views.py --list   # what it would run

Standard library and the AWS CLI only. Exit code 0 = every view created and
read, 1 = Athena refused one (its reason is printed), 2 = bad usage.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.aws_cli import AwsError, call  # noqa: E402

VIEWS_DIR = ROOT / "sql" / "views"
VIEWS = ["day_summary", "weekly_attendance", "pipeline_metrics", "ocr_quality", "ocr_accuracy"]   # dependency order
WORKGROUP = "calls-log"
DATABASE = "calls_log"
_DONE = ("SUCCEEDED", "FAILED", "CANCELLED")


def statement(path: Path) -> str:
    """The file's SQL without its leading comment lines or a trailing semicolon."""
    lines = path.read_text(encoding="utf-8").splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines).strip().rstrip(";")


def run_query(sql: str, *, run=subprocess.run, sleep: Callable[[float], None] = time.sleep,
              timeout_s: float = 120) -> Tuple[str, str]:
    """(final state, Athena's reason) for one query in the calls-log workgroup."""
    started = call("athena", "start-query-execution", {
        "QueryString": sql, "WorkGroup": WORKGROUP,
        "QueryExecutionContext": {"Catalog": "AwsDataCatalog", "Database": DATABASE}}, run=run)
    qid = started["QueryExecutionId"]
    waited = 0.0
    while True:
        status = call("athena", "get-query-execution", {"QueryExecutionId": qid},
                      run=run)["QueryExecution"]["Status"]
        if status["State"] in _DONE or waited >= timeout_s:
            return status["State"], status.get("StateChangeReason", "")
        sleep(1.0)
        waited += 1.0


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="print the views in order; run nothing")
    args = ap.parse_args(argv)
    missing = [v for v in VIEWS if not (VIEWS_DIR / f"{v}.sql").is_file()]
    if missing:
        print(f"missing in sql/views/: {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.list:
        for view in VIEWS:
            print(f"{DATABASE}.{view}  <- sql/views/{view}.sql")
        return 0
    failed = 0
    for view in VIEWS:
        try:
            state, why = run_query(statement(VIEWS_DIR / f"{view}.sql"), run=run, sleep=sleep)
            if state == "SUCCEEDED":
                state, why = run_query(f"SELECT * FROM {DATABASE}.{view} LIMIT 1", run=run, sleep=sleep)
        except AwsError as e:
            state, why = "FAILED", str(e)
        ok = state == "SUCCEEDED"
        failed += not ok
        print(f"{DATABASE}.{view}: " + ("created, reads OK" if ok else f"{state}: {why}"), flush=True)
    print("Views: " + ("all created." if not failed else f"{failed} failed."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
