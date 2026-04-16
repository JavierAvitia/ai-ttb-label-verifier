"""
OCR pipeline: OpenCV preprocessing followed by EasyOCR text extraction.

Why these choices:
  * EasyOCR is self-contained — no API keys, no outbound network calls.
    This sidesteps the federal firewall issues that killed the prior
    cloud-vision vendor pilot.
  * OpenCV preprocessing (grayscale + CLAHE + light blur + opt. deskew)
    is cheap and noticeably improves OCR on labels shot at angles,
    under glare, or with low contrast — Jenny's real-world concern.

The Reader is loaded lazily and cached; first use downloads the
detection/recognition models (~100 MB) then reuses them for every
subsequent call in the process.

v2 changes:
  * Noise filtering — drops single-char / low-confidence fragments
    that pollute downstream matching.
  * Multi-pass OCR — runs on both preprocessed AND raw image, merges
    unique lines. Small-font text (government warning) sometimes
    survives better on the unprocessed image.
  * Adaptive CLAHE — only applied when image contrast is low
    (gray.std() < 60). Clean labels lose detail under unconditional
    CLAHE.
  * Upscaling — images with long edge < 2000 px scaled to 2400 px
    before OCR, helping tiny warning text on real bottle photos.
  * rotation_info=[90,180,270] — recovers vertical text on real
    cylindrical bottle labels.
"""

from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np
from PIL import Image

# easyocr is imported lazily inside _get_reader() so that importing this
# module is cheap (matters for unit tests and CLI tools that don't run OCR).
_READER = None

# ---------------------------------------------------------------------------
# Noise filtering thresholds
# ---------------------------------------------------------------------------
# Lines shorter than this are almost always OCR artifacts from borders,
# decorative elements, or texture patterns.
_MIN_LINE_LENGTH = 2
# Lines below this confidence are noise. Genuine text rarely drops
# below 30%; decorative fragments cluster in the 3–25% range.
_MIN_CONFIDENCE = 0.25

# ---------------------------------------------------------------------------
# Upscaling constants
# ---------------------------------------------------------------------------
_MIN_LONG_EDGE = 2000
_TARGET_LONG_EDGE = 2400


def _get_reader():
    """Lazy singleton for easyocr.Reader. Loading is the expensive step."""
    global _READER
    if _READER is None:
        import easyocr  # local import — heavy
        # gpu=False keeps this portable to Streamlit Cloud / generic CPU envs.
        _READER = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _READER


def _to_cv2(image: "Image.Image | np.ndarray | str") -> np.ndarray:
    """Accept a PIL.Image, numpy array, or path; return a BGR cv2 image."""
    if isinstance(image, str):
        arr = cv2.imread(image, cv2.IMREAD_COLOR)
        if arr is None:
            raise ValueError(f"Could not read image at path: {image}")
        return arr
    if isinstance(image, Image.Image):
        rgb = np.array(image.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if isinstance(image, np.ndarray):
        return image
    raise TypeError(f"Unsupported image type: {type(image).__name__}")


def _upscale_if_small(img: np.ndarray) -> np.ndarray:
    """Upscale to ~_TARGET_LONG_EDGE if long edge is below _MIN_LONG_EDGE."""
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge >= _MIN_LONG_EDGE:
        return img
    scale = _TARGET_LONG_EDGE / float(long_edge)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


def _deskew(gray: np.ndarray, angle_threshold: float = 5.0) -> np.ndarray:
    """Best-effort deskew. Only rotates if estimated angle exceeds threshold."""
    try:
        inv = cv2.bitwise_not(gray)
        _, bw = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        coords = np.column_stack(np.where(bw > 0))
        if coords.size == 0:
            return gray
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) < angle_threshold:
            return gray
        h, w = gray.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        return cv2.warpAffine(
            gray, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
    except Exception:
        return gray


def preprocess(image: "Image.Image | np.ndarray | str") -> np.ndarray:
    """Run the standard preprocessing pipeline. Returns a single-channel image.

    Pipeline:
        1. Upscale if long edge < 2000 px (helps tiny warning text)
        2. To grayscale
        3. CLAHE contrast enhancement only on low-contrast images
           (gray.std() < 60). Clean labels lose detail under CLAHE.
        4. Mild Gaussian blur (3x3) on enhanced path only
        5. Optional deskew if estimated angle > 5 degrees
    """
    bgr = _to_cv2(image)
    bgr = _upscale_if_small(bgr)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < 60.0:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return _deskew(gray)


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------


def _filter_noise(lines: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Remove OCR artifacts that would pollute downstream matching.

    Drops:
      - Single-character fragments (borders, decorative elements)
      - Very low confidence lines (< 25%)
      - Short pure numeric/symbol fragments from textures
    """
    filtered: list[tuple[str, float]] = []
    for text, conf in lines:
        if len(text) < _MIN_LINE_LENGTH:
            continue
        if conf < _MIN_CONFIDENCE:
            continue
        stripped = text.strip("[](){}<>|/\\!@#$%^&*~`")
        if len(stripped) <= 2 and not any(c.isalpha() for c in stripped):
            continue
        filtered.append((text, conf))
    return filtered


# ---------------------------------------------------------------------------
# Multi-pass merge
# ---------------------------------------------------------------------------


def _merge_passes(
    pass1: list[tuple[str, float]],
    pass2: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Merge results from two OCR passes, deduplicating near-identical lines.

    Keeps the higher-confidence version when two lines are substantially
    similar (containment check). This lets us combine:
      - preprocessed pass: better on high-contrast brand/class text
      - raw pass: better on small-font warning text and fine print
    """
    merged = list(pass1)
    existing_lower = [t.lower().strip() for t, _ in merged]

    for text, conf in pass2:
        text_lower = text.lower().strip()
        is_dup = False
        for i, existing in enumerate(existing_lower):
            if text_lower in existing or existing in text_lower:
                if conf > merged[i][1]:
                    merged[i] = (text, conf)
                    existing_lower[i] = text_lower
                is_dup = True
                break
        if not is_dup:
            merged.append((text, conf))
            existing_lower.append(text_lower)

    return merged


# ---------------------------------------------------------------------------
# Single OCR pass
# ---------------------------------------------------------------------------


def _run_ocr_pass(
    prepared: np.ndarray,
    *,
    use_rotation: bool = False,
    paragraph: bool = False,
) -> list[tuple[str, float]]:
    """Run EasyOCR on a single image array. Returns [(text, confidence), ...].

    Parameters:
        use_rotation: enable rotation_info=[90,180,270] for vertical text
            on real bottle labels. Adds ~3× processing time.
        paragraph: enable paragraph grouping (reduces fragmentation).
    """
    reader = _get_reader()
    kwargs: dict = {
        "text_threshold": 0.3,
        "low_text": 0.3,
    }
    if use_rotation:
        kwargs["rotation_info"] = [90, 180, 270]
    if paragraph:
        kwargs["paragraph"] = True
        kwargs["width_ths"] = 0.7
    else:
        kwargs["paragraph"] = False

    raw = reader.readtext(prepared, **kwargs)
    results: list[tuple[str, float]] = []
    for entry in raw:
        if len(entry) >= 3:
            text = (entry[1] or "").strip()
            conf = float(entry[2])
        elif len(entry) >= 2:
            # paragraph mode sometimes drops confidence
            text = (entry[1] if isinstance(entry[1], str) else str(entry[1])).strip()
            conf = 0.5
        else:
            continue
        if text:
            results.append((text, conf))
    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def extract_text(
    image: "Image.Image | np.ndarray | str",
    *,
    skip_preprocess: bool = False,
    multi_pass: bool = True,
) -> dict:
    """Run preprocessing + OCR on an image.

    Returns a dict with:
        full_text:       str          — every recognized line joined by '\\n'
        lines:           list[str]
        confidences:     list[float]  — per-line confidence (0.0–1.0)
        avg_confidence:  float        — mean of per-line confidences (0.0 if empty)
        processing_time: float        — seconds elapsed including preprocessing
        raw_results:     list         — filtered (text, conf) tuples for debugging

    Multi-pass mode (default) runs OCR twice — once on the preprocessed
    image and once on the raw BGR image — then merges the results. This
    recovers small-font text (like the government warning) that CLAHE
    or grayscale conversion can wash out.
    """
    t0 = time.perf_counter()
    bgr = _to_cv2(image)

    if skip_preprocess:
        all_lines = _run_ocr_pass(bgr)
    else:
        # Pass 1: preprocessed (upscale + adaptive CLAHE + deskew).
        # No rotation_info — rotation is only useful for vertical text
        # on real cylindrical bottle labels and actively hurts on clean
        # horizontal labels (fragments "12 FL OZ" into single chars).
        preprocessed = preprocess(image)
        pass1 = _run_ocr_pass(preprocessed)

        if multi_pass:
            # Pass 2: raw BGR image. Small text and fine print often
            # survive better without grayscale + CLAHE. No paragraph
            # mode — it aggressively merges all text into mega-lines
            # which breaks the containment-based merge dedup.
            pass2 = _run_ocr_pass(bgr)
            all_lines = _merge_passes(pass1, pass2)
        else:
            all_lines = pass1

    # Filter noise AFTER merging both passes.
    clean_lines = _filter_noise(all_lines)

    lines = [t for t, _ in clean_lines]
    confs = [c for _, c in clean_lines]
    avg = float(sum(confs) / len(confs)) if confs else 0.0

    return {
        "full_text": "\n".join(lines),
        "lines": lines,
        "confidences": confs,
        "avg_confidence": avg,
        "processing_time": time.perf_counter() - t0,
        "raw_results": clean_lines,
    }


def warm_up() -> None:
    """Force model load. Useful to call once at app start to avoid a
    user-visible pause on the first OCR request."""
    _get_reader()
