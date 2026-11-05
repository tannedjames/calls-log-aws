"""
Label a sample of the lake's calls by hand, for OCR accuracy (ADR 9, Phase 4.5).

    python3 tools/label_calls.py sample                # a worksheet of 20 recent calls
    python3 tools/label_calls.py import WORKSHEET.csv  # its labels -> lake/labels/

`sample` picks calls the runner published in the last 7 days, half of them
flagged or under the confidence threshold when there are enough, and writes
~/calls-log-labels/<batch>.zip: the worksheet <batch>.csv and the sampled
screenshots, named as its `image` column says. Beside it, <batch>.json keeps
what the OCR read; the worksheet keeps only a `ref` per call, so a
spreadsheet can't mangle the IDs. Open each row's image, find the call (the
OCR's start and duration are on the row), and fill in:

    true_start     the start on the screen, HH:MM (24-hour, or with AM/PM)
    true_duration  the duration on the screen: 1m 23s, 83 or 1:23; 0 for a missed call
    not_a_call     x when the OCR's row isn't a call at all

Rows with neither true_start nor not_a_call are left out. `import` checks
every filled row first and writes nothing if one is wrong; re-importing the
same batch replaces its file, and the sample's screenshots are then deleted
from CloudShell. The `ocr_accuracy` view reads the labels.

Run it in CloudShell (docs/TECHNICAL.md section 11), with
`pip3 install pyarrow==24.0.0`. The zip holds employees' screenshots:
keep it out of the repo and delete it once imported. Nothing here holds a
name; the lake gets person_key, IDs, times and confidences.
Exit code 0 = done, 1 = AWS failed, 2 = bad usage or a wrong label.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import random
import re
import subprocess
import sys
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import CONFIG  # noqa: E402

from cloud import naming, publisher  # noqa: E402
from cloud.storage import IMAGE_SUFFIXES, pc_order  # noqa: E402
from tools.aws_cli import AwsError, account_id, run_cli  # noqa: E402
from tools.backfill_lake import fetch, parquet_bytes, parquet_rows  # noqa: E402
from tools.check_lake_choice import keys  # noqa: E402

OUT_DIR = Path.home() / "calls-log-labels"
WORKSHEET = ("ref", "image", "ocr_start", "ocr_duration", "true_start", "true_duration", "not_a_call")
SNAPSHOT = ("person_key", "work_date", "upload_id", "image_key", "call_seq", "ocr_start_ts", "ocr_duration_s",
            "row_conf", "start_conf", "duration_conf", "flagged", "parser_version", "ocr_model")
_BATCH = re.compile(r"labels-\d{8}T\d{6}Z")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?::\d{2})?\s*(?:([AaPp])\.?\s*[Mm]\.?)?")
_COLONS = re.compile(r"(?:(\d+):)?(\d+):(\d{2})")
_UNITS = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.I)

Reader = Callable[[Path], List[Dict[str, Any]]]


class LabelError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def _iso(value: Any) -> Optional[str]:
    """A Parquet value (date, datetime) or its string as the contract writes it."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def clock(text: str) -> str:
    """'8:05', '08:05', '8:05 PM' -> 'HH:MM' (24-hour)."""
    m = _CLOCK.fullmatch(text.strip())
    if not m:
        raise LabelError(f"{text!r} is not a time (HH:MM)")
    hour, minute, half = int(m[1]), int(m[2]), (m[3] or "").lower()
    if half:
        if not 1 <= hour <= 12:
            raise LabelError(f"{text!r}: the hour isn't 1-12")
        hour = hour % 12 + (12 if half == "p" else 0)
    if hour > 23 or minute > 59:
        raise LabelError(f"{text!r} is not a time of day")
    return f"{hour:02d}:{minute:02d}"


def seconds(text: str) -> int:
    """'83', '1:23', '1:02:03', '1m 23s', '2h' -> seconds. Blank is refused: it could mean 0 or
    'as the OCR read it', so a missed call is typed as 0."""
    text = text.strip()
    if not text:
        raise LabelError("true_duration is blank: type the duration on the screen, 0 for a missed call")
    if text.isdigit():
        return int(text)
    m = _COLONS.fullmatch(text)
    if m:
        return int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3])
    m = _UNITS.fullmatch(text)
    if m and any(m.groups()):
        return int(m[1] or 0) * 3600 + int(m[2] or 0) * 60 + int(m[3] or 0)
    raise LabelError(f"{text!r} is not a duration (83, 1:23 or 1m 23s)")


def shown(duration_s: Optional[int]) -> str:
    """How the worksheet shows the OCR's duration: text a spreadsheet leaves alone."""
    if duration_s is None:
        return "none"
    return f"{duration_s // 60}m {duration_s % 60:02d}s"


# ---------------------------------------------------------------------------
# The worksheet
# ---------------------------------------------------------------------------

def snapshot(call: Dict[str, Any]) -> Dict[str, Any]:
    """What the OCR read for one lake call, as the label keeps it."""
    return {"person_key": call["person_key"], "work_date": _iso(call["work_date"]),
            "upload_id": call["upload_id"], "image_key": call["image_key"], "call_seq": call["call_seq"],
            "ocr_start_ts": _iso(call["start_ts"]), "ocr_duration_s": call["duration_s"],
            "row_conf": call["row_conf"], "start_conf": call["start_conf"],
            "duration_conf": call["duration_conf"], "flagged": call["flagged"],
            "parser_version": call["parser_version"], "ocr_model": call["ocr_model"]}


def ref(call: Dict[str, Any]) -> str:
    return f"{call['upload_id']}/{call['call_seq']}"


def pick(calls: List[Dict[str, Any]], count: int, rng: random.Random) -> List[Dict[str, Any]]:
    """Up to count calls; half flagged or under the threshold when there are that many."""
    threshold = CONFIG.ocr_confidence_threshold
    doubtful, rest = [], []
    for i, c in enumerate(calls):
        (doubtful if c["flagged"] or (c["row_conf"] is not None and c["row_conf"] < threshold) else rest).append(i)
    picked = rng.sample(doubtful, min(len(doubtful), count // 2))
    picked += rng.sample(rest, min(len(rest), count - len(picked)))
    if len(picked) < count:   # too few clean calls: fill from the doubtful ones left
        left = sorted(set(doubtful) - set(picked))
        picked += rng.sample(left, min(len(left), count - len(picked)))
    return sorted((calls[i] for i in picked), key=lambda c: (_iso(c["work_date"]), c["upload_id"], c["call_seq"]))


def months(since: _dt.date, today: _dt.date) -> List[str]:
    out, day = [], since
    while day <= today:
        out.append(day.strftime("%Y-%m"))
        day = (day.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
    return out


def sample(bucket: str, *, days: int, count: int, seed: Optional[int], out_dir: Path,
           now: _dt.datetime, run=subprocess.run, read: Optional[Reader] = None) -> Tuple[Path, Dict[str, int]]:
    read = read or parquet_rows
    today = now.date()
    since = today - _dt.timedelta(days=days)
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        for month in months(since, today):
            for key in keys(bucket, f"lake/calls/work_month={month}/", run=run):
                if not since.isoformat() <= key[-len("yyyy-mm-dd.parquet"):-len(".parquet")] <= today.isoformat():
                    continue   # <person_key>_<date>.parquet: only the days in range are read
                calls += read(fetch(bucket, key, Path(tmp) / "part.parquet", run=run))
    calls = [c for c in calls if since.isoformat() <= _iso(c["work_date"]) <= today.isoformat()]
    batch = now.strftime("labels-%Y%m%dT%H%M%SZ")
    folder = out_dir / batch
    folder.mkdir(parents=True, exist_ok=True)
    images: Dict[str, List[str]] = {}
    files: Dict[str, str] = {}   # inbox key -> its file in the zip
    rows, snapshots, gone = [], {}, 0
    for call in pick(calls, count, random.Random(seed if seed is not None else batch)):
        upload = call["upload_id"]
        if upload not in images:
            prefix = naming.inbox_key(upload, "")
            names = [k[len(prefix):] for k in keys(bucket, prefix, run=run)
                     if "/" not in k[len(prefix):]]   # as S3Storage.list_images: no subfolders
            images[upload] = pc_order([n for n in names if Path(n).suffix.lower() in IMAGE_SUFFIXES])
        position = int(call["image_key"]) - 1
        if not 0 <= position < len(images[upload]):   # the inbox expired (30 days) or changed
            gone += 1
            continue
        key = naming.inbox_key(upload, images[upload][position])
        if key not in files:   # named by number: a file name can hold a name or a number
            files[key] = f"{len(files) + 1:02d}{Path(key).suffix.lower()}"
            fetch(bucket, key, folder / files[key], run=run)
        snap = snapshot(call)
        snapshots[ref(call)] = snap
        rows.append({"ref": ref(call), "image": files[key], "ocr_start": snap["ocr_start_ts"][11:16],
                     "ocr_duration": shown(snap["ocr_duration_s"]), "true_start": "", "true_duration": "",
                     "not_a_call": ""})
    sheet = folder / f"{batch}.csv"
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:   # the BOM: Excel reads it as UTF-8
        writer = csv.DictWriter(f, fieldnames=WORKSHEET)
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / f"{batch}.json").write_text(json.dumps({"batch": batch, "calls": snapshots}, indent=1),
                                           encoding="utf-8")
    bundle = out_dir / f"{batch}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(folder.iterdir()):
            z.write(path, f"{batch}/{path.name}")
    return bundle, {"calls_in_range": len(calls), "sampled": len(rows), "screenshots": len(files),
                    "images_gone": gone}


# ---------------------------------------------------------------------------
# Labels into the lake
# ---------------------------------------------------------------------------

def label_rows(filled: Iterable[Dict[str, str]], snapshots: Dict[str, Dict[str, Any]], batch: str,
               labelled_at: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The worksheet's labelled rows as lake rows (lake.LABEL_COLUMNS). Raises LabelError naming every bad row."""
    rows, problems, skipped = [], [], 0
    for line, row in enumerate(filled, 2):   # line 1 is the header
        row = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(row.values()):   # an empty line a spreadsheet saved below the data
            continue
        snap = snapshots.get(row.get("ref", ""))
        if snap is None:
            problems.append(f"line {line}: ref {row.get('ref')!r} isn't in this batch")
            continue
        not_a_call = bool(row.get("not_a_call"))
        if not not_a_call and not row.get("true_start"):
            if row.get("true_duration"):
                problems.append(f"line {line}: true_duration without true_start")
            skipped += 1
            continue
        try:
            start = None if not_a_call else clock(row["true_start"])
            duration = None if not_a_call else seconds(row.get("true_duration", ""))
        except LabelError as e:
            problems.append(f"line {line}: {e}")
            continue
        rows.append({**snap, "real_call": not not_a_call,
                     "true_start_ts": start and f"{snap['work_date']}T{start}:00",
                     "true_duration_s": duration, "batch": batch, "labelled_at": labelled_at})
    if problems:
        raise LabelError("\n".join(problems))
    return rows, {"labelled": len(rows), "not_a_call": sum(not r["real_call"] for r in rows), "unlabelled": skipped}


def label_key(month: str, batch: str) -> str:
    return f"lake/labels/work_month={month}/{batch}.parquet"


def batch_of(refs: List[str], out_dir: Path) -> Dict[str, Any]:
    """The sample's saved half: the <batch>.json in out_dir holding the worksheet's refs (whatever a
    browser renamed the worksheet to)."""
    for path in sorted(out_dir.glob("labels-*.json")):
        side = json.loads(path.read_text(encoding="utf-8"))
        if _BATCH.fullmatch(str(side.get("batch"))) and refs and all(r in side["calls"] for r in refs):
            return side
    raise LabelError(f"no sample in {out_dir} holds this worksheet's refs: import it where `sample` ran")


def import_labels(sheet: Path, bucket: str, *, dry_run: bool, now: _dt.datetime, out_dir: Path,
                  run=subprocess.run, write=None) -> Dict[str, int]:
    text = sheet.read_text(encoding="utf-8-sig", errors="replace")   # what matters is ASCII, whatever Excel saved
    header = text.split("\n", 1)[0]
    # Excel in a comma-decimal locale (Vietnamese among them) saves CSV with ';' between columns.
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";" if header.count(";") > header.count(",") else ",")
    missing = [c for c in ("ref", "true_start", "true_duration", "not_a_call") if c not in (reader.fieldnames or [])]
    if missing:
        raise LabelError(f"the worksheet has lost its columns {missing}")
    filled = list(reader)
    side = batch_of([ref for ref in ((r.get("ref") or "").strip() for r in filled) if ref], out_dir)
    batch = side["batch"]
    stamp = now.astimezone(_dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    rows, counts = label_rows(filled, side["calls"], batch, stamp.isoformat())
    by_month: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_month.setdefault(row["work_date"][:7], []).append(row)
    for month, month_rows in sorted(by_month.items()):
        key = label_key(month, batch)
        print(f"{'would write' if dry_run else 'wrote'} {key}: {len(month_rows)} label(s)")
        if dry_run:
            continue
        body = (write or parquet_bytes)("labels", publisher.typed_rows("labels", month_rows, stamp))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "part.parquet"
            path.write_bytes(body)
            run_cli(["s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(path),
                     "--content-type", "application/vnd.apache.parquet", "--output", "json"], run=run)
    if not dry_run:   # the labels are in: the screenshots needn't stay in CloudShell
        shutil.rmtree(out_dir / batch, ignore_errors=True)
        (out_dir / f"{batch}.zip").unlink(missing_ok=True)
    return counts


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, read: Optional[Reader] = None, write=None,
         now: Optional[_dt.datetime] = None, out_dir: Optional[Path] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample", help="write a worksheet of calls to label")
    s.add_argument("--days", type=int, default=7, help="calls from the last N days (default 7; the inbox keeps 30)")
    s.add_argument("--count", type=int, default=20, help="calls to sample (default 20)")
    s.add_argument("--seed", type=int, help="repeat a sample exactly")
    i = sub.add_parser("import", help="write a filled worksheet's labels to the lake")
    i.add_argument("worksheet", type=Path)
    i.add_argument("--dry-run", action="store_true", help="check the labels; write nothing")
    args = ap.parse_args(argv)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        bucket = f"calls-log-data-{account_id(run=run)}"
        if args.command == "sample":
            if not 1 <= args.days <= 30 or args.count < 1:
                print("--days must be 1-30 and --count at least 1", file=sys.stderr)
                return 2
            bundle, counts = sample(bucket, days=args.days, count=args.count, seed=args.seed,
                                    out_dir=out_dir or OUT_DIR, now=now, run=run, read=read)
            print(f"download: {bundle}")
        else:
            counts = import_labels(args.worksheet, bucket, dry_run=args.dry_run, now=now, out_dir=out_dir or OUT_DIR,
                                   run=run, write=write)
    except AwsError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (LabelError, OSError, KeyError, ValueError) as e:
        print(f"not imported: {e}", file=sys.stderr)
        return 2
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
