"""
Process-level environment setup. Import this BEFORE any heavy native library
(paddle, torch, cv2, numpy BLAS) is imported.

Why this exists
---------------
The venv has BOTH paddlepaddle and torch (EasyOCR) installed. Each ships its
own Intel OpenMP runtime (``libiomp5md.dll``). On Windows, loading two OpenMP
runtimes into one process is unsupported: it either aborts with "OMP: Error
#15" or, more insidiously, installs a second Windows console control handler.
That second handler can deliver a spurious CTRL_C_EVENT to the process, which
CPython turns into a ``KeyboardInterrupt`` that nobody typed.

That is the root cause of the "KeyboardInterrupt despite no keyboard
interrupt" symptom: PaddleOCR failed to construct (2.x API used against
PaddleOCR 3.x), ``run_ocr`` silently fell back to EasyOCR, and torch's OpenMP
runtime was loaded on top of paddle's inside the same process.

Two defences:
  1. ``configure()`` sets KMP_DUPLICATE_LIB_OK / thread caps before anything
     native loads (mitigation).
  2. ``note_framework()`` in ocr.py refuses to import a second deep-learning
     framework in a process that already loaded one (prevention).
"""

from __future__ import annotations

import os
import sys

_configured = False


def _cgroup_cpu_limit(text: str) -> int | None:
    """
    CPUs allowed by a cgroup CPU quota, rounded up (1.5 CPUs -> 2).

    ``text`` is "<quota> <period>": cgroup v2 ``cpu.max`` as written, or the
    two cgroup v1 ``cpu.cfs_*_us`` values joined. "max" or "-1" as the quota
    means no limit.
    """
    parts = text.split()
    if len(parts) != 2 or parts[0] in ("max", "-1"):
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, -(-quota // period))


def _read_cgroup_cpu_limit() -> int | None:
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as f:          # v2
            return _cgroup_cpu_limit(f.read())
    except OSError:
        pass
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", encoding="ascii") as q, \
                open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", encoding="ascii") as p:  # v1
            return _cgroup_cpu_limit(f"{q.read().strip()} {p.read().strip()}")
    except OSError:
        return None


def available_cpus() -> int:
    """
    CPUs this process may actually use: the machine's count, narrowed by CPU
    affinity and by a container's cgroup quota.

    Inside a container os.cpu_count() reports the HOST's CPUs, so threads
    sized from it oversubscribe a 2-vCPU limit; oversubscribed OCR measured
    2-3x slower. On Windows neither limit applies and this is os.cpu_count().
    """
    n = os.cpu_count() or 4
    if hasattr(os, "sched_getaffinity"):
        try:
            n = min(n, len(os.sched_getaffinity(0)))
        except OSError:
            pass
    limit = _read_cgroup_cpu_limit()
    if limit:
        n = min(n, limit)
    return max(1, n)


def configure() -> None:
    """Set OpenMP / threading env vars. Safe to call more than once."""
    global _configured
    if _configured:
        return
    _configured = True

    defaults = {
        # Tolerate a duplicate OpenMP runtime instead of aborting the process.
        "KMP_DUPLICATE_LIB_OK": "TRUE",
        # Stop OpenMP from installing its own console-ctrl handler / spinning.
        "KMP_INIT_AT_FORK": "FALSE",
        "KMP_BLOCKTIME": "0",
        # Use the CPUs this process may use (all cores on the PC; the vCPU
        # limit in a container). An earlier version capped this at 4, which
        # cost roughly 2x on an 8-core box for no benefit -- the cap was meant
        # to tame the paddle+torch OpenMP clash, but the engine guard in ocr.py
        # already prevents both frameworks loading at once.
        "OMP_NUM_THREADS": str(available_cpus()),
        "MKL_NUM_THREADS": str(available_cpus()),
        # Silence PaddleX / PaddleOCR telemetry + banner noise.
        "PADDLE_PDX_DISABLE_DEV_MODE": "1",
        "FLAGS_call_stack_level": "1",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

    # Windows console: make sure stdout can print Japanese/Vietnamese without
    # blowing up on cp932/cp1252.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


configure()
