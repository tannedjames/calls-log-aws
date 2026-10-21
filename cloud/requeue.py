"""
Re-queueing a stored upload (Story 3.8): the one rule that tools/enqueue.py
--requeue, the upload page's Owner tab and its Re-run of a Held upload share.
Only a Done, Failed, Parked, Held or Superseded upload runs again, and only
while all of its images are still in inbox/ (kept 30 days). It becomes Pending
with its attempts back at 0 and so the day's choice (cloud/status.py requeue),
then one "process" message is sent.

The caller passes how to list an upload's images and how to send a message,
so the PC (over the AWS CLI) and the page (boto3) run the same code.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from cloud import naming
from cloud.status import FolderStatus, Status, TransitionRefused

REQUEUEABLE = (Status.DONE.value, Status.FAILED.value, Status.PARKED.value,
               Status.HELD.value, Status.SUPERSEDED.value)


class Refused(Exception):
    """Nothing was re-queued: the reason in plain words, with a code the page translates."""

    def __init__(self, message: str, code: str, **args: Any):
        super().__init__(message)
        self.code, self.args_ = code, args


def requeue(day: naming.PersonDay, upload_id: Optional[str], *, status: FolderStatus,
            list_images: Callable[[str], List[str]],
            send: Callable[[Dict[str, str]], None],
            allowed: Tuple[str, ...] = REQUEUEABLE) -> Tuple[str, int]:
    """
    Re-queue the newest stored upload of this Person-Day (or the one named);
    returns (uploadId, images). allowed narrows the states it starts from (the
    upload passphrase may only re-run a Held upload).
    """
    uploads = status.uploads(day.key)
    found = [u for u in uploads if u.get("uploadId") == upload_id] if upload_id else uploads[-1:]
    if not found:
        raise Refused(f"{day.key}: no stored upload" + (f" {upload_id}" if upload_id else ""), "no_upload")
    item = found[0]
    upload_id = item["uploadId"]
    if item.get("status") not in allowed:
        states = f"{', '.join(allowed[:-1])} or {allowed[-1]}" if len(allowed) > 1 else allowed[0]
        raise Refused(f"{day.key}: upload {upload_id} is {item.get('status')}; "
                      f"only a {states} Folder can be re-queued",
                      "not_requeueable", status=item.get("status"), uploadId=upload_id)
    images = list_images(upload_id)
    declared = int(item.get("imageCount") or 0)
    if len(images) < declared:
        raise Refused(f"{day.key}: {len(images)} of its {declared} images are still in inbox/ "
                      "(kept 30 days); send the folder again instead",
                      "images_gone", arrived=len(images), declared=declared)
    try:
        status.requeue(day.key, upload_id)
    except TransitionRefused:
        raise Refused(f"{day.key}: upload {upload_id} changed state meanwhile; nothing re-queued",
                      "changed") from None
    send({"uploadId": upload_id, "personDay": day.key, "mode": "process"})
    return upload_id, len(images)
