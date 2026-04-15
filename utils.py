"""
Constants and shared helpers for the TTB Alcohol Label Verifier.

Kept deliberately small: a single source of truth for the official
government warning text, status/emoji mappings, and CSV serialization
for batch results.
"""

from __future__ import annotations

import io
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

import pandas as pd


# Exact required statement from 27 CFR § 16.21. The textual content is
# what we can verify from OCR output. Visual formatting (bold, font size,
# physical placement) cannot be checked from text alone — flagged in README.
GOVERNMENT_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women "
    "should not drink alcoholic beverages during pregnancy because of "
    "the risk of birth defects. (2) Consumption of alcoholic beverages "
    "impairs your ability to drive a car or operate machinery, and may "
    "cause health problems."
)

GOVERNMENT_WARNING_HEADER = "GOVERNMENT WARNING:"


# Status tiers used by matcher.FieldResult.status. Kept as plain strings
# (not an Enum) so the values flow cleanly through dataclasses, JSON,
# and Pandas without conversion ceremony.
STATUS_MATCH = "match"
STATUS_REVIEW = "review"
STATUS_MISMATCH = "mismatch"
STATUS_NOT_FOUND = "not_found"

# Overall verdicts (per image)
VERDICT_APPROVE = "APPROVE"
VERDICT_REVIEW = "REVIEW"
VERDICT_REJECT = "REJECT"

# Lower number = higher severity. Used to sort batch results so agents
# see problems first without scrolling past passing labels.
VERDICT_SEVERITY = {
    VERDICT_REJECT: 0,
    VERDICT_REVIEW: 1,
    VERDICT_APPROVE: 2,
}

STATUS_EMOJI = {
    STATUS_MATCH: "✅",
    STATUS_REVIEW: "⚠️",
    STATUS_MISMATCH: "❌",
    STATUS_NOT_FOUND: "❓",
}

VERDICT_EMOJI = {
    VERDICT_APPROVE: "✅",
    VERDICT_REVIEW: "⚠️",
    VERDICT_REJECT: "❌",
}

# Beverage types — informational in the prototype, but surfaced to the user
# and included in CSV output. Type-specific rule variations are noted as a
# future enhancement in the README.
BEVERAGE_TYPES = ("Distilled Spirits", "Wine", "Beer / Malt Beverage")


def get_match_emoji(status: str) -> str:
    """Return ✅/⚠️/❌/❓ for a FieldResult.status value."""
    return STATUS_EMOJI.get(status, "•")


def get_verdict_emoji(verdict: str) -> str:
    """Return the emoji for an overall image verdict."""
    return VERDICT_EMOJI.get(verdict, "•")


def format_confidence(score: float) -> str:
    """Render a 0.0–1.0 or 0–100 score as a human-friendly percentage."""
    if score is None:
        return "—"
    pct = score * 100 if score <= 1.0 else score
    return f"{pct:.0f}%"


def _to_dict(obj: Any) -> dict:
    """Coerce a dataclass or dict-ish object to a plain dict."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Cannot serialize {type(obj).__name__} to dict")


def results_to_csv(results: Iterable[Any]) -> bytes:
    """Flatten a list of VerificationResult objects into a CSV byte blob.

    One row per (image, field) pair so agents can filter/sort in Excel.
    Returns UTF-8 bytes suitable for st.download_button.
    """
    rows: list[dict] = []
    for vr in results:
        vr_d = _to_dict(vr)
        image_name = vr_d.get("image_name", "")
        verdict = vr_d.get("overall_verdict", "")
        ocr_conf = vr_d.get("ocr_confidence", None)
        proc_time = vr_d.get("processing_time", None)
        beverage = vr_d.get("beverage_type", "")
        for field in vr_d.get("fields", []):
            rows.append(
                {
                    "image": image_name,
                    "verdict": verdict,
                    "beverage_type": beverage,
                    "ocr_confidence_pct": (
                        round(ocr_conf * 100, 1) if ocr_conf is not None else ""
                    ),
                    "processing_time_s": (
                        round(proc_time, 2) if proc_time is not None else ""
                    ),
                    "field": field.get("field_name", ""),
                    "expected": field.get("expected", ""),
                    "extracted": field.get("extracted", ""),
                    "score": round(field.get("score", 0.0), 1),
                    "status": field.get("status", ""),
                    "notes": field.get("notes", ""),
                }
            )
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
