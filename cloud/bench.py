"""
Story 3.1's benchmark (infra/bench.tf, while enable_bench is true). Each
benchmark function, one per memory size, runs the images under one bench/
prefix through the runner's own per-image extraction (cloud.runner.extract)
and session rules, and returns how long each part took. Lambda's REPORT line
adds the billed duration and the cold start; tools/bench_lambda.py invokes
the functions from the PC and combines both into the report.

    event   {"prefix": "bench/<set>/"}
    result  timings and counts only: no file name, OCR text or call

Nothing is written to the bucket. A Run that gets close to the 900 s limit
stops early and says so ("cutOff"), which is the answer to "does a 50-image
Folder fit in one Run?".
"""

import runtime_env  # noqa: F401  -- first: sets OpenMP variables before numpy/cv2/paddle

import shutil
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from cloud import runner, settings as settings_mod
from cloud.storage import IMAGE_SUFFIXES, pc_order

WORKDIR = Path("/tmp/calls-log-bench")
STOP_MARGIN_MS = 60_000   # stop before an image could run into the 900 s limit

_cold = True   # the first invocation in a container; REPORT's Init Duration says how long the start took


def list_images(s3: Any, bucket: str, prefix: str) -> List[str]:
    """File names of the images directly under prefix, in the PC's order."""
    names: List[str] = []
    kwargs: Dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    while True:
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            name = obj["Key"][len(prefix):]
            if "/" not in name and name.lower().endswith(IMAGE_SUFFIXES):
                names.append(name)
        if not page.get("IsTruncated"):
            return pc_order(names)
        kwargs["ContinuationToken"] = page["NextContinuationToken"]


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def measure(s3: Any, bucket: str, prefix: str, remaining_ms: Callable[[], int], *,
            memory_mb: int, cold: bool, workdir: Path = WORKDIR) -> Dict[str, Any]:
    started = time.monotonic()
    names = list_images(s3, bucket, prefix)
    if not names:
        raise ValueError(f"no images under {prefix}")
    folder = workdir / "images"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    try:
        for name in names:
            s3.download_file(bucket, prefix + name, str(folder / name))
        download_ms = _ms(started)

        per_image: List[int] = []
        records: list = []
        cut_off = False
        for name in names:
            if remaining_ms() < STOP_MARGIN_MS:
                cut_off = True
                break
            t = time.monotonic()
            got, _ocr = runner.extract([folder / name])
            per_image.append(_ms(t))
            records.extend(got)
        sessions, _stats = runner.sessions_from(records)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    # The first image also loads the OCR models; the rest show the steady rate.
    steady = per_image[1:] or per_image
    return {
        "memoryMb": memory_mb,
        "cold": cold,
        "images": len(names),
        "processed": len(per_image),
        "cutOff": cut_off,
        "downloadMs": download_ms,
        "firstImageMs": per_image[0] if per_image else None,
        "medianImageMs": int(statistics.median(steady)) if steady else None,
        "maxImageMs": max(steady) if steady else None,
        "ocrMs": sum(per_image),
        "handlerMs": _ms(started),
        "records": len(records),
        "sessions": len(sessions),
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    global _cold
    cold, _cold = _cold, False
    prefix = str((event or {}).get("prefix") or "")
    if not (prefix.startswith("bench/") and prefix.endswith("/")):
        raise ValueError('event must be {"prefix": "bench/<set>/"}')
    import boto3   # in the runner image; not needed on the PC

    return measure(boto3.client("s3"), settings_mod.from_environment().bucket, prefix,
                   context.get_remaining_time_in_millis,
                   memory_mb=int(context.memory_limit_in_mb), cold=cold)
