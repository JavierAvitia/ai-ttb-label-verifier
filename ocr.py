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


# Government-warning text on real labels is often the smallest type on the
# bottle (sometimes rotated 90° in a side strip). EasyOCR's recognition
# accuracy degrades sharply once character height drops below ~25 px.
# Upscaling images whose long edge sits under this threshold to ~2400 px
# before OCR runs reliably promotes those small regions into the band
# where EasyOCR can read them — at the cost of a modest extra ~0.5–1.0s
# per image, which is well inside Sarah's <5s feedback budget.
_MIN_LONG_EDGE = 2000
_TARGET_LONG_EDGE = 2400


def _upscale_if_small(img: np.ndarray) -> np.ndarray:
    """Upscale `img` (cv2 ndarray) to ~_TARGET_LONG_EDGE if its long edge
    is below _MIN_LONG_EDGE. INTER_CUBIC keeps strokes clean; no-op when
    the input is already large enough."""
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge >= _MIN_LONG_EDGE:
        return img
    scale = _TARGET_LONG_EDGE / float(long_edge)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


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
        1. Upscale if long edge < 2000 px (helps tiny warning text)
        2. To grayscale
        3. CLAHE contrast enhancement only on low-contrast images
           (gray.std() < 60). Already-clean labels lose detail when CLAHE
           pushes bright areas to pure white — empirically this hurt
           OCR on perfect_label/brand_caps_mismatch in our eval set.
        4. Mild Gaussian blur (3x3) to suppress salt-and-pepper noise on
           the enhanced path only — clean inputs don't need denoising.
        5. Optional deskew if estimated angle > 5 degrees
    """
    bgr = _to_cv2(image)
    bgr = _upscale_if_small(bgr)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # Adaptive contrast: leave already-punchy labels alone; rescue washed-out
    # ones. Threshold picked empirically — perfect_label std ≈ 70+, while
    # low_contrast hovers around 30–45.
    if float(gray.std()) < 60.0:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return _deskew(gray)


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
    # Detection thresholds: defaults (text_threshold=0.7, low_text=0.4) skip
    # the small-font government warning that sits in a vertical strip on most
    # of our test labels. Lowering them brings that text into the candidate
    # set; the matcher's per-line confidence still surfaces low-quality reads.
    #
    # rotation_info=[90, 180, 270] is the critical piece for cylindrical
    # bottle labels: the government warning is almost always printed
    # rotated 90° along a side strip. Without this, EasyOCR detects the
    # region but recognizes it as garbage (I4HI, UHHH, BH, …); with it,
    # we recover the real words ('DRINK', 'ALCOHOLIC', 'PREGNANCY', etc.).
    raw = reader.readtext(
        prepared,
        text_threshold=0.3,
        low_text=0.3,
        paragraph=False,
        rotation_info=[90, 180, 270],
    )
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
