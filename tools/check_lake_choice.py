"""
Does the lake hold each day's chosen upload, and never a Superseded one? (ADR 9, Phase 3)

For each current results/<Person-Day>.xlsx, finds the day's choice in Folder
Status (cloud/status.chosen, as the publisher decides it) and reads which
upload the day's lake/sessions file came from:

    ok           the lake holds the chosen upload, and it is Done
    waiting      the chosen upload isn't Done yet (Pending, Processing, Failed,
                 Parked), so the lake keeps whatever it had
    stale        the chosen upload is Done but the lake holds another one: a
                 Re-run that didn't overwrite, or a Superseded upload
    missing      the chosen upload is Done and the lake has no file for the day
    quarantined  missing, and quarantine/ holds the chosen upload's envelope
    orphan       a lake/sessions file no Day Sheet in results/ matches

    python3 tools/check_lake_choice.py

Run it in CloudShell (docs/TECHNICAL.md section 11) after the backfill, with
`pip3 install --user pyarrow==24.0.0`. It only reads: Folder Status, the
lake's sessions files and quarantine/'s keys. Output names days by
person_key, never by name. Exit code 0 = every day ok or waiting, 1 = a
difference or AWS failed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud import lake, publisher  # noqa: E402
from cloud.status import FolderStatus, Status, chosen  # noqa: E402
from tools.aws_cli import AwsError, Client, account_id, call  # noqa: E402
from tools.backfill_lake import PREFIX, TABLE, day_of, fetch, parquet_rows, settings  # noqa: E402

PROBLEMS = ("stale", "missing", "quarantined", "orphan")

Reader = Callable[[Path], List[Dict[str, Any]]]


def keys(bucket: str, prefix: str, *, run=subprocess.run) -> List[str]:
    listing = call("s3api", "list-objects-v2", {"Bucket": bucket, "Prefix": prefix}, run=run)
    return sorted(o["Key"] for o in listing.get("Contents", []))


def check(bucket: str, *, run=subprocess.run, read: Optional[Reader] = None) -> Counter:
    read = read or parquet_rows
    secret = settings(run=run)
    status = FolderStatus(Client("dynamodb", run=run), TABLE)
    in_lake = set(keys(bucket, "lake/sessions/", run=run))
    counts: Counter = Counter({outcome: 0 for outcome in ("ok", "waiting", *PROBLEMS)})
    for sheet in keys(bucket, PREFIX, run=run):
        day = day_of(sheet)
        if day is None:
            continue
        envelope = {"person_key": lake.person_key(day.name, secret["salt"], secret["prefixes"]),
                    "work_date": day.date}
        label = f"{envelope['person_key']}_{day.date}"
        key = publisher.lake_key("sessions", envelope)
        uploads = status.uploads(day.key)
        pick = chosen(uploads)
        if key in in_lake:
            in_lake.discard(key)
            with tempfile.TemporaryDirectory() as tmp:
                held = sorted({r["upload_id"] for r in read(fetch(bucket, key, Path(tmp) / "part.parquet", run=run))})
        else:
            held = []
        if pick is None or pick.get("status") != Status.DONE.value:
            counts["waiting"] += 1
            continue
        if held == [pick["uploadId"]]:
            counts["ok"] += 1
            continue
        if held:
            states = {u.get("uploadId"): u.get("status") for u in uploads}
            counts["stale"] += 1
            print(f"stale {label}: the lake holds " + ", ".join(f"{u} ({states.get(u, 'not in Folder Status')})"
                                                                 for u in held)
                  + f"; the day's choice is {pick['uploadId']} (Done)")
        elif keys(bucket, publisher.quarantine_key(day.key, pick["uploadId"]), run=run):
            counts["quarantined"] += 1
            print(f"quarantined {label}: {pick['uploadId']} failed the publisher's checks")
        else:
            counts["missing"] += 1
            print(f"missing {label}: {pick['uploadId']} is Done but not in the lake")
    for key in sorted(in_lake):
        counts["orphan"] += 1
        print(f"orphan {key}: no Day Sheet in results/ matches it")
    return counts


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, read: Optional[Reader] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.parse_args(argv)
    try:
        counts = check(f"calls-log-data-{account_id(run=run)}", run=run, read=read)
    except AwsError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(dict(counts)))
    bad = sum(counts[p] for p in PROBLEMS)
    print("Lake choice: " + ("every day holds its chosen upload." if not bad else f"{bad} day(s) differ."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
