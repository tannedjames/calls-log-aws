"""
Bring the stored Day Sheets' history into the data lake, once (ADR 9, Phase 4).

Days that finished before the lake was switched on exist only as Day Sheets in
results/. This reads each current results/<Person-Day>.xlsx with the PC's own
reader (resync_output.read_sessions) and writes the day's sessions to the
lake as source 'xlsx_backfill', plus the upload's row when it was a Run, at
the same fixed keys the publisher uses.

    python3 tools/backfill_lake.py --dry-run     # what it would write
    python3 tools/backfill_lake.py               # write it

Run it in CloudShell (docs/TECHNICAL.md section 11), after
`pip3 install --user openpyxl pyarrow==24.0.0`. It never writes over a file
that exists: a day the publisher already wrote keeps the runner's richer
rows, and a later Re-run's publish replaces a backfilled file. It never runs
OCR or main.py and never touches the Sheets, Folder Status or results/.

Output names days by person_key, never by name; the salt is read from SSM and
never shown. Exit code 0 = done, 1 = AWS failed, 2 = bad usage.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud import lake, naming  # noqa: E402
from cloud.status import FolderStatus, Status  # noqa: E402
from tools.aws_cli import AwsError, Client, account_id, call, run_cli  # noqa: E402

PREFIX = "results/"
TABLE = "calls-log-folders"
_RESULT = re.compile(r"results/(?P<name>[^/]+?) & (?P<date>\d{4}-\d{2}-\d{2})\.xlsx")
_MISSING = {"404", "NoSuchKey", "NotFound"}

Writer = Callable[[str, List[Dict[str, Any]]], bytes]


def day_of(key: str) -> Optional[naming.PersonDay]:
    """The Person-Day of a Day Sheet's key, without the PC's parser (it imports the OCR stack)."""
    m = _RESULT.fullmatch(key)
    if not m:
        return None
    try:
        _dt.date.fromisoformat(m["date"])
    except ValueError:
        return None
    return naming.PersonDay(m["name"], m["date"])


def chosen_upload(uploads: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The Done upload whose Day Sheet is current: made Ready last; a PC import (no readyAt) last of all."""
    done = [u for u in uploads if u.get("status") == Status.DONE.value]
    return max(done, key=lambda u: (u.get("readyAt") or "", u.get("uploadId") or ""), default=None)


def settings(*, run=subprocess.run) -> Dict[str, Any]:
    """The salt and the NAME_TO_ID keys, from SSM; neither is ever printed."""
    names = ["/calls-log/person_salt", "/calls-log/name_prefixes"]
    out = call("ssm", "get-parameters", {"Names": names, "WithDecryption": True}, run=run)
    values = {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in out.get("Parameters", [])}
    salt = values.get("person_salt", "")
    if not salt or salt == "unset":
        raise SystemExit("person_salt is unset: set it first (tools/put_parameters.py --new-person-salt)")
    prefixes = values.get("name_prefixes", "unset")
    return {"salt": salt, "prefixes": naming.prefixes_from_json(prefixes) if prefixes != "unset" else []}


def exists(bucket: str, key: str, *, run=subprocess.run) -> bool:
    try:
        call("s3api", "head-object", {"Bucket": bucket, "Key": key}, run=run)
    except AwsError as e:
        if e.response["Error"]["Code"] in _MISSING:
            return False
        raise
    return True


def parquet_bytes(table: str, rows: List[Dict[str, Any]]) -> bytes:
    from cloud import publisher   # pyarrow, only when writing

    return publisher.parquet_bytes(table, rows)


def parquet_rows(path: Path) -> List[Dict[str, Any]]:
    """A lake file's rows. One thread: pyarrow 24's reader pool can hang the interpreter's exit."""
    import pyarrow.parquet as pq

    return pq.read_table(path, use_threads=False).to_pylist()


def fetch(bucket: str, key: str, path: Path, *, run=subprocess.run) -> Path:
    """Download one object to path."""
    run_cli(["s3api", "get-object", "--output", "json", str(path)], {"Bucket": bucket, "Key": key}, run=run)
    return path


def backfill(bucket: str, *, dry_run: bool, run=subprocess.run, write: Optional[Writer] = None,
             read_pairs: Optional[Callable[[Path], list]] = None, now: Optional[_dt.datetime] = None) -> Dict[str, int]:
    from cloud import publisher   # typed_rows needs no pyarrow

    if read_pairs is None:
        from resync_output import read_sessions

        def read_pairs(path: Path) -> list:
            return [(s.start_time, s.end_time) for s in read_sessions(path)]

    secret = settings(run=run)
    status = FolderStatus(Client("dynamodb", run=run), TABLE)
    ingested = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    listing = call("s3api", "list-objects-v2", {"Bucket": bucket, "Prefix": PREFIX}, run=run)
    counts = {"written": 0, "in_lake": 0, "no_upload": 0, "failed_check": 0, "not_a_day_sheet": 0}
    for obj in sorted(listing.get("Contents", []), key=lambda o: o["Key"]):
        day = day_of(obj["Key"])
        if day is None:
            counts["not_a_day_sheet"] += 1
            continue
        label = f"{lake.person_key(day.name, secret['salt'], secret['prefixes'])}_{day.date}"
        upload = chosen_upload(status.uploads(day.key))
        if upload is None:
            counts["no_upload"] += 1
            print(f"skipped {label}: no Done upload in Folder Status")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            sheet = Path(tmp) / "day-sheet.xlsx"
            pairs = read_pairs(fetch(bucket, obj["Key"], sheet, run=run))
        rows = lake.backfill_rows(day, upload, pairs, salt=secret["salt"], prefixes=secret["prefixes"])
        problems = lake.check_sessions(rows["sessions"]) or ([] if pairs else ["the Day Sheet has no sessions"])
        if problems:
            counts["failed_check"] += 1
            print(f"skipped {label}: " + "; ".join(problems[:3]))
            continue
        envelope = {"person_key": rows["sessions"][0]["person_key"], "work_date": day.date}
        targets = [(t, publisher.lake_key(t, envelope)) for t in ("sessions", "uploads") if rows[t]]
        if exists(bucket, targets[0][1], run=run):
            counts["in_lake"] += 1
            continue
        for table, key in targets:   # sessions is known to be missing; the uploads row may not be
            if dry_run or (table != "sessions" and exists(bucket, key, run=run)):
                continue
            body = (write or parquet_bytes)(table, publisher.typed_rows(table, rows[table], ingested))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "part.parquet"
                path.write_bytes(body)
                run_cli(["s3api", "put-object", "--body", str(path), "--output", "json"],
                        {"Bucket": bucket, "Key": key, "ContentType": "application/vnd.apache.parquet"}, run=run)
        counts["written"] += 1
        print(f"{'would write' if dry_run else 'wrote'} {label}: {len(rows['sessions'])} session(s)"
              + (", the upload's row" if rows["uploads"] else ""))
    return counts


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, write: Optional[Writer] = None,
         read_pairs=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="show what would be written; write nothing")
    args = ap.parse_args(argv)
    try:
        bucket = f"calls-log-data-{account_id(run=run)}"
        counts = backfill(bucket, dry_run=args.dry_run, run=run, write=write, read_pairs=read_pairs)
    except AwsError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
