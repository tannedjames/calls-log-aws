"""
Story 3.1: measure what one image costs in Lambda, from the PC after
`aws login`, while enable_bench is true (infra/bench.tf deploys one benchmark
function per memory size, running cloud/bench.py from the runner image).

    python tools/gen_synth.py                   # synthetic/: three made-up sets
    python tools/gen_synth.py --out synthetic/big --images 50 --photos 0 --sideways 0
    python tools/bench_lambda.py synthetic/Nguyen* --big "synthetic/big/Nguyen Van A & 2026-06-01" \\
        --out docs/aws/lambda-benchmark.md

1. Each folder's images go to bench/<n>/ in the data bucket (bench/ expires
   after 30 days). Only Synthetic Set folders: never employee screenshots.
2. Every benchmark function runs every folder twice. The first run in a new
   container is cold (it loads the image and the OCR models); the second is
   warm, the steady cost per image.
3. Each run gives the function's own timings and Lambda's REPORT line:
   billed duration, init duration (cold only) and peak memory.
4. The report: GB-s per image (warm), seconds per image, the cold start, and
   the month at 12,000 images and 450 Runs, every Run counted cold, against
   350,000 GB-s. Then the --big folder at the cheapest size, which must end
   inside 900 s; if not, the report says checkpoints carry it (AD-16).

Exit code 0 = measured and the plan holds, 1 = AWS failed, 2 = bad usage,
3 = measured but the plan doesn't hold (the report says what follows).
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud.storage import IMAGE_SUFFIXES  # noqa: E402
from tools.aws_cli import AwsError, account_id, run_cli  # noqa: E402

SIZES = (1536, 2048, 3008)
IMAGES_PER_MONTH = 12_000      # the growth case (solution design §2)
RUNS_PER_MONTH = 450
GB_S_BUDGET = 350_000          # of the 400,000 free each month (Phase 0 exit, §7)
LIMIT_S = 900
_REPORT = re.compile(
    r"REPORT RequestId: \S+\s+Duration: (?P<duration>[\d.]+) ms\s+Billed Duration: (?P<billed>\d+) ms"
    r"\s+Memory Size: (?P<memory>\d+) MB\s+Max Memory Used: (?P<used>\d+) MB"
    r"(?:\s+Init Duration: (?P<init>[\d.]+) ms)?")


def parse_report(log_tail: str) -> Dict[str, Any]:
    """Lambda's REPORT line from the end of an invocation's log."""
    m = None
    for m in _REPORT.finditer(log_tail):
        pass
    if m is None:
        raise ValueError("no REPORT line in the log tail")
    return {"durationMs": float(m["duration"]), "billedMs": int(m["billed"]), "memoryMb": int(m["memory"]),
            "maxUsedMb": int(m["used"]), "initMs": float(m["init"]) if m["init"] else None}


def gb_seconds(billed_ms: int, memory_mb: int) -> float:
    return billed_ms / 1000 * memory_mb / 1024


def invoke(function: str, prefix: str, *, run=subprocess.run) -> Dict[str, Any]:
    """One synchronous invocation: the function's result plus its REPORT line."""
    folder = Path(tempfile.mkdtemp(prefix="calls-log-bench-"))
    try:
        out = folder / "result.json"
        meta = json.loads(run_cli(
            ["lambda", "invoke", "--function-name", function, "--payload", json.dumps({"prefix": prefix}),
             "--cli-binary-format", "raw-in-base64-out", "--log-type", "Tail",
             "--cli-read-timeout", str(LIMIT_S + 60), "--output", "json", str(out)], run=run))
        result = json.loads(out.read_text(encoding="utf-8") or "{}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    tail = base64.b64decode(meta.get("LogResult", "")).decode("utf-8", errors="replace")
    report = parse_report(tail)
    if meta.get("FunctionError"):
        timed_out = "Task timed out" in tail
        return {"error": "timed out" if timed_out else str(result.get("errorType") or "error"), **report}
    return {**result, **report}


def upload(folders: List[Path], bucket: str, *, run=subprocess.run) -> List[str]:
    prefixes = []
    for n, folder in enumerate(folders, 1):
        prefix = f"bench/{n}/"
        run_cli(["s3", "rm", f"s3://{bucket}/{prefix}", "--recursive", "--only-show-errors"], run=run)
        for image in sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
            run_cli(["s3", "cp", str(image), f"s3://{bucket}/{prefix}{image.name}", "--only-show-errors"],
                    run=run)
        prefixes.append(prefix)
    return prefixes


def per_size(runs: List[Dict[str, Any]], memory_mb: int) -> Dict[str, Any]:
    """Warm runs give the steady cost per image; cold runs the extra a fresh container costs."""
    warm = [r for r in runs if not r.get("cold") and not r.get("error")]
    cold = [r for r in runs if r.get("cold") and not r.get("error")]
    images = sum(r["processed"] for r in warm)
    if not images:
        return {"memoryMb": memory_mb, "error": "no warm run finished"}
    per_image = sum(gb_seconds(r["billedMs"], memory_mb) for r in warm) / images
    seconds = statistics.median(r["medianImageMs"] for r in warm) / 1000
    # A cold run of a folder minus the warm run of the same folder: the start's own cost.
    warm_of = {r["prefix"]: r for r in warm}
    extra = [gb_seconds(c["billedMs"] - warm_of[c["prefix"]]["billedMs"], memory_mb)
             for c in cold if c["prefix"] in warm_of]
    cold_gbs = max(extra) if extra else 0.0
    init = [c["initMs"] for c in cold if c.get("initMs")]
    month = per_image * IMAGES_PER_MONTH + cold_gbs * RUNS_PER_MONTH
    return {"memoryMb": memory_mb, "gbsPerImage": per_image, "secondsPerImage": seconds,
            "coldGbs": cold_gbs, "coldInitS": max(init) / 1000 if init else None,
            "maxUsedMb": max(r["maxUsedMb"] for r in warm + cold), "monthGbs": month}


def report(sizes: List[Dict[str, Any]], big: Optional[Dict[str, Any]], today: str) -> tuple[str, bool]:
    ok_sizes = [s for s in sizes if "error" not in s]
    best = min(ok_sizes, key=lambda s: s["gbsPerImage"]) if ok_sizes else None
    lines = [f"# Lambda cost per image ({today})", "",
             "Story 3.1, measured with `tools/bench_lambda.py` on the Synthetic Set (made-up images).",
             "GB-s per image from warm runs; the cold start counted on every Run of the month "
             f"({IMAGES_PER_MONTH:,} images, {RUNS_PER_MONTH} Runs).", "",
             "| Memory | GB-s per image | s per image | Cold start | Cold GB-s | Peak memory | GB-s a month |",
             "|---:|---:|---:|---:|---:|---:|---:|"]
    for s in sizes:
        if "error" in s:
            lines.append(f"| {s['memoryMb']:,} MB | {s['error']} | | | | | |")
            continue
        cold = f"{s['coldInitS']:.1f} s" if s["coldInitS"] else "not seen"
        lines.append(f"| {s['memoryMb']:,} MB | {s['gbsPerImage']:.2f} | {s['secondsPerImage']:.1f} | {cold} "
                     f"| {s['coldGbs']:.1f} | {s['maxUsedMb']:,} MB | {s['monthGbs']:,.0f} |")
    lines.append("")
    holds = best is not None and best["monthGbs"] <= GB_S_BUDGET
    if best is None:
        lines.append("**No size finished a warm run; nothing can be chosen.**")
    elif holds:
        lines.append(f"**Chosen: {best['memoryMb']:,} MB**, the lowest GB-s per image. "
                     f"{best['monthGbs']:,.0f} GB-s a month is within {GB_S_BUDGET:,}. "
                     f"Set `runner_memory_mb = {best['memoryMb']}` and `enable_bench = false`.")
    else:
        lines.append(f"**The plan doesn't hold:** even {best['memoryMb']:,} MB needs {best['monthGbs']:,.0f} GB-s "
                     f"a month, over {GB_S_BUDGET:,}. An ADR must record the switch to the Fargate Spot "
                     "runner (AD-15) before more of Epic 3 is built.")
    if big is not None:
        lines.append("")
        if big.get("error") == "timed out" or big.get("cutOff"):
            done = big.get("processed", "some")
            lines.append(f"The {big.get('images', 40)}-image folder did **not** fit in one {LIMIT_S} s Run "
                         f"({done} images done): it relies on checkpoints (AD-16), and a second Run resumes it.")
        elif big.get("error"):
            lines.append(f"The big folder failed: {big['error']}.")
        else:
            lines.append(f"The {big['images']}-image folder took {big['durationMs'] / 1000:.0f} s at "
                         f"{big['memoryMb']:,} MB, inside the {LIMIT_S} s limit.")
    return "\n".join(lines) + "\n", holds


def main(argv: Optional[List[str]] = None, *, run=subprocess.run) -> int:
    ap = argparse.ArgumentParser(description="Story 3.1: Lambda cost per image (calls-log).")
    ap.add_argument("folders", nargs="+", type=Path, help="Synthetic Set folders of images")
    ap.add_argument("--big", type=Path, help="a 50-image Synthetic folder, run at the chosen size")
    ap.add_argument("--sizes", nargs="+", type=int, default=list(SIZES))
    ap.add_argument("--out", type=Path, help="also write the report here (docs/aws/...)")
    args = ap.parse_args(argv)
    for folder in args.folders + ([args.big] if args.big else []):
        if not folder.is_dir() or not any(p.suffix.lower() in IMAGE_SUFFIXES for p in folder.iterdir()):
            print(f"{folder}: not a folder of images", file=sys.stderr)
            return 2

    try:
        bucket = f"calls-log-data-{account_id(run=run)}"
        prefixes = upload(args.folders + ([args.big] if args.big else []), bucket, run=run)
        sizes = []
        for size in args.sizes:
            runs = []
            for prefix in prefixes[:len(args.folders)]:
                for _ in range(2):   # cold (a new container), then warm
                    runs.append({**invoke(f"calls-log-bench-{size}", prefix, run=run), "prefix": prefix})
                    print(f"{size} MB {prefix}: {runs[-1].get('error') or 'ok'}", file=sys.stderr)
            sizes.append(per_size(runs, size))
        ok = [s for s in sizes if "error" not in s]
        big = None
        if args.big and ok:
            chosen = min(ok, key=lambda s: s["gbsPerImage"])["memoryMb"]
            big = invoke(f"calls-log-bench-{chosen}", prefixes[-1], run=run)
    except AwsError as e:
        print(f"AWS failed: {e}", file=sys.stderr)
        return 1

    text, holds = report(sizes, big, _dt.date.today().isoformat())
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0 if holds else 3


if __name__ == "__main__":
    sys.exit(main())
