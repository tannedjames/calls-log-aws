"""
Does the lake hold each day's chosen upload, and never a Superseded one? (ADR 9, Phase 3)

For every Person-Day in Folder Status, finds the day's choice
(cloud/status.chosen, as the publisher decides it) and which upload the
lake's sessions rows came from:

    ok           the lake holds the chosen upload, and it is Done
    waiting      the chosen upload isn't Done yet (Pending, Processing, Failed,
                 Parked), so the lake keeps whatever it had
    stale        the chosen upload is Done but the lake holds another one: a
                 Re-run that didn't overwrite, or a Superseded upload
    missing      the chosen upload is Done and the lake has no rows for the day
    quarantined  missing, and quarantine/ holds the chosen upload's envelope
    orphan       a day in the lake that no Person-Day in Folder Status matches

    python3 tools/check_lake_choice.py

Run it in CloudShell (docs/TECHNICAL.md section 11) after the backfill. It
only reads, in a handful of calls whatever the lake's size: one scan of
Folder Status, one Athena query over calls_log.sessions (the calls-log
workgroup; it scans a few MB) and quarantine/'s keys. Standard library and
the AWS CLI. Output names days by person_key, never by name. Exit code 0 =
every day ok or waiting, 1 = a difference or AWS failed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud import lake, publisher  # noqa: E402
from cloud.status import Status, chosen  # noqa: E402
from tools.aws_cli import AwsError, account_id, call  # noqa: E402
from tools.backfill_lake import TABLE, person_day, settings  # noqa: E402
from tools.lake_views import query_rows  # noqa: E402

PROBLEMS = ("stale", "missing", "quarantined", "orphan")
LAKE_DAYS = ("SELECT DISTINCT person_key, CAST(work_date AS varchar) AS work_date, upload_id "
             "FROM calls_log.sessions")


def keys(bucket: str, prefix: str, *, run=subprocess.run) -> List[str]:
    listing = call("s3api", "list-objects-v2", {"Bucket": bucket, "Prefix": prefix}, run=run)
    return sorted(o["Key"] for o in listing.get("Contents", []))


def uploads_by_day(*, run=subprocess.run) -> Dict[str, List[Dict[str, Any]]]:
    """Every upload in Folder Status (no leases), by Person-Day. A read: cloud/status.py stays the only writer."""
    out = call("dynamodb", "scan", {
        "TableName": TABLE, "ConsistentRead": True, "FilterExpression": "begins_with(sk, :up)",
        "ExpressionAttributeValues": {":up": {"S": "up#"}}}, run=run)   # the CLI pages through it
    days: Dict[str, List[Dict[str, Any]]] = {}
    for item in out.get("Items", []):
        plain = {k: int(v["N"]) if "N" in v else v.get("S") for k, v in item.items()}
        days.setdefault(plain["personDay"], []).append(plain)
    return days


def check(bucket: str, *, run=subprocess.run, sleep=time.sleep) -> Counter:
    secret = settings(run=run)
    held: Dict[str, set] = {}
    for person_key, work_date, upload in query_rows(LAKE_DAYS, run=run, sleep=sleep):
        held.setdefault(f"{person_key}_{work_date}", set()).add(upload)
    quarantined = set(keys(bucket, "quarantine/", run=run))
    counts: Counter = Counter({outcome: 0 for outcome in ("ok", "waiting", *PROBLEMS)})
    for day_key, uploads in sorted(uploads_by_day(run=run).items()):
        day = person_day(day_key)
        if day is None:
            continue
        label = f"{lake.person_key(day.name, secret['salt'], secret['prefixes'])}_{day.date}"
        in_lake = sorted(held.pop(label, set()))
        pick = chosen(uploads)
        if pick is None or pick.get("status") != Status.DONE.value:
            counts["waiting"] += 1
        elif in_lake == [pick["uploadId"]]:
            counts["ok"] += 1
        elif in_lake:
            states = {u.get("uploadId"): u.get("status") for u in uploads}
            counts["stale"] += 1
            print(f"stale {label}: the lake holds " + ", ".join(f"{u} ({states.get(u, 'not in Folder Status')})"
                                                                 for u in in_lake)
                  + f"; the day's choice is {pick['uploadId']} (Done)")
        elif publisher.quarantine_key(day_key, pick["uploadId"]) in quarantined:
            counts["quarantined"] += 1
            print(f"quarantined {label}: {pick['uploadId']} failed the publisher's checks")
        else:
            counts["missing"] += 1
            print(f"missing {label}: {pick['uploadId']} is Done but not in the lake")
    for label in sorted(held):
        counts["orphan"] += 1
        print(f"orphan {label}: no Person-Day in Folder Status matches it")
    return counts


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.parse_args(argv)
    try:
        counts = check(f"calls-log-data-{account_id(run=run)}", run=run, sleep=sleep)
    except AwsError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(dict(counts)))
    bad = sum(counts[p] for p in PROBLEMS)
    print("Lake choice: " + ("every day holds its chosen upload." if not bad else f"{bad} day(s) differ."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
