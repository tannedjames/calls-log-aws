"""
Send Folders to the AWS runner (Story 3.7), from the Owner's PC after
`aws login`:

    python tools/enqueue.py "D:/calls/Nguyen Van A & 2026-06-01" [FOLDER ...]
    python tools/enqueue.py --dry-run FOLDER ...

Every folder is checked before anything is sent: its name must be a
Person-Day (cloud/naming.py, the parser the runner uses) and it must hold 1
to max_images images. Then, one folder at a time:

  1. a new uploadId, and its Folder Status item as Uploading;
  2. the images, to inbox/<uploadId>/;
  3. Uploading -> Pending (cloud/status.py stays the table's only writer);
  4. one queue message {"uploadId", "personDay", "mode": "process"}.

The runner takes it from there; the Day Sheet lands in results/ and, with the
AWS Sync Switch on, in the Sheet. Nothing on the PC is moved or changed.

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
from cloud.status import FolderStatus  # noqa: E402
from cloud.storage import IMAGE_SUFFIXES, pc_order  # noqa: E402
from tools.aws_cli import REGION, AwsError, Client, account_id, call, run_cli  # noqa: E402

TABLE = "calls-log-folders"
QUEUE = "calls-log-runs"


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
    body = {"uploadId": upload_id, "personDay": folder.day.key, "mode": "process"}
    call("sqs", "send-message", {"QueueUrl": queue_url, "MessageBody": json.dumps(body, ensure_ascii=False)},
         run=run)
    return upload_id


def main(argv: Optional[List[str]] = None, *, run=subprocess.run, max_images: Optional[int] = None) -> int:
    ap = argparse.ArgumentParser(description="Send Folders to the AWS runner (calls-log).")
    ap.add_argument("folders", nargs="+", type=Path, help='folders named "<Name> & <yyyy-mm-dd>"')
    ap.add_argument("--dry-run", action="store_true", help="check the folders and say what would be sent")
    args = ap.parse_args(argv)
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
        account = account_id(run=run)
    except AwsError as e:
        print(f"Nothing sent: {e} (signed in? `aws login`)", file=sys.stderr)
        return 1
    bucket = f"calls-log-data-{account}"
    queue_url = f"https://sqs.{REGION}.amazonaws.com/{account}/{QUEUE}"
    status = FolderStatus(Client("dynamodb", run=run), TABLE)

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
