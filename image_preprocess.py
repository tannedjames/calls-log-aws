"""
Image preprocessing for the OCR pipeline.

Two very different inputs arrive here:

* **Screenshots** — already flat, already the right size, high contrast.
  Almost nothing should be done to them.
* **Photos of a phone screen** — the screen is a rotated/perspective-skewed
  bright quadrilateral floating in a dark surround, usually slightly blurry,
  often with moiré banding and glare.

The old pipeline treated both the same and, in practice, did nothing useful to
the photos:

* ``detect_phone_screen`` bailed out whenever more than 55% of pixels were on
  the bright side of Otsu — but real photos here measure 55-71%, so the crop
  almost never fired and the dark wooden desk stayed in the frame.
* ``deskew`` then binarised that whole frame, so the projection-profile score
  was dominated by the dark surround rather than the text lines, and it
  "corrected" by a meaningless angle.
* it did so with 33 full-resolution rotations per image, which is where most
  of the wall-clock time went.

Now: rectify the screen with a perspective warp (removes surround, rotation
*and* keystone in one step), then only deskew the small residual angle, on a
downscaled copy.
"""

from __future__ import annotations

import runtime_env  # noqa: F401  -- must come before cv2/numpy native loads

import io
import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageEnhance, ImageOps, ImageFilter

from config import CONFIG

log = logging.getLogger(__name__)


def _cv2():
    """Import OpenCV lazily; return None when unavailable."""
    try:
        import cv2
        return cv2
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_image(path: Path) -> Image.Image:
    """Load an image and bake EXIF orientation into pixels."""
    img = Image.open(path)
    # ImageOps.exif_transpose returns a new image rotated according to EXIF
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


# ---------------------------------------------------------------------------
# Screenshot vs photo
# ---------------------------------------------------------------------------

def looks_like_screenshot(img: Image.Image) -> Tuple[bool, dict]:
    """
    Decide whether this image IS a screen (screenshot) or contains one (photo).

    Screenshots are near-uniformly bright edge-to-edge and their border pixels
    are app chrome, not desk. Photos in this dataset measure a bright fraction
    of roughly 0.55-0.71; screenshots measure ~0.91. We also look at the border
    ring directly, which separates the two far more sharply than a global
    threshold.
    """
    cv2 = _cv2()
    arr = np.array(img.convert("L"), dtype=np.uint8)
    h, w = arr.shape[:2]

    if cv2 is not None:
        _, binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        binary = (arr > int(arr.mean())).astype(np.uint8) * 255
    bright_frac = float((binary > 0).sum()) / binary.size

    # Mean luminance of a thin ring around the edge of the frame.
    ring = max(2, int(min(h, w) * 0.03))
    border = np.concatenate([
        arr[:ring, :].ravel(), arr[-ring:, :].ravel(),
        arr[:, :ring].ravel(), arr[:, -ring:].ravel(),
    ])
    border_mean = float(border.mean())

    meta = {
        "bright_frac": round(bright_frac, 3),
        "border_mean": round(border_mean, 1),
    }
    is_shot = bright_frac >= CONFIG.screenshot_bright_frac and border_mean >= 110.0
    meta["verdict"] = "screenshot" if is_shot else "photo"
    return is_shot, meta


# ---------------------------------------------------------------------------
# Screen rectification (photos of phone displays)
# ---------------------------------------------------------------------------

def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    ordered = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    ordered[0] = pts[np.argmin(s)]   # top-left  : smallest x+y
    ordered[2] = pts[np.argmax(s)]   # bottom-right
    ordered[1] = pts[np.argmin(d)]   # top-right : smallest y-x
    ordered[3] = pts[np.argmax(d)]   # bottom-left
    return ordered


def rectify_phone_screen(img: Image.Image) -> Tuple[Image.Image, dict]:
    """
    Find the bright phone screen inside a photo and warp it to a flat
    rectangle: removes the surrounding desk, the hand tilt and the keystone
    from holding the camera at an angle.

    Falls back to the original image on any failure. Never raises.
    """
    meta = {"applied": False}
    cv2 = _cv2()
    if cv2 is None:
        meta["reason"] = "opencv_not_installed"
        return img, meta

    try:
        arr = np.array(img.convert("RGB"))
        h_img, w_img = arr.shape[:2]
        img_area = float(h_img * w_img)

        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        _, binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )

        # Close then open: bridge the dark text inside the screen into one
        # solid blob, then shed thin bright specks (glare on the desk).
        ksize = max(9, (min(h_img, w_img) // 40) | 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, ksize))
        mask = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            meta["reason"] = "no_contours"
            return img, meta

        chosen = None
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            area = float(cv2.contourArea(cnt))
            frac = area / img_area
            # Too small: not the screen. Too large: the whole frame is the
            # screen already, so there is nothing to crop away.
            if frac < CONFIG.screen_min_area_frac or frac > CONFIG.screen_max_area_frac:
                continue
            rect = cv2.minAreaRect(cnt)
            (_, _), (rw, rh), _ = rect
            if rw < 50 or rh < 50:
                continue
            if area / (rw * rh) < 0.72:   # not convincingly rectangular
                continue
            long_side, short_side = max(rw, rh), min(rw, rh)
            if not (1.2 <= long_side / short_side <= 3.2):
                continue
            chosen = (rect, area, frac)
            break

        if chosen is None:
            meta["reason"] = "no_screen_shaped_contour"
            return img, meta

        rect, area, frac = chosen
        corners = _order_corners(cv2.boxPoints(rect).astype("float32"))
        tl, tr, br, bl = corners
        out_w = int(round(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))))
        out_h = int(round(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))))
        if out_w < 100 or out_h < 100:
            meta["reason"] = "rectified_too_small"
            return img, meta

        dst = np.array(
            [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(corners, dst)
        warped = cv2.warpPerspective(
            arr, matrix, (out_w, out_h),
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
        )

        # Trim a hair off each edge: the screen bezel bleeds in, and a dark
        # sliver along one side skews autocontrast for the whole image.
        inset_x = int(out_w * 0.015)
        inset_y = int(out_h * 0.010)
        if inset_x or inset_y:
            warped = warped[inset_y:out_h - inset_y, inset_x:out_w - inset_x]

        meta.update({
            "applied": True,
            "original_size": [int(w_img), int(h_img)],
            "rectified_size": [int(warped.shape[1]), int(warped.shape[0])],
            "screen_area_fraction": round(frac, 3),
            "rotation_deg": round(float(rect[2]), 2),
        })
        return Image.fromarray(warped), meta
    except Exception as exc:
        log.debug("Screen rectification skipped: %s", exc)
        meta["reason"] = f"error:{exc.__class__.__name__}"
        return img, meta


# Backwards-compatible alias (older callers / tests used this name).
detect_phone_screen = rectify_phone_screen


# ---------------------------------------------------------------------------
# Rotation detection
# ---------------------------------------------------------------------------

def detect_and_fix_rotation(img: Image.Image) -> Tuple[Image.Image, int]:
    """
    Detect a 90/180/270 rotation.

    Preferred path is Tesseract's OSD, which needs BOTH the pytesseract package
    AND the tesseract binary with osd traineddata — neither is installed in
    this venv, so previously this silently did nothing on every single image.
    We now log that at INFO once, and use a cheap aspect-ratio heuristic as a
    backstop: these call-log screens are always portrait.
    """
    try:
        import pytesseract
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        rot = int(osd.get("rotate", 0)) % 360
        if rot in (90, 180, 270):
            # PIL rotates counter-clockwise; OSD reports clockwise-needed angle
            img = img.rotate(-rot, expand=True)
            return img, rot
        return img, 0
    except ImportError:
        _warn_no_osd()
    except Exception as exc:  # pragma: no cover - OSD is optional
        log.debug("OSD rotation detection skipped: %s", exc)

    # Backstop: a landscape call-log screen is almost certainly a sideways
    # portrait one. We cannot tell 90 from 270, so leave it alone but flag it.
    w, h = img.size
    if CONFIG.warn_on_landscape and w > h * 1.15:
        log.warning(
            "Image is landscape (%dx%d). Call-log screens are portrait; if OCR "
            "comes back empty, rotate this file and re-run.", w, h,
        )
    return img, 0


_osd_warned = False


def _warn_no_osd() -> None:
    global _osd_warned
    if not _osd_warned:
        _osd_warned = True
        log.info(
            "pytesseract not installed: skipping pre-OCR rotation detection. "
            "Harmless when using PaddleOCR -- its document-orientation "
            "classifier (paddle_use_doc_orientation) turns landscape "
            "(sideways) images upright. Only install pytesseract if you "
            "switch engines."
        )


# ---------------------------------------------------------------------------
# Deskew (slight angle correction)
# ---------------------------------------------------------------------------

def deskew(img: Image.Image, max_abs_angle: float = 6.0,
           work_long_edge: int = 900) -> Image.Image:
    """
    Estimate a small skew angle from the horizontal projection profile.

    Two changes from the original:
      * the sweep runs on a downscaled copy (~900px long edge) instead of the
        full-resolution image, so it costs milliseconds rather than tens of
        seconds per photo;
      * it uses a coarse pass followed by a fine pass instead of 33 uniform
        steps, and requires the winning score to actually beat the do-nothing
        score before rotating at all.
    """
    try:
        long_edge = max(img.size)
        scale = min(1.0, work_long_edge / float(long_edge))
        if scale < 1.0:
            small = img.resize(
                (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale))),
                resample=Image.BILINEAR,
            )
        else:
            small = img

        gray = np.array(small.convert("L"), dtype=np.uint8)
        thresh = _otsu_threshold(gray)
        binary = (gray < thresh).astype(np.uint8) * 255
        base = Image.fromarray(binary)

        def score_at(angle: float) -> float:
            if angle == 0.0:
                arr = binary
            else:
                arr = np.array(
                    base.rotate(angle, resample=Image.NEAREST, fillcolor=0)
                )
            proj = arr.sum(axis=1, dtype=np.float64)
            return float(np.var(proj))

        zero_score = score_at(0.0)

        best_angle, best_score = 0.0, zero_score
        for angle in np.arange(-max_abs_angle, max_abs_angle + 1.0, 1.0):
            s = score_at(float(angle))
            if s > best_score:
                best_score, best_angle = s, float(angle)

        if best_angle != 0.0:
            for angle in np.arange(best_angle - 0.75, best_angle + 0.8, 0.25):
                s = score_at(float(angle))
                if s > best_score:
                    best_score, best_angle = s, float(angle)

        # Only rotate for a real, worthwhile improvement.
        if abs(best_angle) >= 0.4 and best_score > zero_score * 1.02:
            img = img.rotate(
                best_angle, resample=Image.BICUBIC, expand=True,
                fillcolor=(255, 255, 255),
            )
    except Exception as exc:
        log.debug("Deskew skipped: %s", exc)
    return img


def _otsu_threshold(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    cum = np.cumsum(hist)
    cum_mean = np.cumsum(hist * np.arange(256))
    global_mean = cum_mean[-1] / total
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (global_mean * cum - cum_mean) ** 2 / (cum * (total - cum))
    between[~np.isfinite(between)] = 0
    return int(np.argmax(between))


# ---------------------------------------------------------------------------
# Contrast / sharpen / upscale
# ---------------------------------------------------------------------------

def enhance(img: Image.Image, is_photo: bool = False,
            min_long_edge: Optional[int] = None) -> Image.Image:
    """
    Clean up and upscale.

    Photos and screenshots want opposite treatment. A screenshot is already
    crisp: sharpening it just amplifies JPEG ringing around the small digits
    that carry the actual data ("03:06", "(38秒)"). A photo needs glare
    flattened and moiré softened *before* sharpening, or the sharpen locks the
    banding in.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    if min_long_edge is None:
        min_long_edge = (
            CONFIG.photo_min_long_edge if is_photo else CONFIG.screenshot_min_long_edge
        )

    if is_photo:
        cv2 = _cv2()
        if cv2 is not None:
            arr = np.array(img)
            # Order matters. Denoise BEFORE contrast work: photographing an
            # LCD produces moiré banding, and running CLAHE first amplifies
            # that banding into texture the detector reads as strokes.
            arr = cv2.bilateralFilter(arr, d=7, sigmaColor=55, sigmaSpace=55)
            # Then flatten uneven backlight / glare so autocontrast is not
            # driven by the brightest corner. Gentle clip limit: higher values
            # bring the (now suppressed) banding back.
            lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)
            l = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(l)
            arr = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)
            img = Image.fromarray(arr)
        img = ImageOps.autocontrast(img, cutoff=1)
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=110, threshold=3))
        img = ImageEnhance.Contrast(img).enhance(1.10)
    else:
        img = ImageOps.autocontrast(img, cutoff=0)
        img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=4))

    # Upscale when the long edge is small; detectors like ~1600-2600px, and
    # the "(38秒)" duration glyphs are only a few pixels tall on a 960px photo.
    long_edge = max(img.size)
    if long_edge < min_long_edge:
        scale = min_long_edge / long_edge
        new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
        img = img.resize(new_size, resample=Image.LANCZOS)

    return img


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def preprocess(path: Path) -> Tuple[Image.Image, dict]:
    """
    Run the full preprocessing pipeline. Returns the processed image plus a
    metadata dict describing what was done (useful for the processing log).
    """
    meta: dict = {"source_file": path.name, "steps": []}

    img = load_image(path)
    meta["steps"].append("exif_transpose")
    meta["original_size"] = list(img.size)

    is_shot, shot_meta = looks_like_screenshot(img)
    meta["classification"] = shot_meta
    meta["steps"].append("classify_" + shot_meta["verdict"])

    img, rot = detect_and_fix_rotation(img)
    if rot:
        meta["steps"].append(f"rotate_{rot}")

    rectified = False
    if not is_shot:
        img, screen_meta = rectify_phone_screen(img)
        if screen_meta.get("applied"):
            rectified = True
            meta["steps"].append("rectify_phone_screen")
            meta["screen_crop"] = screen_meta
        else:
            meta["screen_crop_skipped"] = screen_meta.get("reason", "")
    else:
        meta["screen_crop_skipped"] = "screenshot"

    # The perspective warp already removed the bulk of the rotation. Screenshots
    # have no skew at all. Only run the sweep when it can still help.
    if not is_shot:
        img = deskew(img, max_abs_angle=3.0 if rectified else 6.0)
        meta["steps"].append("deskew")
    else:
        meta["steps"].append("deskew_skipped_screenshot")

    img = enhance(img, is_photo=not is_shot)
    meta["steps"].append("enhance_photo" if not is_shot else "enhance_screenshot")
    meta["final_size"] = list(img.size)

    return img, meta


# Convenience for OCR engines that want bytes
def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
