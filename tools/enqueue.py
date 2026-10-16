"""
Send Folders to the AWS runner (Story 3.7), from the Owner's PC after
`aws login`:

    python tools/enqueue.py "D:/calls/Nguyen Van A & 2026-06-01" [FOLDER ...]
    python tools/enqueue.py --dry-run FOLDER ...
    python tools/enqueue.py --requeue "Nguyen Van A & 2026-06-01" [--upload-id ID]

Every folder is checked before anything is sent: its name must be a
Person-Day (cloud/naming.py, the parser the runner uses) and it must hold 1
to max_images images. Then, one folder at a time:

  1. a new uploadId, and its Folder Status item as Uploading;
  2. the images, to inbox/<uploadId>/;
  3. Uploading -> Pending (cloud/status.py stays the table's only writer);
  4. one queue message {"uploadId", "personDay", "mode": "process"}.

The runner takes it from there; the Day Sheet lands in results/ and, with the
AWS Sync Switch on, in the Sheet. Nothing on the PC is moved or changed.

--requeue runs a stored Folder again without uploading it (Story 3.8, FR-7):
the newest upload of that Person-Day, or --upload-id, if it is Done, Failed
or Parked and its images are all still in inbox/ (kept 30 days). It becomes
Pending with its attempts back at 0, and one message is queued. The Day Sheet
it writes is a new version; earlier ones stay in results/.

Exit code 0 = all sent, 1 = AWS failed (the folders after it are not sent),
2 = bad usage or a folder refused (nothing sent at all).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud import naming  # noqa: E402
from cloud.status import FolderStatus, Status, TransitionRefused  # noqa: E402
from cloud.storage import IMAGE_SUFFIXES, pc_order  # noqa: E402
from tools.aws_cli import REGION, AwsError, Client, account_id, call, run_cli  # noqa: E402

TABLE = "calls-log-folders"
QUEUE = "calls-log-runs"
REQUEUEABLE = (Status.DONE.value, Status.FAILED.value, Status.PARKED.value)


class Refused(Exception):
    """Nothing was changed; the message says why."""


@dataclass
class Folder:
    path: Path
    day: naming.PersonDay
    images: List[str]   # file names, in the order the PC sorts them


def check(path: Path, max_images: int) -> tuple[Optional[Folder], Optional[str]]:
    """The folder ready to send, or why it is refused."""
    if not path.is_dir():
        return None, "not a folder"
    day = naming.parse(path.name)
    if day is None:
        return None, 'its name is not "<Name> & <yyyy-mm-dd>" with a real date'
    images = pc_order([p.name for p in path.iterdir()
                       if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES])
    if not images:
        return None, "no images in it"
    if len(images) > max_images:
        return None, f"{len(images)} images, more than max_images ({max_images})"
    return Folder(path, day, images), None


def send(folder: Folder, *, bucket: str, queue_url: str, status: FolderStatus,
         run=subprocess.run) -> str:
    """Upload one checked folder, mark it Pending and queue it. Returns its uploadId."""
    upload_id = naming.new_upload_id()
    status.create(folder.day.key, upload_id, len(folder.images))
    for name in folder.images:
        run_cli(["s3", "cp", str(folder.path / name),
                 f"s3://{bucket}/{naming.inbox_key(upload_id, name)}", "--only-show-errors"], run=run)
    status.mark_ready(folder.day.key, upload_id)
    _queue(upload_id, folder.day, queue_url, run)
    return upload_id


def _queue(upload_id: str, day: naming.PersonDay, queue_url: str, run) -> None:
    body = {"uploadId": upload_id, "personDay": day.key, "mode": "process"}
    call("sqs", "send-message", {"QueueUrl": queue_url, "MessageBody": json.dumps(body, ensure_ascii=False)},
         run=run)


def requeue(day: naming.PersonDay, upload_id: Optional[str], *, bucket: str, queue_url: str,
            status: FolderStatus, run=subprocess.run) -> tuple[str, int]:
    """Re-queue a stored upload of this Person-Day; returns (uploadId, images). Raises Refused."""
    uploads = status.uploads(day.key)
    found = [u for u in uploads if u.get("uploadId") == upload_id] if upload_id else uploads[-1:]
    if not found:
        raise Refused(f"{day.key}: no stored upload" + (f" {upload_id}" if upload_id else ""))
    item = found[0]
    upload_id = item["uploadId"]
    if item.get("status") not in REQUEUEABLE:
        raise Refused(f"{day.key}: upload {upload_id} is {item.get('status')}; "
                      "only a Done, Failed or Parked Folder can be re-queued")
    prefix = naming.inbox_key(upload_id, "")
    listing = call("s3api", "list-objects-v2", {"Bucket": bucket, "Prefix": prefix}, run=run)
    images = [o["Key"] for o in listing.get("Contents", [])
              if "/" not in o["Key"][len(prefix):] and o["Key"].lower().endswith(IMAGE_SUFFIXES)]
    if len(images) < int(item.get("imageCount") or 0):
        raise Refused(f"{day.key}: {len(images)} of its {item.get('imageCount')} images are still in inbox/ "
                      "(kept 30 days); send the folder again instead")
    try:
        status.requeue(day.key, upload_id)
    except TransitionRefused:
        raise Refused(f"{day.key}: upload {upload_id} changed state meanwhile; nothing re-queued") from None
    _queue(upload_id, day, queue_url, run)
    return upload_id, len(images)


def _aws(run) -> tuple[str, str, FolderStatus]:
    account = account_id(run=run)
    return (f"calls-log-data-{account}", f"https://sqs.{REGION}.amazonaws.com/{account}/{QUEUE}",
            FolderStatus(Client("dynamodb", run=run), TABLE))


def _main_requeue(person_day: str, upload_id: Optional[str], run) -> int:
    day = naming.parse(person_day)
    if day is None or (upload_id and not naming.is_upload_id(upload_id)):
        print('--requeue needs "<Name> & <yyyy-mm-dd>" (and --upload-id an uploadId)', file=sys.stderr)
        return 2
    try:
        bucket, queue_url, status = _aws(run)
        upload_id, images = requeue(day, upload_id, bucket=bucket, queue_url=queue_url, status=status, run=run)
    except Refused as e:
        print(f"Nothing re-queued. {e}", file=sys.stderr)
        return 2
    except AwsError as e:
        print(f"{day.key}: not re-queued: {e} (signed in? `aws login`)", file=sys.stderr)
        return 1
    print(f"re-queued {day.key}: upload {upload_id}, {images} image(s)")
    return 0


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, max_images: Optional[int] = None) -> int:
    ap = argparse.ArgumentParser(description="Send Folders to the AWS runner (calls-log).")
    ap.add_argument("folders", nargs="*", type=Path, help='folders named "<Name> & <yyyy-mm-dd>"')
    ap.add_argument("--dry-run", action="store_true", help="check the folders and say what would be sent")
    ap.add_argument("--requeue", metavar="PERSON_DAY",
                    help='run a stored Folder of "<Name> & <yyyy-mm-dd>" again, without uploading it')
    ap.add_argument("--upload-id", help="with --requeue: which upload (default: the newest)")
    args = ap.parse_args(argv)
    if bool(args.requeue) == bool(args.folders) or (args.requeue and args.dry_run) \
            or (args.upload_id and not args.requeue):
        ap.print_usage(sys.stderr)
        print("give folders to send, or --requeue PERSON_DAY", file=sys.stderr)
        return 2
    if args.requeue:
        return _main_requeue(args.requeue, args.upload_id, run)
    if max_images is None:
        from config import CONFIG
        max_images = CONFIG.max_images

    checked, refused = [], []
    for path in args.folders:
        folder, why = check(path, max_images)
        if folder is None:
            refused.append(f"{path.name}: {why}")
        else:
            checked.append(folder)
    keys = [f.day.key for f in checked]
    refused += [f"{k}: given twice" for k in sorted({k for k in keys if keys.count(k) > 1})]
    if refused:
        print("Nothing sent. Refused:", *refused, sep="\n  ", file=sys.stderr)
        return 2
    if args.dry_run:
        for f in checked:
            print(f"would send {f.day.key}: {len(f.images)} image(s)")
        return 0

    try:
        bucket, queue_url, status = _aws(run)
    except AwsError as e:
        print(f"Nothing sent: {e} (signed in? `aws login`)", file=sys.stderr)
        return 1

    for done, f in enumerate(checked):
        try:
            upload_id = send(f, bucket=bucket, queue_url=queue_url, status=status, run=run)
        except AwsError as e:
            print(f"{f.day.key}: not sent: {e}", file=sys.stderr)
            print("It never runs as it is; send the folder again. "
                  f"Not tried: {len(checked) - done - 1} folder(s) after it.", file=sys.stderr)
            return 1
        print(f"sent {f.day.key}: {len(f.images)} image(s), upload {upload_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
