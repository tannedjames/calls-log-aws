"""
The data lake's publisher (ADR 9, Phase 2; docs/aws/data-contract.md).

S3 calls it for each envelope the runner writes to
staging/<Person-Day>/<uploadId>.json. For each one:
  1. the upload must be Done and still the day's choice (cloud/status.py);
     otherwise the envelope is dropped;
  2. lake.check() must find nothing wrong with it; otherwise it goes to
     quarantine/<Person-Day>/<uploadId>.json with its problems, and an alarm
     emails the Owner;
  3. its three tables become Parquet at fixed keys,
     lake/<table>/work_month=<yyyy-mm>/<person_key>_<yyyy-mm-dd>.parquet,
     so a Re-run or a replay overwrites them.

One JSON line per envelope, in CloudWatch's Embedded Metric Format, so the
outcome counts as a metric with no extra permission. Never a name, a row or a
secret in it. The Person-Day in the key only finds the upload in Folder
Status; it never enters the lake.

A zip function with the AWS SDK for pandas layer, which brings pyarrow. Only
parquet_bytes() imports it, so tests and the runner image need none. The zip
holds this module, lake, naming, status, config and runtime_env (infra/lake.tf).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote_plus

from cloud import lake, naming, settings as settings_mod
from cloud.status import FolderStatus, Status

NAMESPACE = "calls-log/lake"
FUNCTION = "calls-log-publisher"   # the metrics' one dimension; the alarms name it (infra/lake.tf)
_STAGED = re.compile(r"staging/(?P<day>[^/]+ & \d{4}-\d{2}-\d{2})/(?P<upload>[^/]+)\.json")
_PARQUET = "application/vnd.apache.parquet"

Writer = Callable[[str, List[Dict[str, Any]]], bytes]


@dataclass
class Outcome:
    status: str                  # published | dropped | quarantined
    reason: Optional[str] = None
    problems: Tuple[str, ...] = ()
    keys: Tuple[str, ...] = ()


def lake_key(table: str, envelope: Dict[str, Any]) -> str:
    """The one key a person's day has in a table: a Re-run overwrites it."""
    date = envelope["work_date"]
    return f"lake/{table}/work_month={date[:7]}/{envelope['person_key']}_{date}.parquet"


def quarantine_key(day_key: str, upload_id: str) -> str:
    return f"quarantine/{day_key}/{upload_id}.json"


# ---------------------------------------------------------------------------
# Rows and Parquet
# ---------------------------------------------------------------------------

def _value(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "date":
        return _dt.date.fromisoformat(value)
    if kind == "timestamp":   # local wall-clock times stay naive; UTC ones lose their Z
        moment = _dt.datetime.fromisoformat(value)
        if moment.tzinfo is not None:
            moment = moment.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return moment
    if kind == "double":
        return float(value)
    if kind == "int":
        return int(value)
    if kind == "boolean":
        return bool(value)
    return str(value)


def tables(envelope: Dict[str, Any], ingested_at: _dt.datetime) -> Dict[str, List[Dict[str, Any]]]:
    """The envelope's rows per table, typed, with exactly lake.COLUMN_TYPES' columns."""
    out = {}
    for table, types in lake.COLUMN_TYPES.items():
        out[table] = [{column: ingested_at if column == "ingested_at" else _value(kind, row[column])
                       for column, kind in types.items()} for row in envelope[table]]
    return out


def parquet_bytes(table: str, rows: List[Dict[str, Any]]) -> bytes:
    """One table's rows as a Parquet file with the Glue table's schema."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    kinds = {"string": pa.string(), "date": pa.date32(), "int": pa.int32(), "double": pa.float64(),
             "boolean": pa.bool_(), "timestamp": pa.timestamp("ms")}
    schema = pa.schema([(column, kinds[kind]) for column, kind in lake.COLUMN_TYPES[table].items()])
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), sink, compression="snappy")
    return sink.getvalue().to_pybytes()


# ---------------------------------------------------------------------------
# One staged envelope
# ---------------------------------------------------------------------------

def _emit(outcome: Outcome, upload_id: Optional[str]) -> None:
    """One EMF line (stdout, unprefixed, so CloudWatch reads it as metrics)."""
    counts = {name: int(outcome.status == status) for name, status in
              (("Published", "published"), ("Dropped", "dropped"), ("Quarantined", "quarantined"))}
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
            "Namespace": NAMESPACE, "Dimensions": [["Function"]],
            "Metrics": [{"Name": name, "Unit": "Count"} for name in counts]}]},
        "Function": FUNCTION, **counts, "uploadId": upload_id, "outcome": outcome.status, "reason": outcome.reason,
        "problems": list(outcome.problems[:20])}), flush=True)


def publish(key: str, *, s3: Any, bucket: str, status: FolderStatus, write: Optional[Writer] = None,
            now: Optional[Callable[[], _dt.datetime]] = None) -> Outcome:
    """Publish, drop or quarantine one staged envelope. AWS errors raise, so S3 retries the event."""
    m = _STAGED.fullmatch(key)
    upload_id = m["upload"] if m else None
    if not m or not naming.is_upload_id(upload_id):
        outcome = Outcome("dropped", "not a staged envelope's key")
        _emit(outcome, None)
        return outcome
    day_key = m["day"]

    item = status.get(day_key, upload_id)
    if item is None or item.get("status") != Status.DONE.value:
        outcome = Outcome("dropped", "the upload is not Done")
    elif status.chosen_later(day_key, upload_id) is not None:
        outcome = Outcome("dropped", "a later upload of the day was chosen")
    else:
        outcome = None
    if outcome is not None:
        _emit(outcome, upload_id)
        return outcome

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        envelope = None
    problems = lake.check(envelope) if isinstance(envelope, dict) else ["the envelope is not a JSON object"]
    if not problems:
        if envelope["upload_id"] != upload_id or envelope["work_date"] != day_key[-10:]:
            problems = ["upload_id or work_date differs from the staged key"]

    if problems:
        target = quarantine_key(day_key, upload_id)
        s3.put_object(Bucket=bucket, Key=target, ContentType="application/json",
                      Body=json.dumps({"problems": problems, "envelope": envelope},
                                      ensure_ascii=False).encode("utf-8"))
        outcome = Outcome("quarantined", "the quality checks failed", tuple(problems), (target,))
        _emit(outcome, upload_id)
        return outcome

    ingested_at = (now or (lambda: _dt.datetime.now(_dt.timezone.utc)))()
    if ingested_at.tzinfo is not None:
        ingested_at = ingested_at.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    ingested_at = ingested_at.replace(microsecond=ingested_at.microsecond // 1000 * 1000)
    written = []
    for table, rows in tables(envelope, ingested_at).items():
        target = lake_key(table, envelope)
        s3.put_object(Bucket=bucket, Key=target, Body=(write or parquet_bytes)(table, rows),
                      ContentType=_PARQUET)
        written.append(target)
    outcome = Outcome("published", keys=tuple(written))
    _emit(outcome, upload_id)
    return outcome


# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

_aws: Dict[str, Any] = {}


def _clients() -> Dict[str, Any]:
    if not _aws:
        import boto3

        current = settings_mod.from_environment()
        _aws.update(bucket=current.bucket, s3=boto3.client("s3"),
                    status=FolderStatus(boto3.client("dynamodb"), current.table))
    return _aws


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """S3 ObjectCreated on staging/: each envelope in turn. An AWS error raises, and S3 retries."""
    aws = _clients()
    outcomes = []
    for record in event.get("Records", []):
        if record.get("s3", {}).get("bucket", {}).get("name") != aws["bucket"]:
            continue
        key = unquote_plus(record["s3"]["object"]["key"])
        outcomes.append(publish(key, s3=aws["s3"], bucket=aws["bucket"], status=aws["status"]).status)
    return {"outcomes": outcomes}
