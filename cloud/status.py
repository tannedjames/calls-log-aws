"""
Folder Status (AD-4, AD-5, AD-6): the only record of where each upload is in
its lifecycle, a lease that lets one Run at a time work on a Person-Day, and
the attempt count that parks a Folder after 3 Runs.

Table calls-log-folders, keys as strings:
    pk = "pd#<Person-Day>", sk = "up#<uploadId>"   one item per upload
    pk = "pd#<Person-Day>", sk = "lease"           at most one Run per Person-Day
Index uploads-by-day: upDay (the uploadId's UTC date) and sk, on upload items
only, so the status page reads one day's uploads without a scan (Epic 4).

Only this module writes the table, and every change is a conditional write
from the expected prior state: a write whose condition fails changes nothing
and raises TransitionRefused (or returns False for the lease).

    create        -> Uploading
    record_import -> Done, imported (Story 5.1: a PC Day Sheet brought over; no images)
    mark_ready    Uploading -> Pending (readyAt: the start of the turnaround)
    begin_run     take the lease, then Pending | Failed | Processing -> Processing
                  (attempts + 1), or -> Parked when 3 Runs have already started
    finish        Processing -> Done | Failed
    requeue       Done | Failed | Parked -> Pending, attempts back to 0, readyAt again

Processing -> Processing is a redelivered message after a Run that timed out
and so never wrote Failed. It needs the lease, which outlives the 900 s Lambda
timeout (1,000 s) but expires before SQS redelivers (1,200 s).

Uses the low-level DynamoDB client (boto3.client("dynamodb")), passed in, so
this module imports no AWS library and tests can fake the table.
"""

from __future__ import annotations

import datetime as _dt
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional

INDEX = "uploads-by-day"
MAX_RUNS = 3            # AD-6: the 4th start parks the Folder
LEASE_SECONDS = 1000    # AD-5: longer than the 900 s Lambda timeout
_ERROR_LIMIT = 500      # characters of lastError kept


class Status(str, Enum):
    UPLOADING = "Uploading"
    PENDING = "Pending"
    PROCESSING = "Processing"
    DONE = "Done"
    FAILED = "Failed"
    PARKED = "Parked"


class TransitionRefused(Exception):
    """The upload wasn't in a state this change starts from (or doesn't exist); nothing changed."""


class Busy(Exception):
    """Another Run holds this Person-Day's lease; nothing changed, not an attempt."""


_NAMES = {"#s": "status", "#a": "attempts", "#u": "updatedAt", "#r": "lastRunAt",
          "#e": "lastError", "#h": "holder", "#x": "expiresAt", "#y": "readyAt"}


def _s(value: str) -> Dict[str, str]:
    return {"S": value}


def _n(value: int) -> Dict[str, str]:
    return {"N": str(int(value))}


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def up_day(upload_id: str) -> str:
    """The UTC date an upload started, from its uploadId (<yyyymmdd>T<hhmmss>Z-<hex>)."""
    return f"{upload_id[0:4]}-{upload_id[4:6]}-{upload_id[6:8]}"


def _refused(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _plain(item: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    return {k: int(v["N"]) if "N" in v else v.get("S") for k, v in item.items()}


class FolderStatus:
    def __init__(self, client: Any, table: str, clock: Callable[[], float] = time.time):
        self.client, self.table, self.clock = client, table, clock

    # ----- keys -------------------------------------------------------------

    @staticmethod
    def _key(person_day: str, upload_id: str) -> Dict[str, Dict[str, str]]:
        return {"pk": _s(f"pd#{person_day}"), "sk": _s(f"up#{upload_id}")}

    @staticmethod
    def _lease_key(person_day: str) -> Dict[str, Dict[str, str]]:
        return {"pk": _s(f"pd#{person_day}"), "sk": _s("lease")}

    # ----- reads ------------------------------------------------------------

    def get(self, person_day: str, upload_id: str) -> Optional[Dict[str, Any]]:
        out = self.client.get_item(TableName=self.table, Key=self._key(person_day, upload_id),
                                   ConsistentRead=True)
        return _plain(out["Item"]) if "Item" in out else None

    def uploads(self, person_day: str) -> list:
        """Every upload of a Person-Day (not the lease), oldest first: uploadIds start with UTC time."""
        kwargs: Dict[str, Any] = {
            "TableName": self.table, "ConsistentRead": True,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :up)",
            "ExpressionAttributeValues": {":pk": _s(f"pd#{person_day}"), ":up": _s("up#")}}
        items = []
        while True:
            out = self.client.query(**kwargs)
            items += [_plain(i) for i in out.get("Items", [])]
            if not out.get("LastEvaluatedKey"):
                return sorted(items, key=lambda i: i.get("uploadId") or "")
            kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]

    def recent(self, day: str) -> list:
        """Every upload started on one UTC date (yyyy-mm-dd), oldest first; the index is read eventually consistent."""
        kwargs: Dict[str, Any] = {
            "TableName": self.table, "IndexName": INDEX,
            "KeyConditionExpression": "upDay = :d",
            "ExpressionAttributeValues": {":d": _s(day)}}
        items = []
        while True:
            out = self.client.query(**kwargs)
            items += [_plain(i) for i in out.get("Items", [])]
            if not out.get("LastEvaluatedKey"):
                return sorted(items, key=lambda i: i.get("uploadId") or "")
            kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]

    # ----- transitions ------------------------------------------------------

    def _update(self, person_day: str, upload_id: str, update: str, condition: str,
                values: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
        names = {k: v for k, v in _NAMES.items() if k in update or k in condition}
        try:
            out = self.client.update_item(
                TableName=self.table, Key=self._key(person_day, upload_id),
                UpdateExpression=update, ConditionExpression=condition,
                ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW")
        except Exception as e:
            if _refused(e):
                raise TransitionRefused(
                    f"{person_day} / {upload_id}: not in a state this change starts from") from None
            raise
        return _plain(out["Attributes"])

    def create(self, person_day: str, upload_id: str, image_count: int) -> None:
        """A new upload starts Uploading (the intake has just issued its upload links)."""
        now = self.clock()
        item = {**self._key(person_day, upload_id), "status": _s(Status.UPLOADING.value),
                "personDay": _s(person_day), "uploadId": _s(upload_id), "upDay": _s(up_day(upload_id)),
                "imageCount": _n(image_count), "attempts": _n(0),
                "createdAt": _s(_iso(now)), "updatedAt": _s(_iso(now))}
        try:
            self.client.put_item(TableName=self.table, Item=item,
                                 ConditionExpression="attribute_not_exists(pk)")
        except Exception as e:
            if _refused(e):
                raise TransitionRefused(f"{person_day} / {upload_id} already exists") from None
            raise

    def record_import(self, person_day: str, upload_id: str) -> None:
        """
        A Day Sheet imported from the PC's output/ (Story 5.1): Done from the
        start, no images, and no upDay, so the upload page's 7-day view doesn't
        fill with history.
        """
        now = _iso(self.clock())
        item = {**self._key(person_day, upload_id), "status": _s(Status.DONE.value),
                "personDay": _s(person_day), "uploadId": _s(upload_id), "imported": _s("true"),
                "imageCount": _n(0), "attempts": _n(0), "createdAt": _s(now), "updatedAt": _s(now)}
        try:
            self.client.put_item(TableName=self.table, Item=item,
                                 ConditionExpression="attribute_not_exists(pk)")
        except Exception as e:
            if _refused(e):
                raise TransitionRefused(f"{person_day} / {upload_id} already exists") from None
            raise

    def mark_ready(self, person_day: str, upload_id: str) -> Dict[str, Any]:
        """Uploading -> Pending, once the intake has counted every image."""
        return self._update(person_day, upload_id, "SET #s = :to, #u = :now, #y = :now", "#s IN (:from0)",
                            {":to": _s(Status.PENDING.value), ":from0": _s(Status.UPLOADING.value),
                             ":now": _s(_iso(self.clock()))})

    def _start(self, person_day: str, upload_id: str) -> Dict[str, Any]:
        """Counts the attempt before any work (AD-6): Processing, or Parked at the 4th start."""
        startable = {":from0": _s(Status.PENDING.value), ":from1": _s(Status.FAILED.value),
                     ":from2": _s(Status.PROCESSING.value), ":one": _n(1), ":max": _n(MAX_RUNS),
                     ":now": _s(_iso(self.clock()))}
        try:
            return self._update(
                person_day, upload_id, "SET #s = :to, #a = #a + :one, #u = :now, #r = :now",
                "#s IN (:from0, :from1, :from2) AND #a < :max",
                {**startable, ":to": _s(Status.PROCESSING.value)})
        except TransitionRefused:
            return self._update(
                person_day, upload_id, "SET #s = :to, #a = #a + :one, #u = :now",
                "#s IN (:from0, :from1, :from2) AND #a >= :max",
                {**startable, ":to": _s(Status.PARKED.value)})

    def begin_run(self, person_day: str, upload_id: str, holder: str) -> Dict[str, Any]:
        """
        Take the Person-Day's lease, then count the attempt (AD-5, AD-6). Returns
        the item: status Processing (work on it) or Parked (alert, stop). Raises
        Busy when another Run holds the lease (nothing changed), TransitionRefused
        when the upload isn't startable. Either way the lease is not kept.
        """
        if not self.take_lease(person_day, holder):
            raise Busy(f"{person_day}: another Run holds the lease")
        try:
            item = self._start(person_day, upload_id)
        except BaseException:
            self.release_lease(person_day, holder)
            raise
        if item["status"] == Status.PARKED.value:
            self.release_lease(person_day, holder)
        return item

    def finish(self, person_day: str, upload_id: str, error: Optional[str] = None) -> Dict[str, Any]:
        """Processing -> Done (error None) or Failed (with the error, shortened)."""
        values = {":from0": _s(Status.PROCESSING.value), ":now": _s(_iso(self.clock()))}
        if error is None:
            return self._update(person_day, upload_id, "SET #s = :to, #u = :now REMOVE #e",
                                "#s IN (:from0)", {**values, ":to": _s(Status.DONE.value)})
        return self._update(person_day, upload_id, "SET #s = :to, #u = :now, #e = :err",
                            "#s IN (:from0)", {**values, ":to": _s(Status.FAILED.value),
                                               ":err": _s(error[:_ERROR_LIMIT] or "failed")})

    def requeue(self, person_day: str, upload_id: str) -> Dict[str, Any]:
        """Done | Failed | Parked -> Pending with the attempt count reset (the Owner's re-queue)."""
        return self._update(
            person_day, upload_id, "SET #s = :to, #a = :zero, #u = :now, #y = :now REMOVE #e",
            "#s IN (:from0, :from1, :from2)",
            {":to": _s(Status.PENDING.value), ":zero": _n(0), ":now": _s(_iso(self.clock())),
             ":from0": _s(Status.DONE.value), ":from1": _s(Status.FAILED.value),
             ":from2": _s(Status.PARKED.value)})

    # ----- the Person-Day lease (AD-5) --------------------------------------

    def take_lease(self, person_day: str, holder: str) -> bool:
        now = self.clock()
        try:
            self.client.update_item(
                TableName=self.table, Key=self._lease_key(person_day),
                UpdateExpression="SET #h = :me, #x = :until, #u = :now",
                ConditionExpression="attribute_not_exists(pk) OR #x < :epoch OR #h = :me",
                ExpressionAttributeNames={k: _NAMES[k] for k in ("#h", "#x", "#u")},
                ExpressionAttributeValues={":me": _s(holder), ":until": _n(now + LEASE_SECONDS),
                                           ":epoch": _n(now), ":now": _s(_iso(now))})
        except Exception as e:
            if _refused(e):
                return False
            raise
        return True

    def release_lease(self, person_day: str, holder: str) -> None:
        """Give the lease back if this Run still holds it; otherwise do nothing."""
        try:
            self.client.delete_item(TableName=self.table, Key=self._lease_key(person_day),
                                    ConditionExpression="#h = :me",
                                    ExpressionAttributeNames={"#h": "holder"},
                                    ExpressionAttributeValues={":me": _s(holder)})
        except Exception as e:
            if not _refused(e):
                raise
