"""
Parity check: does the runner image read the Reference Set exactly like the PC?

Runs a runner image on each reference folder and compares the Sessions with a
private expectations file. Everything stays on this PC: each folder is
mounted read-only as /ref/F<n>, the container has no network, and the output
shows only labels F1..Fn, counts and times -- never names, paths or numbers.
Re-run it after any change to requirements.lock, a model or the image.

The Reference Set and its expectations file hold employee data. Keep both
outside the repo; this tool refuses paths inside it.

Usage:
    python tools/parity_check.py --image calls-log-runner:local --ref <dir>
                                 [--expect FILE] [--docker PATH] [--via-runner]

--via-runner reads each folder through the AWS runner's entrypoint
(python3 -m cloud.runner --local) instead of main.py's functions sent inline.

--expect defaults to <dir>/parity-expected.json:
    {"folders": [{"name": "<folder in <dir>>",
                  "ignore": ["correct output.png"],
                  "sessions": [["08:00", "10:01"], ...]}, ...]}

Exit code 0 = every folder IDENTICAL; 1 = a difference, or a reference folder
changed during the run; 2 = bad input or Docker unavailable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
MARK = "PARITY_RESULT "

# Runs inside the image: main.py's batch path (same file order, same dedupe
# and session rules) minus writing, syncing and archiving. Sent with
# `python3 -c`, so any runner image can be checked, not only one built from
# this exact tree.
INSIDE = r'''
import json, logging, sys
import runtime_env  # noqa: F401
from pathlib import Path
from main import IMAGE_SUFFIXES, extract_calls
from session_builder import build_sessions, dedupe
logging.getLogger().setLevel(logging.WARNING)
folder, ignore = Path(sys.argv[1]), set(json.loads(sys.argv[2]))
files = sorted(p for p in folder.iterdir()
               if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and p.name not in ignore)
records, _meta, _stats = extract_calls(files)
kept, _dups = dedupe(records)
sessions, stats = build_sessions(kept)
print("PARITY_RESULT " + json.dumps({
    "images": len(files), "records": len(records), "countable": stats["countable_calls"],
    "sessions": [[s.start_time, s.end_time] for s in sessions]}))
'''


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def fingerprint(folder: Path) -> list[tuple]:
    """Every file's relative path, size and modified time: any write changes it."""
    return sorted((p.relative_to(folder).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in folder.rglob("*") if p.is_file())


def first_difference(expected: list, got: list) -> str | None:
    """None when the Sessions are identical, else the first one that differs."""
    for i, (e, g) in enumerate(zip(expected, got), 1):
        if list(e) != list(g):
            return f"session {i}: expected {e[0]}-{e[1]}, got {g[0]}-{g[1]}"
    if len(expected) > len(got):
        e = expected[len(got)]
        return f"session {len(got) + 1}: expected {e[0]}-{e[1]}, got none"
    if len(got) > len(expected):
        g = got[len(expected)]
        return f"session {len(expected) + 1}: expected none, got {g[0]}-{g[1]}"
    return None


def find_docker(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    return shutil.which("docker") or (str(DOCKER_DESKTOP) if DOCKER_DESKTOP.is_file() else None)


def run_folder(docker: str, image: str, folder: Path, label: str, ignore: list,
               via_runner: bool = False) -> dict:
    target = f"/ref/{label}"
    cmd = [docker, "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
           "--user", "1000:1000",
           "--mount", f"type=bind,source={folder},target={target},readonly", image]
    if via_runner:   # the AWS runner's own entrypoint (Story 3.5), same PARITY_RESULT line
        cmd += ["python3", "-m", "cloud.runner", "--local", target]
        for name in ignore:
            cmd += ["--ignore", name]
    else:
        cmd += ["python3", "-c", INSIDE, target, json.dumps(ignore)]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in reversed(out.stdout.splitlines()):
        if line.startswith(MARK):
            return json.loads(line[len(MARK):])
    # The container only ever sees /ref/F<n>, so its errors can't name the folder.
    last = (out.stderr.strip().splitlines() or ["(no error output)"])[-1][:200]
    raise RuntimeError(f"no result from the container (exit {out.returncode}): {last}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--image", required=True, help="runner image tag, e.g. calls-log-runner:local")
    ap.add_argument("--ref", type=Path, required=True, help="folder that holds the reference folders")
    ap.add_argument("--expect", type=Path, help="expectations (default: <ref>/parity-expected.json)")
    ap.add_argument("--docker", help="docker executable (default: PATH, then Docker Desktop's)")
    ap.add_argument("--via-runner", action="store_true",
                    help="read each folder through the AWS runner (python3 -m cloud.runner --local), "
                         "not main.py's functions")
    args = ap.parse_args(argv)

    expect = args.expect or args.ref / "parity-expected.json"
    if inside_repo(args.ref) or inside_repo(expect):
        print("The Reference Set and its expectations must stay outside the repo; "
              "refusing a path inside it.")
        return 2
    if not expect.is_file():
        print("No expectations file (parity-expected.json) in --ref; see this tool's docstring.")
        return 2
    folders = json.loads(expect.read_text(encoding="utf-8")).get("folders") or []
    if not folders:
        print("The expectations file lists no folders.")
        return 2
    docker = find_docker(args.docker)
    if not docker:
        print("Docker not found: install Docker Desktop (docs/TECHNICAL.md, 'Parity check').")
        return 2
    if subprocess.run([docker, "version", "--format", "{{.Server.Version}}"],
                      capture_output=True).returncode != 0:
        print("Docker is installed but its engine isn't running: start Docker Desktop.")
        return 2
    if subprocess.run([docker, "image", "inspect", args.image], capture_output=True).returncode != 0:
        print(f"No image {args.image!r}: build it with "
              f"docker build --platform linux/amd64 -t {args.image} .")
        return 2

    failed = 0
    for n, f in enumerate(folders, 1):
        label = f"F{n}"
        folder = args.ref / f["name"]
        if not folder.is_dir() or "," in str(folder):
            print(f"{label}: reference folder missing or unusable (a ',' in its path) -> DIFFERENT")
            failed += 1
            continue
        before = fingerprint(folder)
        try:
            got = run_folder(docker, args.image, folder, label, f.get("ignore", []), args.via_runner)
        except RuntimeError as exc:
            print(f"{label}: {exc} -> DIFFERENT")
            failed += 1
            continue
        if fingerprint(folder) != before:
            print(f"{label}: the reference folder CHANGED during the run -> DIFFERENT")
            failed += 1
            continue
        diff = first_difference(f["sessions"], got["sessions"])
        failed += diff is not None
        print(f"{label}: {got['images']} images, {got['countable']} countable calls, "
              f"{len(got['sessions'])} sessions -> "
              + ("IDENTICAL" if diff is None else f"DIFFERENT ({diff})"), flush=True)
    print("Parity: " + ("all folders IDENTICAL." if not failed else f"{failed} folder(s) DIFFERENT."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
