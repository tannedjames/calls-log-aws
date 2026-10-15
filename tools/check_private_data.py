"""
Refuse to publish employee data or credentials.

Checks every file git would publish (tracked, plus untracked files that are
not ignored) for:
  * call-log screenshots, workbooks, or reports (anything under input_images/
    or output/ except .gitkeep; any image; any .xlsx except sprs.xlsx);
  * the private files themselves (secrets_local.py, apps_script/Employees.gs);
  * the real Web App secret, the /exec URL, and every employee name key and
    spreadsheet ID -- read from those private files, so a value pasted into
    ANY tracked file is caught. Values are never printed.

Where the private files don't exist (CI, other PCs), a hashed denylist stands
in for them: keyed hashes of the same values, exported on the Owner's PC, so
the values themselves never leave it. It comes from the environment variable
PRIVATE_DENYLIST (a GitHub secret) or --denylist FILE.

Usage:
    python tools/check_private_data.py                       # everything publishable (before pushing)
    python tools/check_private_data.py --staged              # what the next commit would add
    python tools/check_private_data.py --fail-closed         # CI: no private files and no denylist = FAIL
    python tools/check_private_data.py --export-denylist F   # Owner's PC: write the hashed denylist to F

Exit code 0 = clean, 1 = something private would be published (or, with
--fail-closed, nothing to check against), 2 = bad usage.
Uses only the standard library, so any Python 3.10+ can run it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRIVATE_FILES = {"secrets_local.py", "apps_script/Employees.gs"}
DATA_DIRS = ("input_images/", "output/")
ALLOWED = {"input_images/.gitkeep", "output/.gitkeep", "sprs.xlsx"}
DATA_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".xlsx", ".xls")
# Vendored tool folders: generic, contain no project data, and are large.
SKIP_CONTENT_SCAN = ("_bmad/", ".claude/skills/")
NAME_LABEL = "employee name"
DENYLIST_FORMAT = "calls-log-denylist/1"


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True).stdout


def have_private_files() -> bool:
    return all((ROOT / f).exists() for f in PRIVATE_FILES)


def private_values() -> list[tuple[str, str]]:
    """(label, value) pairs that must never appear in a published file."""
    vals: list[tuple[str, str]] = []
    secrets_file = ROOT / "secrets_local.py"
    if secrets_file.exists():
        text = secrets_file.read_text(encoding="utf-8")
        for name in ("SHEETS_SYNC_SECRET", "SHEETS_WEBAPP_URL"):
            m = re.search(rf'^{name}\s*=\s*["\']([^"\']+)["\']', text, re.M)
            if m:
                vals.append((name, m.group(1)))
                if name == "SHEETS_WEBAPP_URL":
                    dep = re.search(r"/s/([^/]+)/exec", m.group(1))
                    if dep:
                        vals.append(("Web App deployment ID", dep.group(1)))
    employees = ROOT / "apps_script" / "Employees.gs"
    if employees.exists():
        text = employees.read_text(encoding="utf-8")
        for sid in sorted(set(re.findall(r"'([A-Za-z0-9_-]{25,})'", text))):
            vals.append(("a spreadsheet ID from Employees.gs", sid))
        block = re.search(r"var NAME_TO_ID\s*=\s*\{(.*?)\};", text, re.S)
        if block:
            for key in re.findall(r"'([^']+)'\s*:", block.group(1)):
                # Skip pure aliases like 'id 2'; any real name word counts.
                if any(len(w) >= 3 and w.isalpha() for w in key.split()):
                    vals.append((f"{NAME_LABEL} '{key}'", key))
    return vals


def norm(text: str) -> str:
    """Same folding as norm_() in WebAppSync.gs: lower-case, strip accents, đ->d."""
    text = unicodedata.normalize("NFD", text.lower()).replace("đ", "d")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text)


# ---------------------------------------------------------------------------
# Hashed denylist: the private values as keyed hashes of fixed-length text
# windows. A file matches when any window of the right length hashes to an
# entry -- the same substring test as the plaintext check, without the values.
# ---------------------------------------------------------------------------

def _mac(key: bytes, text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), key=key, digest_size=16).hexdigest()


def export_denylist(values: list[tuple[str, str]], key: bytes | None = None) -> dict:
    """Keyed hashes of `values`; labels say what kind of value, never which."""
    key = key or secrets.token_bytes(32)
    counts: dict[str, int] = {}
    entries = []
    for label, value in values:
        folded = label.startswith(NAME_LABEL)
        if folded:
            kind = NAME_LABEL
        elif label.startswith("a spreadsheet ID"):
            kind = "spreadsheet ID"
        else:
            kind = label
        counts[kind] = counts.get(kind, 0) + 1
        text = norm(value) if folded else value
        entries.append({
            "label": f"{kind} #{counts[kind]}" if kind in (NAME_LABEL, "spreadsheet ID") else kind,
            "folded": folded, "length": len(text), "mac": _mac(key, text),
        })
    return {"format": DENYLIST_FORMAT, "key": key.hex(), "entries": entries}


def load_denylist(raw: str | None) -> dict | None:
    """Parse a denylist; None when absent. Raises ValueError when malformed."""
    if not raw or not raw.strip():
        return None
    data = json.loads(raw)
    if data.get("format") != DENYLIST_FORMAT or not data.get("entries"):
        raise ValueError("not a calls-log denylist (wrong format or no entries)")
    bytes.fromhex(data["key"])
    return data


def denylist_hits(raw: str, folded: str, deny: dict) -> list[str]:
    """Labels of the denylist entries found in this text."""
    key = bytes.fromhex(deny["key"])
    groups: dict[tuple[bool, int], dict[str, str]] = {}
    for e in deny["entries"]:
        groups.setdefault((bool(e["folded"]), int(e["length"])), {})[e["mac"]] = e["label"]
    hits = set()
    for (is_folded, n), macs in groups.items():
        hay = folded if is_folded else raw
        for i in range(len(hay) - n + 1):
            label = macs.get(_mac(key, hay[i:i + n]))
            if label:
                hits.add(label)
    return sorted(hits)


# ---------------------------------------------------------------------------

def publishable_files(staged: bool) -> list[str]:
    if staged:
        paths = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        # Tracked files plus new ones `git add .` would pick up.
        paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return [p for p in paths.decode("utf-8").split("\0") if p]


def read_publishable(f: str, staged: bool) -> str | None:
    try:
        blob = git("show", f":{f}") if staged else (ROOT / f).read_bytes()
    except (OSError, subprocess.CalledProcessError):
        return None
    return blob.decode("utf-8", errors="ignore")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staged", action="store_true", help="check what the next commit would add")
    ap.add_argument("--fail-closed", action="store_true",
                    help="fail when neither the private files nor a denylist are available (CI)")
    ap.add_argument("--denylist", type=Path, help="hashed denylist file (default: env PRIVATE_DENYLIST)")
    ap.add_argument("--export-denylist", type=Path, metavar="FILE",
                    help="write the hashed denylist from the private files to FILE")
    args = ap.parse_args(argv)

    values = private_values()
    if args.export_denylist:
        if not have_private_files():
            print("Export needs secrets_local.py and apps_script/Employees.gs: run it on the Owner's PC, where they are.")
            return 2
        deny = export_denylist(values)
        args.export_denylist.write_text(json.dumps(deny), encoding="utf-8")
        print(f"Wrote {len(deny['entries'])} hashed entries to {args.export_denylist}. Add its contents "
              f"as the GitHub secret PRIVATE_DENYLIST, then delete the file.")
        return 0

    try:
        raw = (args.denylist.read_text(encoding="utf-8") if args.denylist
               else os.environ.get("PRIVATE_DENYLIST"))
        deny = load_denylist(raw)
    except (OSError, ValueError) as exc:
        print(f"The denylist can't be used ({exc.__class__.__name__}); refusing to pass.")
        return 1

    files = publishable_files(args.staged)
    problems: list[str] = []
    for f in files:
        low = f.lower()
        if f in ALLOWED:
            continue
        if f in PRIVATE_FILES:
            problems.append(f"{f}: private file is tracked")
        elif low.startswith(DATA_DIRS):
            problems.append(f"{f}: employee data folder")
        elif low.endswith(DATA_SUFFIXES):
            problems.append(f"{f}: screenshot/workbook file type")

    for f in files:
        if f in PRIVATE_FILES or f.startswith(SKIP_CONTENT_SCAN):
            continue
        raw_text = read_publishable(f, args.staged)
        if raw_text is None:
            continue
        folded = norm(raw_text)
        for label, value in values:
            if label.startswith(NAME_LABEL):
                found = norm(value) in folded
            else:
                found = value in raw_text
            if found:
                problems.append(f"{f}: contains {label}")
        if deny:
            for label in denylist_hits(raw_text, folded, deny):
                problems.append(f"{f}: contains {label} (denylist)")

    if not have_private_files():
        if deny:
            print(f"Private files absent; checked against the hashed denylist "
                  f"({len(deny['entries'])} entries).")
        elif args.fail_closed:
            print("FAILED CLOSED: secrets_local.py / apps_script/Employees.gs are missing and no "
                  "PRIVATE_DENYLIST was given, so names, IDs and the secret can't be checked. "
                  "Export it on the Owner's PC (docs/TECHNICAL.md, section 10).")
            return 1
        else:
            print("note: secrets_local.py or apps_script/Employees.gs is missing, "
                  "so their values could not be checked.")
    if problems:
        print("PRIVATE DATA WOULD BE PUBLISHED -- fix before committing/pushing:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"OK: {len(files)} file(s) checked, no employee data or credentials found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
