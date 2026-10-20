"""
The upload and status page (Epic 4, AD-17): one Lambda behind a public
Function URL. GET / serves cloud/static/upload.html; everything under /api/
needs the passphrase (cloud/passphrase.py) in the x-calls-log-passphrase
header, checked before any data is touched.

    POST /api/hello    the passphrase works; the limits and the AWS Sync Switch
    POST /api/check    {"folders": [{"folder", "files": [{"name", "size"}]}]}
                       -> each folder accepted or refused, with the reason
    POST /api/uploads  {"folder", "files"} -> a new upload (Uploading) and one
                       presigned POST per image, for its exact key
    POST /api/ready    {"personDay", "uploadId"} -> every image arrived:
                       Pending and queued (Pending again: queued again)
    POST /api/status   the newest upload of each Person-Day of the last 7 days
    POST /api/sheets   {"name", "date"} -> matching Day Sheets, each with a
                       5-minute download link

The Owner tab's routes need the owner password (owner_passphrase_hash) in the
x-calls-log-owner header instead; the upload passphrase never opens them:

    POST /api/owner/hello    the switch and whether the runner is on
    POST /api/owner/requeue  {"personDay", "uploadId"?} -> a stored upload runs
                             again (cloud/requeue.py, as enqueue.py --requeue)
    POST /api/owner/resend   {"personDay"} -> its stored Day Sheet is sent to the
                             Sheet again (as tools/resync.py)
    POST /api/owner/runner   {"action": "status" | "stop" | "start"}
    POST /api/owner/timings  {"days"} -> Ready -> Done times (cloud/timing.py)
    POST /api/owner/settings the switch, the employee list and the alert addresses
    POST /api/owner/sync     {"on": true | false} -> the AWS Sync Switch
    POST /api/owner/password {"which": "upload" | "owner", "new"} -> its new hash
    POST /api/owner/alerts   {"action": "add", "address"} | {"action": "remove", "id"}
    POST /api/owner/employees {"names": [...]} -> name_prefixes, as the Web App keys it

The Owner tab writes only cloud/owner_settings.py WRITABLE, never the Web App
URL or its secret.

Errors are {"ok": false, "error": "<plain words>", "code", "args"}: the page
shows the code in English or Vietnamese, other clients the words.
cloud/status.py stays the only writer of Folder Status and cloud/naming.py the
only reader of folder names. Log lines carry the route, the result and the
uploadId, never a name, a passphrase or a link.
"""

import runtime_env  # noqa: F401  -- first: naming.parse imports main, which loads numpy

import base64
import datetime as _dt
import hashlib
import hmac
import json
import logging
import math
import os
import re
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cloud import killswitch, naming, owner_settings, passphrase as passphrase_mod, requeue as shared_requeue
from cloud import settings as settings_mod, timing
from cloud.status import FolderStatus, Status, TransitionRefused
from cloud.storage import IMAGE_SUFFIXES, S3Storage, pc_order

log = logging.getLogger("calls-log.intake")

HEADER = "x-calls-log-passphrase"
OWNER_HEADER = "x-calls-log-owner"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_FILES = 500             # entries in one folder's listing, images or not
MAX_FOLDERS = 100           # folders in one check
MAX_BODY_BYTES = 1024 * 1024   # 100 folders of 40 file names fit easily
UPLOAD_SECONDS = 15 * 60
DOWNLOAD_SECONDS = 5 * 60
STATUS_DAYS = 8             # UTC days queried: the last 7 local days, whatever the time zone
MAX_SHEETS = 50
SETTINGS_SECONDS = 300      # a changed passphrase or employee list applies within 5 minutes
RECHECK_SECONDS = 10        # a refused password re-reads settings at most this often (a change made elsewhere)
MAX_TIMING_DAYS = 28
PAGE = Path(__file__).resolve().parent / "static" / "upload.html"
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".webp": "image/webp", ".bmp": "image/bmp"}
HEIC = (".heic", ".heif")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Refused(Exception):
    """A request the page can't have: the HTTP status, the reason in plain words and its code."""

    def __init__(self, status: int, error: str, code: Optional[str] = None, args: Optional[Dict[str, Any]] = None):
        super().__init__(error)
        self.status, self.error, self.code, self.args_ = status, error, code, args or {}


# Every code a response can carry; the page has an English and a Vietnamese
# string for each (a test checks both lists).
CODES = (
    "bad_name", "no_employee_list", "not_registered", "listed_twice", "empty_file", "too_big", "only_heic",
    "no_images", "too_many", "same_day", "folders_count", "file_list", "file_list_invalid", "queue_failed",
    "not_an_upload", "no_such_upload", "already", "partial", "changed_meanwhile", "name_or_date",
    "date_format", "not_set_up", "wrong_passphrase", "owner_not_set", "wrong_owner", "bad_json",
    "too_large", "not_found", "server_error", "bad_day", "no_sheet", "no_runner", "bad_action",
    "runner_failed", "days_range",
    "bad_switch", "bad_which", "password_short", "password_spaces", "password_accents", "password_weak",
    "same_as_other", "save_failed", "no_topic", "alerts_failed", "bad_action_alerts", "not_confirmed",
    "last_alert",
    *("employees_invalid", "employees_empty", "employees_many", "employee_blank", "employee_long",
      "employee_twice", "prefix_clash", "bad_email", "employees_too_long"),                    # cloud/owner_settings.py
    *("no_upload", "not_requeueable", "images_gone", "changed"),   # cloud/requeue.py
)


def _no(error: str, code: str, **args: Any) -> Dict[str, Any]:
    return {"ok": False, "error": error, "code": code, "args": args}


# ----- checking a folder (FR-2) ------------------------------------------------

def _suffix(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def _files(raw: Any) -> List[Tuple[str, int]]:
    """The listing the page sent, as (file name, size); anything malformed is refused."""
    if not isinstance(raw, list) or len(raw) > MAX_FILES:
        raise Refused(400, f"A folder's file list must be a list of at most {MAX_FILES} files.",
                      "file_list", {"max": MAX_FILES})
    out = []
    for f in raw:
        name = f.get("name") if isinstance(f, dict) else None
        size = f.get("size") if isinstance(f, dict) else None
        if (not isinstance(name, str) or not name or len(name) > 255 or "/" in name or "\\" in name
                or name in (".", "..") or not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise Refused(400, "The file list isn't valid.", "file_list_invalid")
        out.append((unicodedata.normalize("NFC", name), size))
    return out


def check_folder(folder: str, files: List[Tuple[str, int]], *, prefixes, max_images: int) -> Dict[str, Any]:
    """
    Whether a folder may be uploaded, as the server decides it:
    {"ok": True, "personDay", "images": [names in the PC's order], "ignored": [...]}
    or {"ok": False, "error": "<why>", "code", "args"}.
    """
    day = naming.parse(folder) if isinstance(folder, str) and folder.strip() else None
    if day is None:
        return _no('its name isn\'t "<Name> & <yyyy-mm-dd>" with a real date', "bad_name")
    if not prefixes:
        return _no("the employee list isn't set up yet "
                   "(the Owner runs tools/put_parameters.py --from-private-files)", "no_employee_list")
    if naming.matching_prefix(day.name, prefixes) is None:
        return _no(f"{day.name} is not a registered employee", "not_registered", name=day.name)
    images, ignored, heic, seen = [], [], 0, set()
    for name, size in files:
        if name in seen:
            return _no(f"{name} is listed twice", "listed_twice", file=name)
        seen.add(name)
        suffix = _suffix(name)
        if suffix not in IMAGE_SUFFIXES:
            heic += suffix in HEIC
            ignored.append(name)
        elif size == 0:
            return _no(f"{name} is empty", "empty_file", file=name)
        elif size > MAX_IMAGE_BYTES:
            mb = math.ceil(size / 1024 / 1024)   # never rounded down to the limit itself
            return _no(f"{name} is {mb} MB, more than 15 MB", "too_big", file=name, mb=mb)
        else:
            images.append(name)
    if not images:
        if heic:
            return _no("it has only HEIC photos: save the screenshots as JPEG or PNG "
                       "(or convert them) and choose the folder again", "only_heic")
        return _no("it has no images (.jpg, .jpeg, .png, .webp or .bmp)", "no_images")
    if len(images) > max_images:
        return _no(f"it has {len(images)} images, more than {max_images}", "too_many",
                   count=len(images), max=max_images)
    return {"ok": True, "personDay": day.key, "images": pc_order(images), "ignored": sorted(ignored)}


# ----- the passphrase (AD-17) ------------------------------------------------------

_verified: Dict[str, bytes] = {}   # stored hash -> SHA-256 of the passphrase that matched it (upload, owner)


def authorised(given: Optional[str], stored: str) -> bool:
    """
    Constant-time check of the passphrase against the stored PBKDF2 hash. The
    first match in a container is remembered as a SHA-256 digest, so later
    calls skip the PBKDF2; a wrong passphrase always pays it.
    """
    if not given or not stored:
        return False
    digest = hashlib.sha256(given.encode("utf-8")).digest()
    known = _verified.get(stored)
    if known is not None and hmac.compare_digest(digest, known):
        return True
    if passphrase_mod.verify(given, stored):
        if len(_verified) >= 4:   # old hashes after a change are never asked for again
            _verified.clear()
        _verified[stored] = digest
        return True
    return False


# ----- the services, once per container -------------------------------------------

@dataclass
class Services:
    settings: settings_mod.Settings
    status: FolderStatus
    storage: S3Storage
    s3: Any
    sqs: Any
    ssm: Any
    max_images: int
    today: Callable[[], _dt.date] = lambda: _dt.datetime.now(_dt.timezone.utc).date()
    now: Callable[[], _dt.datetime] = lambda: _dt.datetime.now(_dt.timezone.utc)
    lambda_client: Any = None   # the Owner tab's runner on/off
    sns: Any = None             # the Owner tab's alert addresses


_services: Optional[Services] = None
_loaded_at = 0.0


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-2"


def s3_client(region: str):
    """
    An S3 client whose presigned links name the bucket's regional endpoint
    (https://<bucket>.s3.<region>.amazonaws.com), the origin the page's CSP
    allows; the global endpoint would redirect a browser's upload.
    """
    import boto3
    from botocore.config import Config

    return boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))


def services(clock: Callable[[], float] = time.monotonic) -> Services:
    """The clients and settings; the parameters are re-read every 5 minutes."""
    global _services, _loaded_at
    if _services is None or clock() - _loaded_at > SETTINGS_SECONDS:
        import boto3   # in the runner image
        from config import CONFIG

        ssm = boto3.client("ssm")
        settings_mod.reset_cache()
        current = settings_mod.load(ssm)
        s3 = s3_client(_region())
        _services = Services(current, FolderStatus(boto3.client("dynamodb"), current.table),
                             S3Storage(s3, current.bucket), s3, boto3.client("sqs"), ssm, CONFIG.max_images,
                             lambda_client=boto3.client("lambda"), sns=boto3.client("sns"))
        _loaded_at = clock()
    return _services


# ----- the API -------------------------------------------------------------------------

def hello(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "maxImages": svc.max_images, "maxImageMb": MAX_IMAGE_BYTES // (1024 * 1024),
            "syncEnabled": _sync_switch(svc)}


def check(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    folders = body.get("folders")
    if not isinstance(folders, list) or not folders or len(folders) > MAX_FOLDERS:
        raise Refused(400, f"Send between 1 and {MAX_FOLDERS} folders to check.", "folders_count",
                      {"max": MAX_FOLDERS})
    out, keys = [], {}
    for f in folders:
        folder = f.get("folder") if isinstance(f, dict) else None
        result = check_folder(folder, _files(f.get("files") if isinstance(f, dict) else None),
                              prefixes=svc.settings.name_prefixes, max_images=svc.max_images)
        if result["ok"]:
            if result["personDay"] in keys:
                result = _no(f"it is the same Person-Day as {keys[result['personDay']]}", "same_day",
                             other=keys[result["personDay"]])
            else:
                keys[result["personDay"]] = folder
        out.append({"folder": folder, **({k: v for k, v in result.items() if k != "images"}),
                    **({"images": len(result["images"])} if result["ok"] else {})})
    return {"ok": True, "folders": out}


def _form(svc: Services, upload_id: str, name: str) -> Dict[str, Any]:
    content_type = CONTENT_TYPES[_suffix(name)]
    post = svc.s3.generate_presigned_post(
        Bucket=svc.settings.bucket, Key=naming.inbox_key(upload_id, name),
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_IMAGE_BYTES]],
        ExpiresIn=UPLOAD_SECONDS)
    return {"name": name, "url": post["url"], "fields": post["fields"]}


def create_upload(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    folder = body.get("folder")
    result = check_folder(folder, _files(body.get("files")),
                          prefixes=svc.settings.name_prefixes, max_images=svc.max_images)
    if not result["ok"]:
        raise Refused(400, f"{folder}: {result['error']}. Nothing was uploaded.", result["code"], result["args"])
    upload_id = naming.new_upload_id()
    svc.status.create(result["personDay"], upload_id, len(result["images"]))
    return {"ok": True, "uploadId": upload_id, "personDay": result["personDay"],
            "ignored": result["ignored"], "expiresInSeconds": UPLOAD_SECONDS,
            "forms": [_form(svc, upload_id, name) for name in result["images"]]}


def _send(svc: Services, body: Dict[str, str], route: str, error: str) -> None:
    try:
        svc.sqs.send_message(QueueUrl=svc.settings.queue_url, MessageBody=json.dumps(body, ensure_ascii=False))
    except Exception as e:
        log.error(json.dumps({"route": route, **({"uploadId": body["uploadId"]} if "uploadId" in body else {}),
                              "error": type(e).__name__}))
        raise Refused(502, error, "queue_failed") from None


def _queue(svc: Services, person_day: str, upload_id: str) -> None:
    _send(svc, {"uploadId": upload_id, "personDay": person_day, "mode": "process"}, "ready",
          "It is Ready, but it couldn't be queued. Click Ready again.")


def ready(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    person_day, upload_id = body.get("personDay"), body.get("uploadId")
    if not isinstance(person_day, str) or not isinstance(upload_id, str) or not naming.is_upload_id(upload_id):
        raise Refused(400, "That isn't an upload.", "not_an_upload")
    item = svc.status.get(person_day, upload_id)
    if item is None:
        raise Refused(404, "No such upload.", "no_such_upload")
    if item["status"] == Status.PENDING.value:   # Ready already, maybe without its message: queue it again
        _queue(svc, person_day, upload_id)
        return {"ok": True, "status": Status.PENDING.value, "queuedAgain": True}
    if item["status"] != Status.UPLOADING.value:
        raise Refused(409, f"This upload is already {item['status']}. To correct the day, "
                           "upload the whole folder again.", "already", {"status": item["status"]})
    arrived, declared = len(svc.storage.list_images(upload_id)), int(item["imageCount"])
    if arrived != declared:
        raise Refused(409, f"Only {arrived} of {declared} images arrived, so it can't be made Ready. "
                           "Choose the folder and upload it again; the partial upload is deleted after 30 days.",
                      "partial", {"arrived": arrived, "declared": declared})
    try:
        svc.status.mark_ready(person_day, upload_id)
    except TransitionRefused:
        raise Refused(409, "This upload changed meanwhile; refresh the status.", "changed_meanwhile") from None
    _queue(svc, person_day, upload_id)
    return {"ok": True, "status": Status.PENDING.value}


_SHOWN = ("personDay", "uploadId", "status", "imageCount", "attempts", "createdAt", "updatedAt",
          "lastRunAt", "lastError")


def status(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    today = svc.today()
    latest: Dict[str, Dict[str, Any]] = {}
    uploads: Dict[str, int] = {}
    for back in range(STATUS_DAYS):
        for item in svc.status.recent((today - _dt.timedelta(days=back)).isoformat()):
            pd = item.get("personDay") or ""
            uploads[pd] = uploads.get(pd, 0) + 1
            if pd not in latest or (item.get("uploadId") or "") > (latest[pd].get("uploadId") or ""):
                latest[pd] = item
    rows = sorted(latest.values(), key=lambda i: i.get("uploadId") or "", reverse=True)
    return {"ok": True, "syncEnabled": _sync_switch(svc),
            "folders": [{**{k: i.get(k) for k in _SHOWN}, "uploads": uploads[i.get("personDay") or ""]}
                        for i in rows]}


def _disposition(file_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", file_name).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r'["\\\r\n]', "_", ascii_name) or "day-sheet.xlsx"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(file_name, safe='')}"


def sheets(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    name, date = body.get("name") or "", body.get("date") or ""
    if not isinstance(name, str) or not isinstance(date, str) or not (name.strip() or date.strip()):
        raise Refused(400, "Give a name, a date or both.", "name_or_date")
    date = date.strip()
    if date and not _DATE.fullmatch(date):
        raise Refused(400, "The date must look like 2026-06-01.", "date_format")
    want = naming.norm(name)
    found = []
    kwargs: Dict[str, Any] = {"Bucket": svc.settings.bucket, "Prefix": "results/"}
    while True:
        page = svc.s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            stem = obj["Key"][len("results/"):]
            if "/" in stem or not stem.endswith(".xlsx"):
                continue
            day = naming.parse(stem[:-len(".xlsx")])
            if day is None or (date and day.date != date) or (want and want not in naming.norm(day.name)):
                continue
            found.append((day, obj))
        if not page.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = page["NextContinuationToken"]
    found.sort(key=lambda f: f[0].name)                  # newest date first, then by name
    found.sort(key=lambda f: f[0].date, reverse=True)
    out = []
    for day, obj in found[:MAX_SHEETS]:
        url = svc.s3.generate_presigned_url(
            "get_object", ExpiresIn=DOWNLOAD_SECONDS,
            Params={"Bucket": svc.settings.bucket, "Key": obj["Key"], "ResponseContentType": XLSX,
                    "ResponseContentDisposition": _disposition(f"{day.key}.xlsx")})
        modified = obj.get("LastModified")
        out.append({"personDay": day.key, "name": day.name, "date": day.date, "url": url,
                    "updatedAt": modified.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if isinstance(modified, _dt.datetime) else None})
    return {"ok": True, "sheets": out, "more": len(found) > MAX_SHEETS}


def _sync_switch(svc: Services) -> Optional[bool]:
    try:
        return settings_mod.sync_enabled(svc.ssm)
    except Exception as e:   # shown as "unknown"; it never blocks the page
        log.warning(json.dumps({"syncSwitch": "unreadable", "error": type(e).__name__}))
        return None


ROUTES: Dict[str, Callable[[Services, Dict[str, Any]], Dict[str, Any]]] = {
    "/api/hello": hello, "/api/check": check, "/api/uploads": create_upload,
    "/api/ready": ready, "/api/status": status, "/api/sheets": sheets,
}


# ----- the Owner tab (owner password) ------------------------------------------------

def _runner_state(svc: Services) -> Optional[str]:
    if not svc.settings.runner_function or svc.lambda_client is None:
        return None
    try:
        return killswitch.runner_state(svc.lambda_client, svc.settings.runner_function)
    except Exception as e:   # shown as "unknown"; the tab still opens
        log.warning(json.dumps({"runner": "unreadable", "error": type(e).__name__}))
        return None


def owner_hello(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "syncEnabled": _sync_switch(svc), "runner": _runner_state(svc)}


def _day(body: Dict[str, Any]) -> naming.PersonDay:
    person_day = body.get("personDay")
    day = naming.parse(person_day) if isinstance(person_day, str) and person_day.strip() else None
    if day is None:
        raise Refused(400, 'That isn\'t a Person-Day ("<Name> & <yyyy-mm-dd>").', "bad_day")
    return day


def owner_requeue(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    """Run a stored upload again: Done, Failed or Parked -> Pending, one message (as enqueue.py --requeue)."""
    day, upload_id = _day(body), body.get("uploadId")
    if upload_id is not None and (not isinstance(upload_id, str) or not naming.is_upload_id(upload_id)):
        raise Refused(400, "That isn't an upload.", "not_an_upload")

    def send(message: Dict[str, str]) -> None:
        _send(svc, message, "owner/requeue", "It is Pending again, but it couldn't be queued. Click Re-run again.")

    try:
        upload_id, images = shared_requeue.requeue(day, upload_id, status=svc.status,
                                                   list_images=svc.storage.list_images, send=send)
    except shared_requeue.Refused as e:
        if e.code == "not_requeueable" and e.args_.get("status") == Status.PENDING.value:
            # Pending already, maybe without its message (a failed send): queue it again,
            # as Ready does; the runner skips a duplicate.
            send({"uploadId": e.args_["uploadId"], "personDay": day.key, "mode": "process"})
            return {"ok": True, "uploadId": e.args_["uploadId"], "status": Status.PENDING.value, "queuedAgain": True}
        raise Refused(409, str(e), e.code, e.args_) from None
    return {"ok": True, "uploadId": upload_id, "images": images, "status": Status.PENDING.value}


def owner_resend(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    """Send a stored Day Sheet to its Sheet again, whatever the AWS Sync Switch says (AD-10, as resync.py)."""
    day = _day(body)
    key = naming.result_key(day)
    listing = svc.s3.list_objects_v2(Bucket=svc.settings.bucket, Prefix=key)
    if not any(obj["Key"] == key for obj in listing.get("Contents", [])):
        raise Refused(404, f"No Day Sheet is stored for {day.key}.", "no_sheet")
    _send(svc, {"personDay": day.key, "mode": "resync"}, "owner/resend", "It couldn't be queued. Try again.")
    return {"ok": True, "personDay": day.key}


def owner_runner(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    """Whether AWS processes queued folders, and the switch to stop or start it (the runner's queue trigger)."""
    action = body.get("action", "status")
    if action not in ("status", "stop", "start"):
        raise Refused(400, 'The action must be "status", "stop" or "start".', "bad_action")
    if not svc.settings.runner_function or svc.lambda_client is None:
        raise Refused(503, "The page doesn't know the runner (CALLS_LOG_RUNNER_FUNCTION is not set).", "no_runner")
    try:
        changed: List[Dict[str, str]] = []
        if action == "stop":
            changed = killswitch.stop_runner(svc.lambda_client, svc.settings.runner_function)
        elif action == "start":
            changed = killswitch.start_runner(svc.lambda_client, svc.settings.runner_function)
        state = killswitch.runner_state(svc.lambda_client, svc.settings.runner_function)
    except Exception as e:
        log.error(json.dumps({"route": "owner/runner", "action": action, "error": type(e).__name__}))
        raise Refused(502, "AWS didn't answer; try again in a minute.", "runner_failed") from None
    if changed:
        log.info(json.dumps({"route": "owner/runner", "action": action, "triggers": len(changed)}))
    return {"ok": True, "runner": state, "changed": len(changed)}


def owner_timings(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ready -> Done over the uploads started in the last N x 24 hours: counts,
    median, p95, slowest (seconds). A rolling window, so "1 day" means the
    last 24 hours wherever the Owner is, not the current UTC date.
    """
    days = body.get("days", 7)
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_TIMING_DAYS:
        raise Refused(400, f"Days must be 1 to {MAX_TIMING_DAYS}.", "days_range", {"max": MAX_TIMING_DAYS})
    now = svc.now()
    since = f"{now - _dt.timedelta(days=days):%Y%m%dT%H%M%SZ}"   # uploadIds start with their UTC time
    items = [i for back in range(days + 1)
             for i in svc.status.recent((now.date() - _dt.timedelta(days=back)).isoformat())
             if str(i.get("uploadId") or "") >= since]
    return {"ok": True, "days": days, "targetSeconds": timing.TARGET_S, **timing.summary(items)}


# ----- the Owner tab's settings (Story 4.5 part B) ------------------------------------------

def _forget_settings() -> None:
    """After a write, this container reads the parameters again on its next request."""
    global _services
    settings_mod.reset_cache()
    _services = None


def _put(svc: Services, name: str, value: str) -> None:
    if name not in owner_settings.WRITABLE:   # the policy refuses the rest too; this says so first
        raise RuntimeError(f"{name} is not a setting the Owner tab may write")
    try:
        svc.ssm.put_parameter(Name=settings_mod.PARAMETER_PREFIX + name, Value=value,
                              Type="SecureString", Overwrite=True)
    except Exception as e:
        log.warning(json.dumps({"settingWrite": "failed", "name": name, "error": type(e).__name__}))
        raise Refused(502, "AWS didn't save it. Try again in a minute.", "save_failed") from None
    _forget_settings()


def _invalid(e: owner_settings.Invalid) -> Refused:
    return Refused(400, str(e), e.code, e.args_)


def _alerts(svc: Services) -> Optional[List[Dict[str, Any]]]:
    """The topic's email subscriptions: address, confirmed, and the id to remove a confirmed one."""
    topic = svc.settings.alerts_topic_arn
    if not topic or svc.sns is None:
        return None
    found: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"TopicArn": topic}
    while True:
        page = svc.sns.list_subscriptions_by_topic(**kwargs)
        for sub in page.get("Subscriptions", []):
            if sub.get("Protocol") not in ("email", "email-json"):
                continue
            arn = str(sub.get("SubscriptionArn") or "")
            confirmed = arn.startswith(topic + ":")   # else "PendingConfirmation"
            found.append({"address": sub.get("Endpoint") or "", "confirmed": confirmed,
                          "id": arn if confirmed else None})
        if not page.get("NextToken"):
            return found
        kwargs["NextToken"] = page["NextToken"]


def _alerts_or_none(svc: Services) -> Optional[List[Dict[str, Any]]]:
    try:
        return _alerts(svc)
    except Exception as e:   # shown as "unknown"; the rest of the card still works
        log.warning(json.dumps({"alerts": "unreadable", "error": type(e).__name__}))
        return None


def owner_settings_read(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    current = settings_mod.read(svc.ssm)   # fresh: the other container may have just changed it
    return {"ok": True, "syncEnabled": _sync_switch(svc), "employees": list(current.name_prefixes),
            "alerts": _alerts_or_none(svc)}


def owner_sync(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    on = body.get("on")
    if not isinstance(on, bool):
        raise Refused(400, "Say on or off.", "bad_switch")
    _put(svc, "sync_enabled", "true" if on else "false")
    return {"ok": True, "syncEnabled": _sync_switch(svc)}


def _password_problem(new: Any) -> Optional[Refused]:
    if not isinstance(new, str) or len(new) < passphrase_mod.MIN_LENGTH:
        return Refused(400, f"Use at least {passphrase_mod.MIN_LENGTH} characters.", "password_short",
                       {"min": passphrase_mod.MIN_LENGTH})
    if new != new.strip():
        return Refused(400, "No spaces at the start or the end.", "password_spaces")
    if not new.isascii():
        return Refused(400, "Use only unaccented letters, digits and symbols.", "password_accents")
    if passphrase_mod.weak(new):   # any rule added there later
        return Refused(400, "That password isn't allowed.", "password_weak")
    return None


def owner_password(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    which, new = body.get("which"), body.get("new")
    if which not in ("upload", "owner"):
        raise Refused(400, "Say which password: upload or owner.", "bad_which")
    problem = _password_problem(new)
    if problem:
        raise problem
    current = settings_mod.read(svc.ssm)
    other = current.owner_passphrase_hash if which == "upload" else current.intake_passphrase_hash
    if other and passphrase_mod.verify(new, other):
        raise Refused(400, "Keep the upload passphrase and the owner password different.", "same_as_other")
    _put(svc, "intake_passphrase_hash" if which == "upload" else "owner_passphrase_hash",
         passphrase_mod.hash_passphrase(new))
    return {"ok": True, "which": which}


def owner_alerts(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    topic = svc.settings.alerts_topic_arn
    if not topic or svc.sns is None:
        raise Refused(503, "There is no alert topic to manage.", "no_topic")
    action = body.get("action")
    try:
        if action == "add":
            try:
                address = owner_settings.email(body.get("address"))
            except owner_settings.Invalid as e:
                raise _invalid(e) from None
            svc.sns.subscribe(TopicArn=topic, Protocol="email", Endpoint=address)
        elif action == "remove":
            confirmed = [a for a in _alerts(svc) or [] if a["confirmed"]]
            sub = body.get("id")
            if not any(a["id"] == sub for a in confirmed):
                raise Refused(404, "Only a confirmed address can be removed; an unconfirmed one expires by itself.",
                              "not_confirmed")
            if len(confirmed) == 1:
                raise Refused(409, "Add another address first: this one is the only one that gets the alerts.",
                              "last_alert")
            svc.sns.unsubscribe(SubscriptionArn=sub)
        else:
            raise Refused(400, "Say add or remove.", "bad_action_alerts")
    except Refused:
        raise
    except Exception as e:
        log.warning(json.dumps({"alerts": "failed", "action": action if action in ("add", "remove") else "other",
                                "error": type(e).__name__}))
        raise Refused(502, "AWS didn't change the alert addresses. Try again in a minute.", "alerts_failed") from None
    return {"ok": True, "alerts": _alerts_or_none(svc)}


def owner_employees(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        names = owner_settings.employees(body.get("names"))
    except owner_settings.Invalid as e:
        raise _invalid(e) from None
    _put(svc, "name_prefixes", json.dumps(names, ensure_ascii=False))
    return {"ok": True, "employees": names}


OWNER_ROUTES: Dict[str, Callable[[Services, Dict[str, Any]], Dict[str, Any]]] = {
    "/api/owner/hello": owner_hello, "/api/owner/requeue": owner_requeue, "/api/owner/resend": owner_resend,
    "/api/owner/runner": owner_runner, "/api/owner/timings": owner_timings,
    "/api/owner/settings": owner_settings_read, "/api/owner/sync": owner_sync,
    "/api/owner/password": owner_password, "/api/owner/alerts": owner_alerts,
    "/api/owner/employees": owner_employees,
}


# ----- HTTP -----------------------------------------------------------------------------

def _page() -> Tuple[str, str]:
    """The page and its Content-Security-Policy, which allows its own inline blocks by hash."""
    html = PAGE.read_text(encoding="utf-8")

    def hashes(tag: str) -> str:
        blocks = re.findall(rf"<{tag}>(.*?)</{tag}>", html, re.S)
        return " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(b.encode('utf-8')).digest()).decode()}'"
                        for b in blocks)

    bucket = os.environ.get(settings_mod.ENVIRONMENT["bucket"], "")
    csp = ("default-src 'none'; "
           f"script-src {hashes('script')}; style-src {hashes('style')}; "
           f"connect-src 'self' https://{bucket}.s3.{_region()}.amazonaws.com; "
           "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return html, csp


def _respond(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status_code,
            "headers": {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff"},
            "body": json.dumps(body, ensure_ascii=False)}


def _body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise Refused(400, "The request isn't valid JSON.", "bad_json") from None
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise Refused(413, "The request is too large; choose fewer folders at once.", "too_large")
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise Refused(400, "The request isn't valid JSON.", "bad_json") from None
    if not isinstance(body, dict):
        raise Refused(400, "The request isn't valid JSON.", "bad_json")
    return body


def _gate(svc: Services, owner: bool, headers: Dict[str, str]) -> Optional[Refused]:
    """Why this request may not go on, or None."""
    if owner:   # the owner password only: the upload passphrase never opens these
        if not svc.settings.owner_passphrase_hash:
            return Refused(503, "The Owner tab isn't set up yet: the Owner sets a password with "
                                "tools/put_parameters.py --owner-passphrase.", "owner_not_set")
        if not authorised(headers.get(OWNER_HEADER), svc.settings.owner_passphrase_hash):
            return Refused(403, "Wrong owner password.", "wrong_owner")
        return None
    if not svc.settings.intake_passphrase_hash:
        return Refused(503, "The page isn't set up yet: the Owner sets a passphrase with "
                            "tools/put_parameters.py --passphrase.", "not_set_up")
    if not authorised(headers.get(HEADER), svc.settings.intake_passphrase_hash):
        return Refused(401, "Wrong passphrase.", "wrong_passphrase")
    return None


def _reload(clock: Callable[[], float] = time.monotonic) -> Optional[Services]:
    """
    Fresh settings for a refused password, when this container's are older than
    RECHECK_SECONDS: a password changed in the other container (or in CloudShell)
    then works here at once. A guesser costs one parameter read per period at most.
    """
    global _services
    if _services is None or clock() - _loaded_at < RECHECK_SECONDS:
        return None
    _services = None
    return services(clock)


def handle(event: Dict[str, Any], get_services: Callable[[], Services] = services,
           reload: Optional[Callable[[], Optional[Services]]] = None) -> Dict[str, Any]:
    http = (event.get("requestContext") or {}).get("http") or {}
    method, path = http.get("method", "GET"), event.get("rawPath") or "/"
    if path == "/" and method in ("GET", "HEAD"):
        html, csp = _page()
        return {"statusCode": 200, "body": html if method == "GET" else "",
                "headers": {"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": csp,
                            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}}
    route, owner = ROUTES.get(path), OWNER_ROUTES.get(path)
    if (route is None and owner is None) or method != "POST":
        return _respond(404, {"ok": False, "error": "No such page.", "code": "not_found", "args": {}})

    started = time.monotonic()
    result, upload_id = "ok", None
    try:
        svc = get_services()
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        denied = _gate(svc, owner is not None, headers)
        if reload is None and get_services is services:
            reload = _reload
        if denied is not None and reload is not None:
            fresh = reload()
            if fresh is not None:
                svc, denied = fresh, _gate(fresh, owner is not None, headers)
        if denied is not None:
            raise denied
        route = owner or route
        body = _body(event)
        response = _respond(200, route(svc, body))
        upload_id = json.loads(response["body"]).get("uploadId") or body.get("uploadId")
        return response
    except Refused as e:
        result = str(e.status)
        return _respond(e.status, {"ok": False, "error": e.error,
                                   **({"code": e.code, "args": e.args_} if e.code else {})})
    except Exception as e:
        result = type(e).__name__
        return _respond(500, {"ok": False, "error": "Something went wrong on the server. Try again in a minute.",
                              "code": "server_error", "args": {}})
    finally:
        log.info(json.dumps({"route": path, "result": result, "durationMs": int((time.monotonic() - started) * 1000),
                             **({"uploadId": upload_id} if isinstance(upload_id, str)
                                and naming.is_upload_id(upload_id) else {})}))


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logging.getLogger().setLevel(logging.INFO)
    return handle(event)
