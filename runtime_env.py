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
        # Use the machine's cores. An earlier version capped this at 4, which
        # cost roughly 2x on an 8-core box for no benefit -- the cap was meant
        # to tame the paddle+torch OpenMP clash, but the engine guard in ocr.py
        # already prevents both frameworks loading at once.
        "OMP_NUM_THREADS": str(max(1, os.cpu_count() or 4)),
        "MKL_NUM_THREADS": str(max(1, os.cpu_count() or 4)),
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
