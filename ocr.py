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


def _deskew(gray: np.ndarray, angle_threshold: float = 5.0) -> np.ndarray:
    """Best-effort deskew. Only rotates if estimated angle exceeds threshold.

    Uses the minimum-area rectangle around dark pixels (text). For most
    photos this lands within a degree or two of the true skew angle.
    Silently returns the input on failure — deskew is a nice-to-have.
    """
    try:
        # Invert so text is the "foreground" for findNonZero.
        inv = cv2.bitwise_not(gray)
        # Threshold to isolate text-ish pixels.
        _, bw = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        coords = np.column_stack(np.where(bw > 0))
        if coords.size == 0:
            return gray
        angle = cv2.minAreaRect(coords)[-1]
        # cv2 returns angles in [-90, 0); normalize to a small correction.
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
        1. To grayscale
        2. CLAHE contrast enhancement (clip=2.0, tile=8x8)
        3. Mild Gaussian blur (3x3) to suppress salt-and-pepper noise
        4. Optional deskew if estimated angle > 5 degrees
    """
    bgr = _to_cv2(image)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blurred = cv2.GaussianBlur(enhanced, (3, 3), 0)
    return _deskew(blurred)


def extract_text(
    image: "Image.Image | np.ndarray | str",
    *,
    skip_preprocess: bool = False,
) -> dict:
    """Run preprocessing + OCR on an image.

    Returns a dict with:
        full_text:    str   — every recognized line joined by '\\n'
        lines:        list[str]
        confidences:  list[float]   — per-line confidence (0.0–1.0)
        avg_confidence: float        — mean of per-line confidences (0.0 if empty)
        processing_time: float       — seconds elapsed including preprocessing
        raw_results: list            — EasyOCR's raw output, kept for debugging

    `skip_preprocess` is escape-valve for evaluation/debugging: lets us
    measure the preprocessing contribution to accuracy.
    """
    t0 = time.perf_counter()
    if skip_preprocess:
        prepared = _to_cv2(image)
        # EasyOCR is happy with BGR or grayscale numpy arrays.
    else:
        prepared = preprocess(image)
    reader = _get_reader()
    raw = reader.readtext(prepared)
    lines: list[str] = []
    confs: list[float] = []
    for entry in raw:
        # EasyOCR returns (bbox, text, confidence) tuples.
        if len(entry) >= 3:
            _, text, conf = entry[0], entry[1], entry[2]
            text = (text or "").strip()
            if text:
                lines.append(text)
                confs.append(float(conf))
    avg = float(sum(confs) / len(confs)) if confs else 0.0
    return {
        "full_text": "\n".join(lines),
        "lines": lines,
        "confidences": confs,
        "avg_confidence": avg,
        "processing_time": time.perf_counter() - t0,
        "raw_results": raw,
    }


def warm_up() -> None:
    """Force model load. Useful to call once at app start to avoid a
    user-visible pause on the first OCR request."""
    _get_reader()
