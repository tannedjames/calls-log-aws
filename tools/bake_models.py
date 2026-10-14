"""
Bake the OCR models into the runner image, checked against models.sha256.

The runner image must read pictures with exactly the models the PC uses, and
must never download one at run time. At build time this tool lets the
pipeline download its models through its own config (the same call main.py
makes), checks every file against models.sha256, and copies the files
read-only into DEST. Any file that differs, is missing, or isn't listed
fails the build.

    python tools/bake_models.py --bake /opt/calls-log/models   # image build
    python tools/bake_models.py --check                        # does this PC match?
    python tools/bake_models.py --write-manifest               # record from this PC

--check and --write-manifest read PaddleX's cache (PADDLE_PDX_CACHE_HOME, by
default ~/.paddlex) unless --cache is given. Hoster metadata (README.md,
.gitattributes, .cache/) is neither checked nor copied.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "models.sha256"
SKIP_NAMES = {"README.md", ".gitattributes"}
SKIP_DIRS = {".cache"}
HEADER = """\
# SHA-256 of every OCR model file the pipeline uses (PaddleX official_models/).
# Recorded from a working install: python tools/bake_models.py --write-manifest
# The runner image build fails unless the models it downloads match exactly.
"""


def model_files(models_dir: Path) -> dict[str, Path]:
    """Relative POSIX path -> file, for every checked file under models_dir."""
    out = {}
    for p in sorted(models_dir.rglob("*")):
        rel = p.relative_to(models_dir)
        if p.is_file() and p.name not in SKIP_NAMES and not SKIP_DIRS & set(rel.parts):
            out[rel.as_posix()] = p
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(text: str) -> dict[str, str]:
    entries = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            digest, rel = line.split(maxsplit=1)
            entries[rel.strip()] = digest.lower()
    return entries


def verify(models_dir: Path, manifest: dict[str, str]) -> list[str]:
    """Problems found; an empty list means the files are exactly the manifest's."""
    files = model_files(models_dir)
    problems = []
    for rel, digest in sorted(manifest.items()):
        if rel not in files:
            problems.append(f"missing: {rel}")
        elif sha256(files[rel]) != digest:
            problems.append(f"checksum differs: {rel}")
    for rel in sorted(set(files) - set(manifest)):
        problems.append(f"not in models.sha256: {rel}")
    return problems


def default_cache() -> Path:
    home = os.environ.get("PADDLE_PDX_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".paddlex")
    return Path(home) / "official_models"


def bake(dest: Path) -> int:
    manifest = read_manifest(MANIFEST.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        # Must be set before paddlex is imported: it reads the cache home once.
        os.environ["PADDLE_PDX_CACHE_HOME"] = tmp
        sys.path.insert(0, str(ROOT))
        import runtime_env  # noqa: F401  -- before numpy/cv2/paddle
        import ocr
        ocr._check_framework("paddle")
        ocr._build_paddle_engine()          # downloads exactly the models CONFIG uses
        models_dir = Path(tmp) / "official_models"
        problems = verify(models_dir, manifest)
        if problems:
            print("Model files don't match models.sha256 -- refusing to bake:")
            for p in problems:
                print("  " + p)
            return 1
        for rel in manifest:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(models_dir / rel, target)
            target.chmod(0o444)
    print(f"baked {len(manifest)} verified model files into {dest}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bake", type=Path, metavar="DEST", help="download, verify, copy to DEST")
    mode.add_argument("--check", action="store_true", help="verify a local cache")
    mode.add_argument("--write-manifest", action="store_true", help="record a local cache")
    ap.add_argument("--cache", type=Path, help="official_models folder (default: PaddleX's)")
    args = ap.parse_args(argv)

    if args.bake:
        return bake(args.bake)
    cache = args.cache or default_cache()
    if not cache.is_dir():
        print(f"No models at {cache}: run the pipeline once to download them.")
        return 2
    if args.write_manifest:
        lines = [f"{sha256(p)}  {rel}" for rel, p in model_files(cache).items()]
        MANIFEST.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8")
        print(f"recorded {len(lines)} files from {cache} into {MANIFEST.name}")
        return 0
    problems = verify(cache, read_manifest(MANIFEST.read_text(encoding="utf-8")))
    for p in problems:
        print("  " + p)
    print(f"{cache}: " + ("matches models.sha256" if not problems else "DIFFERS from models.sha256"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
