"""
OCR wrapper that supports multiple back-ends.

Engine priority (auto):
    1. PaddleOCR (best multilingual quality, handles JP/EN/VI well)
    2. EasyOCR (good fallback, GPU optional)
    3. Tesseract (always available, OK on clean screenshots)

Each engine returns a list of "lines" with per-line text + confidence.
We then re-group those lines into rows (call entries) downstream.

Version note
------------
PaddleOCR 3.x rewrote the constructor and the result format:
  * ``show_log`` was removed and now raises ``ValueError: Unknown argument``.
  * ``use_angle_cls`` was renamed ``use_textline_orientation``.
  * ``.ocr(img, cls=True)`` is deprecated in favour of ``.predict(img)``.
  * results are ``OCRResult`` mappings (``rec_texts`` / ``rec_scores`` /
    ``rec_polys``) instead of ``[[box, (text, conf)], ...]``.
Both the 2.x and 3.x shapes are handled below.
"""

from __future__ import annotations

import runtime_env  # noqa: F401  -- must come before paddle/torch/cv2 imports

import logging
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from config import CONFIG

log = logging.getLogger(__name__)


class OcrEngineError(RuntimeError):
    """Raised when an OCR engine cannot be used."""


@dataclass
class OcrLine:
    text: str
    confidence: float           # 0..1
    box: Tuple[float, float, float, float]  # x_min, y_min, x_max, y_max


# ---------------------------------------------------------------------------
# Deep-learning framework guard
#
# paddlepaddle and torch each bundle their own Intel OpenMP runtime. Loading
# both into one process on Windows is what produces the phantom
# KeyboardInterrupt (see runtime_env.py). Never do it.
# ---------------------------------------------------------------------------

_ENGINE_FRAMEWORK = {"paddle": "paddle", "easyocr": "torch", "tesseract": None}
_loaded_framework: Optional[str] = None


def _check_framework(engine: str) -> None:
    global _loaded_framework
    needed = _ENGINE_FRAMEWORK.get(engine)
    if needed is None:
        return
    already = _loaded_framework
    if already is None:
        # Something else may have imported one already.
        if "paddle" in sys.modules:
            already = "paddle"
        elif "torch" in sys.modules:
            already = "torch"
    if already is not None and already != needed:
        raise OcrEngineError(
            f"refusing to load {needed!r} because {already!r} is already "
            f"loaded in this process. Two OpenMP runtimes in one process "
            f"cause crashes and phantom KeyboardInterrupts on Windows. "
            f"Re-run with --ocr-engine {engine} to use it as the primary "
            f"engine instead of a fallback."
        )
    _loaded_framework = needed


# ---------------------------------------------------------------------------
# Engine: PaddleOCR
# ---------------------------------------------------------------------------

_paddle_engine = None  # cache instance (heavy to construct)
_paddle_api: Optional[int] = None  # 2 or 3


def _paddle_lang() -> str:
    """Map our language hints onto a PaddleOCR language code."""
    langs = [str(x).lower() for x in CONFIG.ocr_languages]
    if "japan" in langs or "ja" in langs or "jpn" in langs:
        # PP-OCRv5's "japan" recognizer also covers latin letters + digits,
        # which is what the call-log rows actually contain.
        return "japan"
    if "vi" in langs or "vie" in langs:
        return "vi"
    return "en"


def _build_paddle_engine():
    """Construct a PaddleOCR instance for whichever major version is installed."""
    global _paddle_api
    from paddleocr import PaddleOCR  # lazy import

    try:
        from paddleocr import __version__ as _pv
    except Exception:
        _pv = "0"
    major = int(str(_pv).split(".")[0] or 0)

    lang = _paddle_lang()

    if major >= 3:
        _paddle_api = 3
        # 3.x: no show_log; angle classifier renamed.
        # The document orientation classifier is loaded here, but each
        # predict() call decides whether to use it: only landscape images
        # are classified (see _wants_doc_orientation). Without pytesseract
        # there is no OSD, so a sideways photo would otherwise go through
        # un-rotated and produce no usable lines. Unwarping stays off --
        # image_preprocess already perspective-rectifies the screen.
        engine = PaddleOCR(
            lang=None if CONFIG.paddle_rec_model_name else lang,
            text_recognition_model_name=CONFIG.paddle_rec_model_name,
            text_detection_model_name=CONFIG.paddle_det_model_name,
            use_textline_orientation=CONFIG.paddle_use_textline_orientation,
            use_doc_orientation_classify=CONFIG.paddle_use_doc_orientation,
            use_doc_unwarping=False,
            text_det_limit_side_len=CONFIG.paddle_det_limit_side_len,
            text_det_limit_type="max",
            text_det_thresh=CONFIG.paddle_det_thresh,
            text_det_box_thresh=CONFIG.paddle_det_box_thresh,
            text_det_unclip_ratio=CONFIG.paddle_det_unclip_ratio,
            text_rec_score_thresh=CONFIG.paddle_rec_score_thresh,
            # Common args -> parse_common_args(). See config.py: oneDNN is
            # broken under the PIR executor on Windows CPU.
            enable_mkldnn=CONFIG.paddle_enable_mkldnn,
            cpu_threads=CONFIG.paddle_cpu_threads,
        )
    else:
        _paddle_api = 2
        engine = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

    log.info("PaddleOCR %s initialised (api=v%d, lang=%s)", _pv, _paddle_api, lang)
    return engine


def _paddle_lines_from_v3(result: Sequence[Any]) -> List[OcrLine]:
    """Parse PaddleOCR 3.x ``predict()`` output."""
    lines: List[OcrLine] = []
    for page in result or []:
        # OCRResult behaves like a mapping; some builds wrap it in .json/.res
        data: Dict[str, Any] = {}
        if isinstance(page, dict) and ("rec_texts" in page or "res" in page):
            data = page
        elif hasattr(page, "json"):
            # OCRResult subclasses dict in some builds but keeps the payload
            # under .json["res"], so check the mapping for real keys first and
            # only then fall through to .json.
            raw = page.json
            if isinstance(raw, dict):
                data = raw
        elif isinstance(page, dict):
            data = page
        if isinstance(data.get("res"), dict):
            data = data["res"]

        texts = data.get("rec_texts") or []
        scores = data.get("rec_scores") or []
        polys = data.get("rec_polys")
        if polys is None:
            polys = data.get("dt_polys") or data.get("rec_boxes") or []

        for i, text in enumerate(texts):
            try:
                conf = float(scores[i]) if i < len(scores) else 0.0
            except (TypeError, ValueError):
                conf = 0.0
            box = (0.0, 0.0, 0.0, 0.0)
            if i < len(polys):
                pts = np.asarray(polys[i], dtype=float).reshape(-1, 2)
                if pts.size:
                    box = (
                        float(pts[:, 0].min()), float(pts[:, 1].min()),
                        float(pts[:, 0].max()), float(pts[:, 1].max()),
                    )
            lines.append(OcrLine(text=str(text), confidence=conf, box=box))
    return lines


def _paddle_lines_from_v2(result: Sequence[Any]) -> List[OcrLine]:
    """Parse PaddleOCR 2.x ``ocr()`` output: [[[box, (text, conf)], ...]]."""
    lines: List[OcrLine] = []
    for page in result or []:
        if not page:
            continue
        for entry in page:
            try:
                box, (text, conf) = entry
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                lines.append(
                    OcrLine(
                        text=str(text),
                        confidence=float(conf),
                        box=(min(xs), min(ys), max(xs), max(ys)),
                    )
                )
            except Exception:
                continue
    return lines


def _is_onednn_pir_bug(exc: BaseException) -> bool:
    """
    Recognise the paddle 3.x Windows-CPU oneDNN failure:

        NotImplementedError: (Unimplemented)
        ConvertPirAttribute2RuntimeAttribute not support
        [pir::ArrayAttribute<pir::DoubleAttribute>]
        (at ...\\new_executor\\instruction\\onednn\\onednn_instruction.cc:118)

    The PIR executor cannot lower a double-array attribute onto the oneDNN
    kernels, so every single inference dies. Rebuilding with enable_mkldnn=False
    avoids the oneDNN path entirely.
    """
    text = f"{exc.__class__.__name__}: {exc}"
    return "ConvertPirAttribute2RuntimeAttribute" in text or "onednn" in text.lower()


def _wants_doc_orientation(img: Image.Image) -> bool:
    """
    Classify page orientation only for landscape images (wider than tall).

    Call-log screens are portrait, so a portrait image is already upright.
    Classifying it anyway let the model turn an upright phone photo by 90°,
    and that image then yielded no calls at all. A landscape image is a
    sideways screen and still needs the classifier to turn it upright. The
    cost: an upside-down portrait photo is no longer flipped.
    """
    w, h = img.size
    return CONFIG.paddle_use_doc_orientation and w > h


def _ocr_paddle(img: Image.Image) -> List[OcrLine]:
    global _paddle_engine
    _check_framework("paddle")

    if _paddle_engine is None:
        _paddle_engine = _build_paddle_engine()

    classify = _wants_doc_orientation(img)
    log.debug("doc orientation classify=%s (%dx%d)", classify, *img.size)

    arr = np.array(img.convert("RGB"))
    # PaddleOCR expects BGR when handed a raw ndarray.
    arr = arr[:, :, ::-1].copy()

    try:
        return _paddle_predict(arr, classify)
    except NotImplementedError as exc:
        if not (_is_onednn_pir_bug(exc) and CONFIG.paddle_enable_mkldnn):
            raise
        # Self-heal: drop oneDNN and rebuild once, then keep using the working
        # engine for the rest of the batch instead of failing every image.
        log.warning(
            "PaddleOCR hit the oneDNN/PIR attribute bug. Rebuilding with "
            "enable_mkldnn=False and retrying. Set paddle_enable_mkldnn=False "
            "in config.py to skip this on future runs."
        )
        CONFIG.paddle_enable_mkldnn = False
        _paddle_engine = _build_paddle_engine()
        return _paddle_predict(arr, classify)


def _paddle_predict(arr: np.ndarray, classify: bool) -> List[OcrLine]:
    if _paddle_api == 3:
        result = _paddle_engine.predict(arr, use_doc_orientation_classify=classify)
        lines = _paddle_lines_from_v3(result)
        if not lines:
            # Some 3.x builds still return the legacy nested list.
            lines = _paddle_lines_from_v2(result)
        return lines

    result = _paddle_engine.ocr(arr, cls=True)
    return _paddle_lines_from_v2(result)


# ---------------------------------------------------------------------------
# Engine: EasyOCR
# ---------------------------------------------------------------------------

_easy_reader = None


def _ocr_easy(img: Image.Image) -> List[OcrLine]:
    global _easy_reader
    _check_framework("easyocr")
    import easyocr

    if _easy_reader is None:
        # EasyOCR cannot combine Japanese + Vietnamese in one reader; pick the
        # most likely language pair for screenshots and fall back per-image.
        langs = ["ja", "en"] if "japan" in CONFIG.ocr_languages else ["en"]
        _easy_reader = easyocr.Reader(langs, gpu=False, verbose=False)

    arr = np.array(img.convert("RGB"))
    result = _easy_reader.readtext(arr, detail=1)

    lines: List[OcrLine] = []
    for box, text, conf in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append(
            OcrLine(
                text=str(text),
                confidence=float(conf),
                box=(min(xs), min(ys), max(xs), max(ys)),
            )
        )
    return lines


# ---------------------------------------------------------------------------
# Engine: Tesseract (always-available fallback)
# ---------------------------------------------------------------------------

def _ocr_tesseract(img: Image.Image) -> List[OcrLine]:
    import pytesseract

    data = pytesseract.image_to_data(
        img,
        lang=CONFIG.tesseract_langs,
        output_type=pytesseract.Output.DICT,
        config="--oem 1 --psm 6",
    )

    lines_by_block: dict = {}
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i]) / 100.0
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0:
            conf = 0.0
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        entry = lines_by_block.setdefault(
            key,
            {"texts": [], "confs": [], "x1": x, "y1": y, "x2": x + w, "y2": y + h},
        )
        entry["texts"].append(text)
        entry["confs"].append(conf)
        entry["x1"] = min(entry["x1"], x)
        entry["y1"] = min(entry["y1"], y)
        entry["x2"] = max(entry["x2"], x + w)
        entry["y2"] = max(entry["y2"], y + h)

    lines: List[OcrLine] = []
    for entry in lines_by_block.values():
        avg_conf = sum(entry["confs"]) / max(1, len(entry["confs"]))
        lines.append(
            OcrLine(
                text=" ".join(entry["texts"]),
                confidence=avg_conf,
                box=(entry["x1"], entry["y1"], entry["x2"], entry["y2"]),
            )
        )
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_ENGINE_FUNCS = {
    "paddle": _ocr_paddle,
    "easyocr": _ocr_easy,
    "tesseract": _ocr_tesseract,
}

_engine_in_use: Optional[str] = None


def run_ocr(img: Image.Image, engine: Optional[str] = None) -> List[OcrLine]:
    """
    Run OCR with the configured engine.

    Once an engine has succeeded, every later image uses that same engine —
    silently switching engines mid-batch produced inconsistent results and, on
    Windows, loaded a second OpenMP runtime.

    With ``CONFIG.ocr_strict_engine = True`` a failure of the requested engine
    is fatal (with a full traceback) instead of quietly degrading to a worse
    engine. That quiet degradation is why photo OCR looked broken: PaddleOCR
    could not construct, and every image was silently handled by EasyOCR.
    """
    global _engine_in_use

    primary = engine or _engine_in_use or CONFIG.ocr_engine
    order = [primary]
    if not CONFIG.ocr_strict_engine:
        for fallback in ("paddle", "easyocr", "tesseract"):
            if fallback not in order:
                order.append(fallback)

    last_err: Optional[BaseException] = None
    for name in order:
        func = _ENGINE_FUNCS.get(name)
        if func is None:
            log.warning("Unknown OCR engine %r, skipping", name)
            continue
        try:
            lines = func(img)
        except KeyboardInterrupt:
            # Never swallow this: a real Ctrl+C must stop the run immediately,
            # and a phantom one must be visible rather than mislabelled as an
            # "engine failure" that triggers a fallback.
            raise
        except ImportError as exc:
            log.warning("OCR engine %s not installed: %s", name, exc)
            last_err = exc
        except OcrEngineError as exc:
            log.warning("OCR engine %s unavailable: %s", name, exc)
            last_err = exc
        except Exception as exc:
            # Log the real traceback. The old code printed only str(exc), so a
            # constructor TypeError/ValueError looked like a one-line shrug.
            log.warning(
                "OCR engine %s failed: %s: %s\n%s",
                name, exc.__class__.__name__, exc, traceback.format_exc(),
            )
            last_err = exc
        else:
            if _engine_in_use != name:
                log.info("OCR engine locked to: %s", name)
                _engine_in_use = name
            log.debug("OCR %s -> %d lines", name, len(lines))
            return lines

    if CONFIG.ocr_strict_engine:
        raise OcrEngineError(
            f"OCR engine {primary!r} failed and ocr_strict_engine is on. "
            f"Fix the engine or set CONFIG.ocr_strict_engine = False."
        ) from last_err

    raise OcrEngineError(
        "No OCR engine available. Install one of: paddleocr, easyocr, pytesseract."
    ) from last_err


def active_engine() -> Optional[str]:
    """Name of the engine that last produced results (None before first run)."""
    return _engine_in_use


def reset_engines() -> None:
    """Drop cached engine instances (used by tests)."""
    global _paddle_engine, _easy_reader, _engine_in_use, _loaded_framework
    _paddle_engine = None
    _easy_reader = None
    _engine_in_use = None
    _loaded_framework = None


# ---------------------------------------------------------------------------
# Row grouping
# ---------------------------------------------------------------------------

def group_lines_into_rows(lines: List[OcrLine],
                          y_tolerance_frac: Optional[float] = None
                          ) -> List[List[OcrLine]]:
    """
    Group OCR lines that share roughly the same y-band into "rows".
    Each row corresponds to (we hope) one call-log entry.

    The band is grown from the *median* line height rather than each line's own
    height. A single tall glyph (an avatar circle, a banner) used to inflate
    the band and swallow the next call's row, merging two calls into one.
    """
    if not lines:
        return []

    if y_tolerance_frac is None:
        y_tolerance_frac = CONFIG.row_group_y_tolerance

    heights = [max(1.0, ln.box[3] - ln.box[1]) for ln in lines]
    median_h = float(np.median(heights))

    enriched = []
    for ln, h in zip(lines, heights):
        cy = (ln.box[1] + ln.box[3]) / 2.0
        # Clamp so an outsized element cannot dominate the band width.
        enriched.append((cy, min(h, median_h * 1.5), ln))
    enriched.sort(key=lambda t: t[0])

    rows: List[List[OcrLine]] = []
    current: List[Tuple[float, float, OcrLine]] = []
    band_top = band_bot = 0.0

    for cy, h, ln in enriched:
        if not current:
            current = [(cy, h, ln)]
            band_top, band_bot = cy - h * y_tolerance_frac, cy + h * y_tolerance_frac
            continue
        if band_top <= cy <= band_bot:
            current.append((cy, h, ln))
            band_top = min(band_top, cy - h * y_tolerance_frac)
            band_bot = max(band_bot, cy + h * y_tolerance_frac)
        else:
            current.sort(key=lambda t: t[2].box[0])
            rows.append([t[2] for t in current])
            current = [(cy, h, ln)]
            band_top, band_bot = cy - h * y_tolerance_frac, cy + h * y_tolerance_frac

    if current:
        current.sort(key=lambda t: t[2].box[0])
        rows.append([t[2] for t in current])

    return rows


def row_text(row: List[OcrLine]) -> str:
    return " ".join(ln.text for ln in row).strip()


def row_line_spans(row: List[OcrLine]) -> List[Tuple[int, int, float]]:
    """
    (begin, end, confidence) of each line's text inside row_text(row), so the
    parser can tell which OCR line a field was read from.
    """
    joined = " ".join(ln.text for ln in row)
    lead = len(joined) - len(joined.lstrip())
    spans: List[Tuple[int, int, float]] = []
    pos = 0
    for ln in row:
        begin = max(0, pos - lead)
        spans.append((begin, max(begin, pos + len(ln.text) - lead), ln.confidence))
        pos += len(ln.text) + 1
    return spans


def row_confidence(row: List[OcrLine]) -> float:
    if not row:
        return 0.0
    return sum(ln.confidence for ln in row) / len(row)
