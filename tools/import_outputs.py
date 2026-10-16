"""
Story 5.1 (FR-14): bring the PC's past Day Sheets into AWS once, from the
Owner's PC after `aws login`, so history and re-sends work from AWS.

    python tools/import_outputs.py --dry-run          # what would happen
    python tools/import_outputs.py                    # import
    python tools/import_outputs.py --verify           # count, then 10 checksums

Every "<Name> & <date>[(n)].xlsx" in output/ goes to results/<Person-Day>.xlsx
as one S3 version each, uploaded so the current version is the file
resync_output.collect_files picks (the base file when there is one): AWS then
re-sends exactly what the PC's resync would (AD-8). Each Person-Day gets a
Folder Status item Done (imported), written through cloud/status.py.

A Person-Day is left alone when it was imported before (so a second run
changes nothing), or when AWS already processed it itself (its own Day Sheet
stays current). An import cut short is finished by the next run: its versions
carry import-sha256 metadata, so they are told apart from the runner's.

--verify checks that every PC Person-Day has a Day Sheet in AWS, then compares
the SHA-256 of 10 random imported ones with S3's own checksum.

Exit code 0 = done (or verified), 1 = AWS failed or a check failed,
2 = bad usage. Nothing on the PC is changed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud import naming  # noqa: E402
from cloud.status import FolderStatus  # noqa: E402
from tools.aws_cli import AwsError, Client, account_id, call, run_cli  # noqa: E402

TABLE = "calls-log-folders"
PREFIX = "results/"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SAMPLE = 10
MARK = "import-sha256"   # S3 metadata on every imported version


@dataclass
class Plan:
    day: naming.PersonDay
    files: List[Path]   # upload order: the last one becomes the current version

    @property
    def key(self) -> str:
        return naming.result_key(self.day)

    @property
    def current(self) -> Path:
        return self.files[-1]


def plan(output_dir: Path) -> List[Plan]:
    """Per Person-Day, its files in upload order, the one resync_output picks last."""
    import resync_output   # the PC's own choice of file, so both sides re-send the same

    chosen = resync_output.collect_files(output_dir)
    groups: Dict[str, List[tuple]] = {}
    days: Dict[str, naming.PersonDay] = {}
    for path in sorted(output_dir.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        parsed = naming.parse_output_stem(path.stem)
        if parsed is None:
            continue
        day, rank = parsed
        days[day.key] = day
        groups.setdefault(day.key, []).append((rank, path))
    plans = []
    for key in sorted(groups):
        day = days[key]
        current = chosen[(day.name, day.date)]
        earlier = [p for _, p in sorted(groups[key]) if p != current]
        plans.append(Plan(day, earlier + [current]))
    return plans


def sha256(path: Path) -> bytes:
    return hashlib.sha256(path.read_bytes()).digest()


def stored_keys(bucket: str, *, run=subprocess.run) -> Set[str]:
    listing = call("s3api", "list-objects-v2", {"Bucket": bucket, "Prefix": PREFIX}, run=run)
    return {obj["Key"] for obj in listing.get("Contents", [])}


def imported_days(*, run=subprocess.run) -> Set[str]:
    """Person-Days with a Done (imported) item. A read: cloud/status.py stays the only writer."""
    out = call("dynamodb", "scan", {
        "TableName": TABLE, "FilterExpression": "imported = :t",
        "ExpressionAttributeValues": {":t": {"S": "true"}}, "ProjectionExpression": "personDay"}, run=run)
    return {item["personDay"]["S"] for item in out.get("Items", [])}


def head(bucket: str, key: str, *, run=subprocess.run) -> Dict:
    return json.loads(run_cli(["s3api", "head-object", "--bucket", bucket, "--key", key,
                               "--checksum-mode", "ENABLED", "--output", "json"], run=run) or "{}")


def upload(bucket: str, key: str, path: Path, *, run=subprocess.run) -> None:
    run_cli(["s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(path),
             "--content-type", XLSX, "--checksum-algorithm", "SHA256",
             "--metadata", f"{MARK}={sha256(path).hex()}", "--output", "json"], run=run)


def do_import(plans: List[Plan], bucket: str, status: FolderStatus, *, dry_run: bool,
              run=subprocess.run) -> Dict[str, int]:
    done = imported_days(run=run)
    keys = stored_keys(bucket, run=run)
    counts = {"imported": 0, "already": 0, "in_aws": 0}
    for p in plans:
        if p.day.key in done:
            counts["already"] += 1
            continue
        if p.key in keys and MARK not in head(bucket, p.key, run=run).get("Metadata", {}):
            counts["in_aws"] += 1
            print(f"left alone, AWS processed it itself: {p.day.key}")
            continue
        if dry_run:
            print(f"would import {p.day.key}: {len(p.files)} version(s), current {p.current.name}")
        else:
            for path in p.files:
                upload(bucket, p.key, path, run=run)
            status.record_import(p.day.key, naming.new_upload_id())
            print(f"imported {p.day.key}: {len(p.files)} version(s)")
        counts["imported"] += 1
    return counts


def verify(plans: List[Plan], bucket: str, *, run=subprocess.run, rng=random) -> bool:
    keys = stored_keys(bucket, run=run)
    missing = [p.day.key for p in plans if p.key not in keys]
    print(f"PC Person-Days: {len(plans)}; with a Day Sheet in AWS: {len(plans) - len(missing)}")
    for key in missing:
        print(f"  missing in AWS: {key}")
    done = imported_days(run=run)
    pool = [p for p in plans if p.day.key in done and p.key in keys]
    sample = rng.sample(pool, min(SAMPLE, len(pool)))
    differ = 0
    for p in sample:
        got = head(bucket, p.key, run=run).get("ChecksumSHA256")
        same = got == base64.b64encode(sha256(p.current)).decode("ascii")
        differ += not same
        print(f"  {'MATCH    ' if same else 'DIFFERENT'} {p.day.key}")
    print(f"checksums: {len(sample) - differ} of {len(sample)} sampled imports match the PC's file")
    return not missing and not differ


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, rng=random) -> int:
    ap = argparse.ArgumentParser(description="Import the PC's Day Sheets into AWS (calls-log, Story 5.1).")
    ap.add_argument("--output", type=Path, default=ROOT / "output", help="the PC's output folder")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="say what would be imported; change nothing")
    mode.add_argument("--verify", action="store_true", help="check the count and 10 random checksums")
    args = ap.parse_args(argv)
    if not args.output.is_dir():
        print(f"{args.output}: not a folder", file=sys.stderr)
        return 2
    plans = plan(args.output)
    if not plans:
        print(f"No Day Sheets in {args.output}.", file=sys.stderr)
        return 2
    try:
        bucket = f"calls-log-data-{account_id(run=run)}"
        if args.verify:
            return 0 if verify(plans, bucket, run=run, rng=rng) else 1
        counts = do_import(plans, bucket, FolderStatus(Client("dynamodb", run=run), TABLE),
                           dry_run=args.dry_run, run=run)
    except AwsError as e:
        print(f"AWS failed: {e} (signed in? `aws login`). Run it again: finished days are skipped.",
              file=sys.stderr)
        return 1
    verb = "would import" if args.dry_run else "imported"
    print(f"PC Person-Days: {len(plans)} = {verb} {counts['imported']} + imported before {counts['already']}"
          f" + processed in AWS {counts['in_aws']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
