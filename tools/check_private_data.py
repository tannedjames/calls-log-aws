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

Usage:
    python tools/check_private_data.py            # everything publishable (run before pushing)
    python tools/check_private_data.py --staged   # what the next commit would add

Exit code 0 = clean, 1 = something private would be published.
Uses only the standard library, so any Python 3.10+ can run it.
"""

from __future__ import annotations

import re
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


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True).stdout


def private_values() -> list[tuple[str, str]]:
    """(label, value) pairs that must never appear in a published file."""
    vals: list[tuple[str, str]] = []
    secrets = ROOT / "secrets_local.py"
    if secrets.exists():
        text = secrets.read_text(encoding="utf-8")
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
                    vals.append((f"employee name '{key}'", key))
    return vals


def norm(text: str) -> str:
    """Same folding as norm_() in WebAppSync.gs: lower-case, strip accents, đ->d."""
    text = unicodedata.normalize("NFD", text.lower()).replace("đ", "d")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text)


def main() -> int:
    staged = "--staged" in sys.argv
    if staged:
        paths = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        # Tracked files plus new ones `git add .` would pick up.
        paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    files = [p for p in paths.decode("utf-8").split("\0") if p]

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

    values = private_values()
    for f in files:
        if f in PRIVATE_FILES or f.startswith(SKIP_CONTENT_SCAN):
            continue
        try:
            blob = git("show", f":{f}") if staged else (ROOT / f).read_bytes()
        except (OSError, subprocess.CalledProcessError):
            continue
        raw = blob.decode("utf-8", errors="ignore")
        folded = norm(raw)
        for label, value in values:
            hay = folded if label.startswith("employee name") else raw
            if value in hay:
                problems.append(f"{f}: contains {label}")

    if not (ROOT / "secrets_local.py").exists() or not (ROOT / "apps_script" / "Employees.gs").exists():
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
