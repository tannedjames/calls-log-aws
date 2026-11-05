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
`pip3 install openpyxl pyarrow==24.0.0`. It never writes over a file
that exists, even one the publisher writes while it runs (put-object
--if-none-match): a day the publisher already wrote keeps the runner's
richer rows, and a later Re-run's publish replaces a backfilled file. A run
that fails part-way is safe to repeat; it writes only the missing files. It never runs
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
from cloud.status import FolderStatus, Status, chosen  # noqa: E402
from tools.aws_cli import AwsError, Client, account_id, call, run_cli  # noqa: E402

PREFIX = "results/"
TABLE = "calls-log-folders"
_PERSON_DAY = re.compile(r"(?P<name>[^/]+?) & (?P<date>\d{4}-\d{2}-\d{2})")
_MISSING = {"404", "NoSuchKey", "NotFound"}
_WRITTEN_MEANWHILE = {"PreconditionFailed", "ConditionalRequestConflict"}   # put-object --if-none-match

Writer = Callable[[str, List[Dict[str, Any]]], bytes]


def person_day(text: str) -> Optional[naming.PersonDay]:
    """'<Name> & <yyyy-mm-dd>' as a PersonDay, without the PC's parser (it imports the OCR stack)."""
    m = _PERSON_DAY.fullmatch(text)
    if not m:
        return None
    try:
        _dt.date.fromisoformat(m["date"])
    except ValueError:
        return None
    return naming.PersonDay(m["name"], m["date"])


def day_of(key: str) -> Optional[naming.PersonDay]:
    """The Person-Day of a Day Sheet's key."""
    if not (key.startswith(PREFIX) and key.endswith(".xlsx")):
        return None
    return person_day(key[len(PREFIX):-len(".xlsx")])


def chosen_upload(uploads: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The Done upload whose Day Sheet is current: made Ready last; a PC import (no readyAt) last of all."""
    return chosen([u for u in uploads if u.get("status") == Status.DONE.value])


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
    # Bucket and key as flags, not --cli-input-json: newer CLI v2 releases (CloudShell's, 2026-11)
    # refuse get-object's required arguments from the JSON once its positional outfile is given.
    run_cli(["s3api", "get-object", "--bucket", bucket, "--key", key, "--output", "json", str(path)], run=run)
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
        person = lake.person_key(day.name, secret["salt"], secret["prefixes"])
        label = f"{person}_{day.date}"
        upload = chosen_upload(status.uploads(day.key))
        if upload is None:
            counts["no_upload"] += 1
            print(f"skipped {label}: no Done upload in Folder Status")
            continue
        # Each table on its own, before reading the Day Sheet: a day half written by an
        # earlier run that failed gets its missing file, and a day in the lake costs no download.
        envelope = {"person_key": person, "work_date": day.date}
        tables = ("sessions",) + (("uploads",) if upload.get("imported") != "true" else ())
        missing = [t for t in tables if not exists(bucket, publisher.lake_key(t, envelope), run=run)]
        if not missing:
            counts["in_lake"] += 1
            continue
        pairs: list = []
        if "sessions" in missing:
            with tempfile.TemporaryDirectory() as tmp:
                pairs = read_pairs(fetch(bucket, obj["Key"], Path(tmp) / "day-sheet.xlsx", run=run))
        rows = lake.backfill_rows(day, upload, pairs, salt=secret["salt"], prefixes=secret["prefixes"])
        problems = [] if "sessions" not in missing else (
            lake.check_sessions(rows["sessions"]) or ([] if pairs else ["the Day Sheet has no sessions"]))
        if problems:
            counts["failed_check"] += 1
            print(f"skipped {label}: " + "; ".join(problems[:3]))
            continue
        wrote = []
        for table in missing:
            if dry_run:
                wrote.append(table)
                continue
            body = (write or parquet_bytes)(table, publisher.typed_rows(table, rows[table], ingested))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "part.parquet"
                path.write_bytes(body)
                try:   # only if still absent: the publisher may have written it since the check
                    run_cli(["s3api", "put-object", "--bucket", bucket, "--key", publisher.lake_key(table, envelope),
                             "--body", str(path), "--if-none-match", "*",
                             "--content-type", "application/vnd.apache.parquet", "--output", "json"], run=run)
                except AwsError as e:
                    if e.response["Error"]["Code"] not in _WRITTEN_MEANWHILE:
                        raise
                    continue
            wrote.append(table)
        if not wrote:
            counts["in_lake"] += 1
            continue
        counts["written"] += 1
        print(f"{'would write' if dry_run else 'wrote'} {label}: "
              + ", ".join(f"{len(rows['sessions'])} session(s)" if t == "sessions" else "the upload's row"
                          for t in wrote))
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
