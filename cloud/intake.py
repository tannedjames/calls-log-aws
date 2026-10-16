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

Errors are {"ok": false, "error": "<plain words>"}, as the Web App answers.
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
import os
import re
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cloud import naming, passphrase as passphrase_mod, settings as settings_mod
from cloud.status import FolderStatus, Status, TransitionRefused
from cloud.storage import IMAGE_SUFFIXES, S3Storage, pc_order

log = logging.getLogger("calls-log.intake")

HEADER = "x-calls-log-passphrase"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_FILES = 500             # entries in one folder's listing, images or not
MAX_FOLDERS = 100           # folders in one check
MAX_BODY_BYTES = 1024 * 1024   # 100 folders of 40 file names fit easily
UPLOAD_SECONDS = 15 * 60
DOWNLOAD_SECONDS = 5 * 60
STATUS_DAYS = 8             # UTC days queried: the last 7 local days, whatever the time zone
MAX_SHEETS = 50
SETTINGS_SECONDS = 300      # a changed passphrase or employee list applies within 5 minutes
PAGE = Path(__file__).resolve().parent / "static" / "upload.html"
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".webp": "image/webp", ".bmp": "image/bmp"}
HEIC = (".heic", ".heif")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Refused(Exception):
    """A request the page can't have: the HTTP status and the reason in plain words."""

    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status, self.error = status, error


# ----- checking a folder (FR-2) ------------------------------------------------

def _suffix(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def _files(raw: Any) -> List[Tuple[str, int]]:
    """The listing the page sent, as (file name, size); anything malformed is refused."""
    if not isinstance(raw, list) or len(raw) > MAX_FILES:
        raise Refused(400, f"A folder's file list must be a list of at most {MAX_FILES} files.")
    out = []
    for f in raw:
        name = f.get("name") if isinstance(f, dict) else None
        size = f.get("size") if isinstance(f, dict) else None
        if (not isinstance(name, str) or not name or len(name) > 255 or "/" in name or "\\" in name
                or name in (".", "..") or not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise Refused(400, "The file list isn't valid.")
        out.append((unicodedata.normalize("NFC", name), size))
    return out


def check_folder(folder: str, files: List[Tuple[str, int]], *, prefixes, max_images: int) -> Dict[str, Any]:
    """
    Whether a folder may be uploaded, as the server decides it:
    {"ok": True, "personDay", "images": [names in the PC's order], "ignored": [...]}
    or {"ok": False, "error": "<why>"}.
    """
    day = naming.parse(folder) if isinstance(folder, str) and folder.strip() else None
    if day is None:
        return {"ok": False, "error": 'its name isn\'t "<Name> & <yyyy-mm-dd>" with a real date'}
    if not prefixes:
        return {"ok": False, "error": "the employee list isn't set up yet "
                                      "(the Owner runs tools/put_parameters.py --from-private-files)"}
    if naming.matching_prefix(day.name, prefixes) is None:
        return {"ok": False, "error": f"{day.name} is not a registered employee"}
    images, ignored, heic, seen = [], [], 0, set()
    for name, size in files:
        if name in seen:
            return {"ok": False, "error": f"{name} is listed twice"}
        seen.add(name)
        suffix = _suffix(name)
        if suffix not in IMAGE_SUFFIXES:
            heic += suffix in HEIC
            ignored.append(name)
        elif size == 0:
            return {"ok": False, "error": f"{name} is empty"}
        elif size > MAX_IMAGE_BYTES:
            return {"ok": False, "error": f"{name} is {size / 1024 / 1024:.0f} MB, more than 15 MB"}
        else:
            images.append(name)
    if not images:
        if heic:
            return {"ok": False, "error": "it has only HEIC photos: save the screenshots as JPEG or PNG "
                                          "(or convert them) and choose the folder again"}
        return {"ok": False, "error": "it has no images (.jpg, .jpeg, .png, .webp or .bmp)"}
    if len(images) > max_images:
        return {"ok": False, "error": f"it has {len(images)} images, more than {max_images}"}
    return {"ok": True, "personDay": day.key, "images": pc_order(images), "ignored": sorted(ignored)}


# ----- the passphrase (AD-17) ------------------------------------------------------

_verified: Dict[str, bytes] = {}   # stored hash -> SHA-256 of the passphrase that matched it


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
                             S3Storage(s3, current.bucket), s3, boto3.client("sqs"), ssm, CONFIG.max_images)
        _loaded_at = clock()
    return _services


# ----- the API -------------------------------------------------------------------------

def hello(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "maxImages": svc.max_images, "maxImageMb": MAX_IMAGE_BYTES // (1024 * 1024),
            "syncEnabled": _sync_switch(svc)}


def check(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    folders = body.get("folders")
    if not isinstance(folders, list) or not folders or len(folders) > MAX_FOLDERS:
        raise Refused(400, f"Send between 1 and {MAX_FOLDERS} folders to check.")
    out, keys = [], {}
    for f in folders:
        folder = f.get("folder") if isinstance(f, dict) else None
        result = check_folder(folder, _files(f.get("files") if isinstance(f, dict) else None),
                              prefixes=svc.settings.name_prefixes, max_images=svc.max_images)
        if result["ok"]:
            if result["personDay"] in keys:
                result = {"ok": False, "error": f"it is the same Person-Day as {keys[result['personDay']]}"}
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
        raise Refused(400, f"{folder}: {result['error']}. Nothing was uploaded.")
    upload_id = naming.new_upload_id()
    svc.status.create(result["personDay"], upload_id, len(result["images"]))
    return {"ok": True, "uploadId": upload_id, "personDay": result["personDay"],
            "ignored": result["ignored"], "expiresInSeconds": UPLOAD_SECONDS,
            "forms": [_form(svc, upload_id, name) for name in result["images"]]}


def _queue(svc: Services, person_day: str, upload_id: str) -> None:
    body = {"uploadId": upload_id, "personDay": person_day, "mode": "process"}
    try:
        svc.sqs.send_message(QueueUrl=svc.settings.queue_url, MessageBody=json.dumps(body, ensure_ascii=False))
    except Exception as e:
        log.error(json.dumps({"route": "ready", "uploadId": upload_id, "error": type(e).__name__}))
        raise Refused(502, "It is Ready, but it couldn't be queued. Click Ready again.") from None


def ready(svc: Services, body: Dict[str, Any]) -> Dict[str, Any]:
    person_day, upload_id = body.get("personDay"), body.get("uploadId")
    if not isinstance(person_day, str) or not isinstance(upload_id, str) or not naming.is_upload_id(upload_id):
        raise Refused(400, "That isn't an upload.")
    item = svc.status.get(person_day, upload_id)
    if item is None:
        raise Refused(404, "No such upload.")
    if item["status"] == Status.PENDING.value:   # Ready already, maybe without its message: queue it again
        _queue(svc, person_day, upload_id)
        return {"ok": True, "status": Status.PENDING.value, "queuedAgain": True}
    if item["status"] != Status.UPLOADING.value:
        raise Refused(409, f"This upload is already {item['status']}. To correct the day, "
                           "upload the whole folder again.")
    arrived, declared = len(svc.storage.list_images(upload_id)), int(item["imageCount"])
    if arrived != declared:
        raise Refused(409, f"Only {arrived} of {declared} images arrived, so it can't be made Ready. "
                           "Choose the folder and upload it again; the partial upload is deleted after 30 days.")
    try:
        svc.status.mark_ready(person_day, upload_id)
    except TransitionRefused:
        raise Refused(409, "This upload changed meanwhile; refresh the status.") from None
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
        raise Refused(400, "Give a name, a date or both.")
    date = date.strip()
    if date and not _DATE.fullmatch(date):
        raise Refused(400, "The date must look like 2026-06-01.")
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
            raise Refused(400, "The request isn't valid JSON.") from None
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise Refused(413, "The request is too large; choose fewer folders at once.")
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise Refused(400, "The request isn't valid JSON.") from None
    if not isinstance(body, dict):
        raise Refused(400, "The request isn't valid JSON.")
    return body


def handle(event: Dict[str, Any], get_services: Callable[[], Services] = services) -> Dict[str, Any]:
    http = (event.get("requestContext") or {}).get("http") or {}
    method, path = http.get("method", "GET"), event.get("rawPath") or "/"
    if path == "/" and method in ("GET", "HEAD"):
        html, csp = _page()
        return {"statusCode": 200, "body": html if method == "GET" else "",
                "headers": {"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": csp,
                            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}}
    route = ROUTES.get(path)
    if route is None or method != "POST":
        return _respond(404, {"ok": False, "error": "No such page."})

    started = time.monotonic()
    result, upload_id = "ok", None
    try:
        svc = get_services()
        if not svc.settings.intake_passphrase_hash:
            raise Refused(503, "The page isn't set up yet: the Owner sets a passphrase with "
                               "tools/put_parameters.py --passphrase.")
        given = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get(HEADER)
        if not authorised(given, svc.settings.intake_passphrase_hash):
            raise Refused(401, "Wrong passphrase.")
        body = _body(event)
        response = _respond(200, route(svc, body))
        upload_id = json.loads(response["body"]).get("uploadId") or body.get("uploadId")
        return response
    except Refused as e:
        result = str(e.status)
        return _respond(e.status, {"ok": False, "error": e.error})
    except Exception as e:
        result = type(e).__name__
        return _respond(500, {"ok": False, "error": "Something went wrong on the server. Try again in a minute."})
    finally:
        log.info(json.dumps({"route": path, "result": result, "durationMs": int((time.monotonic() - started) * 1000),
                             **({"uploadId": upload_id} if isinstance(upload_id, str)
                                and naming.is_upload_id(upload_id) else {})}))


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logging.getLogger().setLevel(logging.INFO)
    return handle(event)
