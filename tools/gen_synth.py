"""
Render a Synthetic Set: fictional call-log screenshots with known answers.

Nothing here is real data. The folder name uses the fictional "Nguyen Van A",
contact labels are invented, and no phone numbers are drawn. The set lets OCR
be regression-tested anywhere, including CI, without employee screenshots.

Writes, under --out (default: synthetic/, which is gitignored):
  Nguyen Van A & 2026-06-01/shot_NN.png            screenshots
  Nguyen Van A & 2026-06-01_photo/photo_NN.jpg     screenshots on a dark desk
  Nguyen Van A & 2026-06-01_sideways/side_NN.jpg   those photos turned 90/270
  truth.json                                       expected calls per set

The same seed always gives the same truth.json, and byte-identical images
when the same font is used. Check the pipeline against it with
tools/check_synth.py.

Usage:
    python tools/gen_synth.py [--seed N] [--out DIR] [--images N]
                              [--photos N] [--sideways N] [--font PATH]
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FOLDER = "Nguyen Van A & 2026-06-01"

W, H = 1080, 2340
ROW_TOP, ROW_H, ROWS_PER_SHOT = 330, 180, 11
LABELS = ["株式会社テスト 様", "サンプル商事", "見本 一郎 様", "見本工業", "架空 太郎",
          "例示物産", "テスト顧客", "試験 次郎 様", "ダミー株式会社", "検証 花子"]
DESK = (45, 33, 26)

# (regular, bold) font files to try, first match wins. Bold may be missing.
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/YuGothB.ttc"),
    ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/meiryob.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",       # Debian/Ubuntu
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",     # Fedora/AL2023
     "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc"),
]


# ---------------------------------------------------------------------------
# Plan: everything random is drawn here, from the seed alone
# ---------------------------------------------------------------------------

def _make_calls(n: int, rng: random.Random) -> list[dict]:
    """n calls for one day, oldest first."""
    t = 8 * 60 + 30
    calls = []
    for i in range(n):
        t += rng.choice([0, 1, 1, 2, 2, 3, 4, 6, 8, 15])
        kind = rng.choices(["out", "in", "miss"], weights=[70, 20, 10])[0]
        secs = 0 if kind == "miss" else rng.choice([0, 12, 32, 45, 58, 75, 85, 130, 200])
        # The pipeline merges identical calls (same minute, kind and length),
        # so the truth must never hold two: move such a call one minute on.
        # Times never go backwards, so only the last few calls can collide.
        if any((c["min"], c["kind"], c["secs"]) == (t, kind, secs) for c in calls[-5:]):
            t += 1
        calls.append({"i": i, "min": t, "kind": kind, "secs": secs,
                      "label": rng.choice(LABELS)})
        t += (secs + 59) // 60
    if t >= 23 * 60:
        raise ValueError(f"the day overflowed ({t // 60}:{t % 60:02d}); use fewer images")
    return calls


def plan(seed: int, images: int, photos: int, sideways: int) -> dict:
    """
    Draw one day of calls and split it into screenshots, newest first, with
    one row of overlap between consecutive screenshots (as employees take
    them). Photos reuse the first screenshots; sideways images reuse the
    first photos. Returns the rows per screenshot, the photo tilts and the
    expected calls per set.
    """
    if images < 1 or not 0 <= photos <= images or not 0 <= sideways <= photos:
        raise ValueError("need 1 <= images, 0 <= photos <= images, 0 <= sideways <= photos")
    rng = random.Random(seed)
    calls = _make_calls(images * ROWS_PER_SHOT - (images - 1), rng)
    newest_first = calls[::-1]
    step = ROWS_PER_SHOT - 1
    shots = [newest_first[k * step:k * step + ROWS_PER_SHOT] for k in range(images)]
    tilts = [rng.uniform(-4, 4) for _ in range(photos)]

    def truth(n_images: int) -> list[dict]:
        shown = {c["i"] for shot in shots[:n_images] for c in shot}
        return [{"start": f"{c['min'] // 60:02d}:{c['min'] % 60:02d}",
                 "kind": c["kind"], "secs": c["secs"]}
                for c in calls if c["i"] in shown]

    return {
        "seed": seed, "shots": shots, "tilts": tilts, "sideways": sideways,
        "truth": {
            "screenshots": {"dir": FOLDER, "calls": truth(images)},
            "photos": {"dir": f"{FOLDER}_photo", "calls": truth(photos)},
            "sideways": {"dir": f"{FOLDER}_sideways", "calls": truth(sideways)},
        },
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def find_fonts(regular: str | None) -> tuple[str, str]:
    if regular:
        if not Path(regular).is_file():
            raise SystemExit(f"--font not found: {regular}")
        return regular, regular
    for reg, bold in FONT_CANDIDATES:
        if Path(reg).is_file():
            return reg, bold if Path(bold).is_file() else reg
    raise SystemExit(
        "No Japanese font found. Pass --font <path to a CJK .ttf/.ttc>, or install "
        "one (Ubuntu: apt-get install fonts-noto-cjk; AL2023: dnf install "
        "google-noto-sans-cjk-fonts).")


def _fmt_time(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{h % 12 or 12:02d}:{mm:02d} {'AM' if h < 12 else 'PM'}"


def _fmt_dur(s: int) -> str:
    return f"({s}秒)" if s < 60 else f"({s // 60}分{s % 60}秒)"


def render_shot(rows: list[dict], fonts: tuple[str, str]) -> Image.Image:
    """One Rakuten-Link-style call-log screen, newest call at the top."""
    reg_path, bold_path = fonts
    small = ImageFont.truetype(reg_path, 34)
    reg = ImageFont.truetype(reg_path, 44)
    sub = ImageFont.truetype(reg_path, 38)
    head = ImageFont.truetype(bold_path, 58)

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    # Status bar clock and header: the parser must ignore both.
    d.text((40, 30), "14:32", fill=(60, 60, 60), font=small)
    d.text((W - 330, 30), "Rakuten 4G 87%", fill=(60, 60, 60), font=small)
    d.text((40, 150), "通話履歴", fill="black", font=head)
    d.text((40, 250), "すべての通話     不在着信", fill=(110, 110, 110), font=sub)
    for i, c in enumerate(rows):
        y = ROW_TOP + i * ROW_H
        d.ellipse((40, y + 20, 140, y + 120), fill=(225, 228, 235))
        d.text((170, y + 18), c["label"], fill="black", font=reg)
        if c["kind"] == "miss":
            line, colour = f"{_fmt_time(c['min'])}  不在着信", (200, 40, 40)
        else:
            word = "発信" if c["kind"] == "out" else "着信"
            line, colour = f"{_fmt_time(c['min'])}  {word}  {_fmt_dur(c['secs'])}", (120, 120, 120)
        d.text((170, y + 92), line, fill=colour, font=sub)
        d.line((170, y + ROW_H - 4, W - 40, y + ROW_H - 4), fill=(235, 235, 235), width=2)
    return img


def to_photo(shot: Image.Image, tilt: float) -> Image.Image:
    """The screen on a dark desk, tilted a few degrees and slightly blurred."""
    canvas = Image.new("RGB", (960, 1280), DESK)
    screen = shot.resize((520, 1127)).rotate(tilt, expand=True, fillcolor=DESK)
    canvas.paste(screen, ((960 - screen.width) // 2, (1280 - screen.height) // 2))
    return canvas.filter(ImageFilter.GaussianBlur(0.8))


def write_set(out: Path, p: dict, fonts: tuple[str, str]) -> None:
    sets = p["truth"]
    dirs = [out / s["dir"] for s in sets.values()]
    # Replace an older Synthetic Set so images from two runs never mix, but
    # only where truth.json shows this tool wrote it: never delete folders in
    # a directory someone pointed --out at by mistake.
    if any(d.exists() for d in dirs) and not (out / "truth.json").is_file():
        raise SystemExit(f"{out} already holds folders this tool did not write; "
                         f"choose another --out")
    for d in dirs:
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for k, rows in enumerate(p["shots"]):
        shot = render_shot(rows, fonts)
        shot.save(out / sets["screenshots"]["dir"] / f"shot_{k + 1:02d}.png")
        if k < len(p["tilts"]):
            photo = to_photo(shot, p["tilts"][k])
            photo.save(out / sets["photos"]["dir"] / f"photo_{k + 1:02d}.jpg", quality=88)
            if k < p["sideways"]:
                # Alternate 90 and 270 degrees: the page classifier must handle both.
                side = photo.rotate(90 if k % 2 == 0 else 270, expand=True)
                side.save(out / sets["sideways"]["dir"] / f"side_{k + 1:02d}.jpg", quality=88)
    truth = {"generator": "tools/gen_synth.py", "seed": p["seed"], "sets": sets}
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--out", type=Path, default=ROOT / "synthetic")
    ap.add_argument("--images", type=int, default=12, help="screenshots (default 12)")
    ap.add_argument("--photos", type=int, default=4, help="photo variants (default 4)")
    ap.add_argument("--sideways", type=int, default=2, help="sideways photos (default 2)")
    ap.add_argument("--font", help="CJK font file (default: auto-detect)")
    args = ap.parse_args(argv)

    try:
        p = plan(args.seed, args.images, args.photos, args.sideways)
    except ValueError as exc:
        raise SystemExit(str(exc))
    fonts = find_fonts(args.font)
    write_set(args.out, p, fonts)
    counts = {name: len(s["calls"]) for name, s in p["truth"].items()}
    print(f"seed {args.seed}: {args.images} screenshots, {args.photos} photos, "
          f"{args.sideways} sideways -> {args.out} (expected calls {counts}; "
          f"font {Path(fonts[0]).name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
