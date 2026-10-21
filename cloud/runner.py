"""
The runner (AD-2, AD-7, AD-14, AD-15): one Run takes one Ready Folder through
the unchanged pipeline, stores its Day Sheet and, if the AWS Sync Switch is
on, syncs it.

    handler(event, context)    Lambda, one SQS message per Folder
    python -m cloud.runner --upload-id ID --person-day "<Name> & <yyyy-mm-dd>"
                               ECS or a shell: the same Run, same code
    python -m cloud.runner --local DIR [--ignore NAME ...] [--out DIR]
                               no AWS: a local folder through the same
                               extraction; prints PARITY_RESULT like
                               tools/parity_check.py expects

A Run, in main.py's order (AD-7):
  1. begin_run: take the Person-Day's lease, then count the attempt; at the
     4th start the Folder is Parked, the Owner is emailed, and the Run stops;
  2. each image, in the order the PC sorts them, through main.extract_calls;
  3. dedupe -> build_sessions; zero Sessions -> Failed, no Day Sheet;
  4. write_sessions -> results/<Person-Day>.xlsx, a new version;
  5. sync_day (unchanged, 3 tries) if the AWS Sync Switch is on;
  6. Done. Any exception leaves the Folder Failed and the message returns.
One JSON log line per transition. Never OCR text, image content or secrets.

A "resync" message (Story 3.8, FR-13) re-sends the stored Day Sheet of one
Person-Day to the Web App, whatever the AWS Sync Switch says (AD-10), as
resync_output.py does on the PC. A failed re-send returns its message until
its 3rd delivery, then the Owner is emailed and the message is dropped, so a
broken Web App can't fill the dead-letter queue and trip the kill switch.
"""

import runtime_env  # noqa: F401  -- first: sets OpenMP variables before numpy/cv2/paddle

import argparse
import json
import logging
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import main as pipeline
from config import CONFIG
from excel_writer import write_sessions
from parser import CallRecord
from session_builder import build_sessions, dedupe
from sheets_sync import sync_day

from cloud import naming, settings as settings_mod
from cloud.status import MAX_RUNS, Busy, FolderStatus, Status, Superseded, TransitionRefused
from cloud.storage import IMAGE_SUFFIXES, S3Storage, pc_order

log = logging.getLogger("calls-log.runner")

TEMPLATE = Path(__file__).resolve().parent.parent / "sprs.xlsx"
WORKDIR = Path("/tmp/calls-log")
RESYNC_TRIES = 3   # deliveries of one resync message before it is dropped with an email
_ERROR_LIMIT = 500


class RunFailed(Exception):
    """The Run failed; the Folder is Failed and the message should return."""


@dataclass
class Outcome:
    status: str            # Done | Failed | Parked | Busy | Superseded | Skipped
    images: int = 0
    sessions: int = 0
    error: Optional[str] = None


def _event(**fields: Any) -> None:
    """One JSON log line (AD-14): uploadId, personDay, status, attempt, durationMs, images, error."""
    log.info(json.dumps({k: v for k, v in fields.items() if v is not None}, ensure_ascii=False))


def _short(error: BaseException, hide: Tuple[str, ...] = ()) -> str:
    text = f"{type(error).__name__}: {error}"
    for secret in hide:
        if secret:
            text = text.replace(secret, "***")
    return text[:_ERROR_LIMIT]


# ---------------------------------------------------------------------------
# The pipeline, unchanged: the same functions main.py calls, in its order.
# ---------------------------------------------------------------------------

Checkpoints = Optional[Tuple[Callable[[str], Optional[list]], Callable[[str, list], None]]]


def extract(files: List[Path], checkpoints: Checkpoints = None) -> Tuple[List[CallRecord], Dict[str, int]]:
    """
    main.extract_calls on one image at a time, in the given order, records
    concatenated in that order: the same records as one call over all files.
    With checkpoints (load, save), an image already done is not OCR'd again.
    """
    records: List[CallRecord] = []
    totals = {"ocr_failures": 0, "ocr_empty": 0}
    for path in files:
        saved = checkpoints[0](path.name) if checkpoints else None
        if saved is not None:
            records.extend(CallRecord(**r) for r in saved)
            continue
        got, _meta, stats = pipeline.extract_calls([path])
        totals["ocr_failures"] += stats["ocr_failures"]
        totals["ocr_empty"] += stats["ocr_empty"]
        if checkpoints:
            checkpoints[1](path.name, [asdict(r) for r in got])
        records.extend(got)
    return records, totals


def sessions_from(records: List[CallRecord]):
    kept, _dups = dedupe(records)
    return build_sessions(kept)


# ---------------------------------------------------------------------------
# One Run against AWS.
# ---------------------------------------------------------------------------

def run_folder(upload_id: str, person_day: str, *, status: FolderStatus, storage: S3Storage,
               settings: settings_mod.Settings, ssm: Any = None, sns: Any = None,
               holder: Optional[str] = None, workdir: Path = WORKDIR) -> Outcome:
    day = naming.parse(person_day)
    if day is None or day.key != person_day or not naming.is_upload_id(upload_id):
        raise ValueError("the message has no valid uploadId and Person-Day")
    holder = holder or uuid.uuid4().hex
    started = time.monotonic()
    ids = {"uploadId": upload_id, "personDay": day.key}

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        item = status.begin_run(day.key, upload_id, holder)
    except Busy:
        _event(**ids, status="Busy")
        return Outcome("Busy")
    except Superseded as e:     # a later upload of the day was chosen (Story 4.6): no Day Sheet, no sync
        _event(**ids, status="Superseded", error=str(e))
        return Outcome("Superseded")
    except TransitionRefused:   # a duplicate delivery after Done, or never made Ready
        _event(**ids, status="Skipped", error="not in a startable state")
        return Outcome("Skipped")

    attempt = item["attempts"]
    if item["status"] == Status.PARKED.value:
        _alert_parked(sns, settings, day, upload_id, item.get("lastError"))
        _event(**ids, status="Parked", attempt=attempt, error=item.get("lastError"))
        return Outcome("Parked")

    _event(**ids, status="Processing", attempt=attempt)
    folder = workdir / upload_id
    images, day_stored = 0, False
    try:
        names = storage.list_images(upload_id)
        images = len(names)
        if not names:
            raise RuntimeError("the upload has no images")
        if len(names) > CONFIG.max_images:
            raise RuntimeError(f"{len(names)} images, more than max_images ({CONFIG.max_images})")
        folder.mkdir(parents=True, exist_ok=True)
        files = [storage.download(upload_id, n, folder) for n in names]
        checkpoints = (lambda n: storage.get_checkpoint(upload_id, n),
                       lambda n, recs: storage.put_checkpoint(upload_id, n, recs))
        records, ocr = extract(files, checkpoints)
        sessions, _stats = sessions_from(records)
        if not sessions:
            raise RuntimeError(
                f"no sessions extracted from {len(files)} image(s) "
                f"({ocr['ocr_failures']} OCR failure(s), {ocr['ocr_empty']} image(s) with no text)")
        xlsx = folder / "day-sheet.xlsx"
        write_sessions(TEMPLATE, xlsx, sessions)
        storage.put_result(day, xlsx)
        day_stored = True
        sync_on = settings_mod.sync_enabled(ssm)
        settings_mod.configure_core(settings, sync_on=sync_on)
        if sync_on:
            sync_day(day.name, day.date, sessions)
        status.finish(day.key, upload_id)
        _event(**ids, status="Done", attempt=attempt, durationMs=elapsed(), images=images,
               sessions=len(sessions), synced=sync_on)
        return Outcome("Done", images=images, sessions=len(sessions))
    except Exception as e:
        error = _short(e, (settings.sync_secret, settings.webapp_url))
        try:
            status.finish(day.key, upload_id, error)
        except Exception as write_error:   # the message still returns; the next Run retries
            log.error("could not mark %s / %s Failed: %s", day.key, upload_id, type(write_error).__name__)
        _event(**ids, status="Failed", attempt=attempt, durationMs=elapsed(), images=images,
               error=error, dayStored=day_stored)
        raise RunFailed(error) from None
    finally:
        status.release_lease(day.key, holder)
        shutil.rmtree(folder, ignore_errors=True)


def _alert_parked(sns: Any, settings: settings_mod.Settings, day: naming.PersonDay,
                  upload_id: str, last_error: Optional[str]) -> None:
    if not (sns and settings.alerts_topic_arn):
        log.error("Parked %s but no alert topic is configured", upload_id)
        return
    sns.publish(
        TopicArn=settings.alerts_topic_arn,
        Subject="calls-log: a Folder is Parked",
        Message=(f"The Folder {day.key} (upload {upload_id}) failed {MAX_RUNS} Runs and is Parked: "
                 f"it won't run again until it is re-queued.\n\nLast error: {last_error or 'unknown'}\n\n"
                 "Fix the cause, then re-queue it (docs/TECHNICAL.md, Runbook)."))


# ---------------------------------------------------------------------------
# Re-send a stored Day Sheet (Story 3.8, FR-13).
# ---------------------------------------------------------------------------

def resync_day(person_day: str, *, storage: S3Storage, settings: settings_mod.Settings,
               sns: Any = None, deliveries: int = 1, workdir: Path = WORKDIR) -> Outcome:
    """
    The current results/<Person-Day>.xlsx, read back and sent with the unchanged
    sync_day. Raises RunFailed (the message returns) until the RESYNC_TRIES-th
    delivery; then emails the Owner and returns Dropped. Nothing to send is
    Skipped, with an email, and never retried.
    """
    from resync_output import read_sessions   # the PC's reader of a Day Sheet's sessions

    day = naming.parse(person_day)
    if day is None or day.key != person_day:
        raise ValueError("the message has no valid Person-Day")
    started = time.monotonic()
    ids = {"personDay": day.key, "mode": "resync", "attempt": deliveries}
    folder = workdir / f"resync-{uuid.uuid4().hex}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        xlsx = folder / "day-sheet.xlsx"
        sessions = read_sessions(xlsx) if storage.get_result(day, xlsx) else None
        if not sessions:
            why = "no stored Day Sheet" if sessions is None else "the stored Day Sheet has no sessions"
            _alert_resync(sns, settings, day, why, dropped=False)
            _event(**ids, status="Skipped", error=why)
            return Outcome("Skipped", error=why)
        settings_mod.configure_core(settings, sync_on=True)   # an explicit re-send ignores the switch
        sync_day(day.name, day.date, sessions)
        _event(**ids, status="Synced", durationMs=int((time.monotonic() - started) * 1000),
               sessions=len(sessions))
        return Outcome("Synced", sessions=len(sessions))
    except Exception as e:
        error = _short(e, (settings.sync_secret, settings.webapp_url))
        if deliveries >= RESYNC_TRIES:
            _alert_resync(sns, settings, day, error, dropped=True)
            _event(**ids, status="Dropped", error=error)
            return Outcome("Dropped", error=error)
        _event(**ids, status="Failed", error=error)
        raise RunFailed(error) from None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _alert_resync(sns: Any, settings: settings_mod.Settings, day: naming.PersonDay,
                  why: str, *, dropped: bool) -> None:
    if not (sns and settings.alerts_topic_arn):
        log.error("re-send of %s not done (%s) but no alert topic is configured", day.key, why)
        return
    if dropped:
        subject = "calls-log: a re-send to the Sheet failed"
        text = (f"Re-sending the stored Day Sheet of {day.key} to the Google Sheet failed "
                f"{RESYNC_TRIES} times, so it was dropped.\n\nLast error: {why}")
    else:
        subject = "calls-log: nothing to re-send"
        text = f"The re-send of {day.key} found {why}, so nothing was sent to the Google Sheet."
    sns.publish(TopicArn=settings.alerts_topic_arn, Subject=subject, Message=(
        text + "\n\nFix the cause, then re-send it: docs/TECHNICAL.md, Runbook, "
        "\"Re-sending stored Day Sheets\"."))


# ---------------------------------------------------------------------------
# Lambda: one container, clients made once.
# ---------------------------------------------------------------------------

_aws: Dict[str, Any] = {}


def _clients() -> Dict[str, Any]:
    if not _aws:
        import boto3

        ssm = boto3.client("ssm")
        current = settings_mod.load(ssm)
        _aws.update(ssm=ssm, sns=boto3.client("sns"), settings=current,
                    status=FolderStatus(boto3.client("dynamodb"), current.table),
                    storage=S3Storage(boto3.client("s3"), current.bucket))
    return _aws


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """SQS -> Run or re-send. A Busy or failed one reports its message as failed, so SQS returns it."""
    logging.getLogger().setLevel(logging.INFO)
    aws = _clients()
    failures = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            mode = message.get("mode", "process")
            if mode == "resync":
                deliveries = int(record.get("attributes", {}).get("ApproximateReceiveCount", 1))
                resync_day(message["personDay"], storage=aws["storage"], settings=aws["settings"],
                           sns=aws["sns"], deliveries=deliveries)
                continue
            if mode != "process":
                raise ValueError(f"unknown mode {mode!r}")
            outcome = run_folder(message["uploadId"], message["personDay"],
                                 holder=getattr(context, "aws_request_id", None), **aws)
            if outcome.status == "Busy":
                failures.append({"itemIdentifier": record["messageId"]})
        except RunFailed:
            failures.append({"itemIdentifier": record["messageId"]})
        except Exception as e:
            log.error("message %s not processed: %s", record.get("messageId"),
                      _short(e, (aws["settings"].sync_secret, aws["settings"].webapp_url)))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


# ---------------------------------------------------------------------------
# Command line.
# ---------------------------------------------------------------------------

def run_local(folder: Path, ignore: List[str], out: Optional[Path]) -> Dict[str, Any]:
    """A local folder through the same extraction and session rules; nothing synced or stored."""
    names = pc_order([p.name for p in folder.iterdir()
                      if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and p.name not in set(ignore)])
    files = [folder / n for n in names]
    records, _ocr = extract(files)
    sessions, stats = sessions_from(records)
    if out and sessions:
        out.mkdir(parents=True, exist_ok=True)
        write_sessions(TEMPLATE, out / f"{folder.name}.xlsx", sessions)
    return {"images": len(files), "records": len(records), "countable": stats["countable_calls"],
            "sessions": [[s.start_time, s.end_time] for s in sessions]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run one Folder (calls-log runner).")
    ap.add_argument("--upload-id")
    ap.add_argument("--person-day", help='"<Name> & <yyyy-mm-dd>", as in the queue message')
    ap.add_argument("--local", type=Path, help="a local folder: no AWS, prints PARITY_RESULT")
    ap.add_argument("--ignore", action="append", default=[], help="with --local: a file to skip")
    ap.add_argument("--out", type=Path, help="with --local: also write the Day Sheet here")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.local:
        print("PARITY_RESULT " + json.dumps(run_local(args.local, args.ignore, args.out)))
        return 0
    if not (args.upload_id and args.person_day):
        ap.error("give --upload-id and --person-day, or --local")
    try:
        outcome = run_folder(args.upload_id, args.person_day, **_clients())
    except RunFailed:
        return 1
    return 0 if outcome.status in ("Done", "Parked", "Superseded", "Skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
