"""
Field extraction + per-field comparison logic.

Two clearly separated concerns:
    extract_fields(ocr_text, ocr_lines) -> dict
        Parses raw OCR output into structured field candidates.
    validate_fields(extracted, expected) -> list[FieldResult]
        Compares the candidates to the application data.

Splitting them this way means each half is testable in isolation:
extract_fields can be exercised against canned OCR strings without
invoking matching, and validate_fields can be exercised with hand-built
inputs without running OCR at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from utils import (
    GOVERNMENT_WARNING_HEADER,
    GOVERNMENT_WARNING_TEXT,
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_NOT_FOUND,
    STATUS_REVIEW,
    VERDICT_APPROVE,
    VERDICT_REJECT,
    VERDICT_REVIEW,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FieldResult:
    field_name: str
    expected: str
    extracted: str
    score: float            # 0–100
    status: str             # one of STATUS_*
    notes: str = ""


@dataclass
class VerificationResult:
    image_name: str
    fields: list[FieldResult]
    overall_verdict: str            # one of VERDICT_*
    ocr_confidence: float = 0.0     # mean per-line OCR confidence (0–1)
    processing_time: float = 0.0    # seconds, end-to-end
    beverage_type: str = ""
    raw_ocr_text: str = ""


# ---------------------------------------------------------------------------
# Per-field thresholds
# ---------------------------------------------------------------------------
# Brand / class / country: typical printed text — confident thresholds.
# Producer/bottler: multi-line addresses fragment under OCR — lower bar.
# ABV / net contents: numeric — exact match required after parsing.

_FUZZY_THRESHOLDS = {
    "brand": (85, 70),
    "class_type": (85, 70),
    "producer": (80, 65),
    "country_of_origin": (85, 70),
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

# Match "45%", "45.5 %", "45 % alc", etc.
_ABV_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# Match "(90 proof)" / "90 Proof"
_PROOF_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*[Pp]roof")
# Match "750 mL", "1.75 L", "12 fl oz", "12 fl. oz", "355 ml".
# Negative lookbehind on `(` and digits guards against matching the "(1)"
# numbering inside the government warning ("(1) According to…") combined
# with a stray L/oz fragment elsewhere in the OCR text.
_NET_CONTENTS_RE = re.compile(
    r"(?<![\(\d.])(\d+(?:\.\d+)?)\s*(ml|l|fl\.?\s*oz|oz)\b",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    """Lowercase, collapse whitespace. Used for substring containment checks."""
    return " ".join((s or "").lower().split())


def extract_abv(text: str) -> Optional[dict]:
    """Pull the first ABV percentage from text. Also captures proof if present.

    Returns {'percent': float, 'proof': float|None, 'raw': str} or None.
    """
    if not text:
        return None
    # Prefer percent values that occur near alcohol-related context to
    # avoid grabbing percentages from unrelated marketing copy.
    candidates = []
    for m in _ABV_PERCENT_RE.finditer(text):
        pct = float(m.group(1))
        if 0 < pct <= 100:
            window = text[max(0, m.start() - 25): m.end() + 25].lower()
            score = 0
            for kw in ("alc", "abv", "vol", "alcohol"):
                # Word boundary avoids false hits like "NVol" matching "vol"
                if re.search(rf"\b{kw}\b", window):
                    score += 1
            has_decimal = 1 if "." in m.group(1) else 0
            candidates.append((score, has_decimal, m.start(), pct, m.group(0)))
    if not candidates:
        return None
    # Highest context score, prefer decimal ABV (6.5% over 5%), then earliest.
    candidates.sort(key=lambda t: (-t[0], -t[1], t[2]))
    _, _, _, pct, raw = candidates[0]
    proof_m = _PROOF_RE.search(text)
    proof = float(proof_m.group(1)) if proof_m else None
    return {"percent": pct, "proof": proof, "raw": raw}


def extract_net_contents(text: str) -> Optional[dict]:
    """Pull the most-specific net-contents value (volume + unit) from text.

    OCR noise frequently produces stray short matches like 'oL' or '1L'
    fragmented out of the warning text or background graphics. We collect
    every regex hit and return the one with the longest raw span — the
    real label value ('12 FL OZ', '750 ml') is almost always longer than
    the spurious fragments.
    """
    if not text:
        return None
    # Common OCR error: zero for capital-O in unit strings (e.g. "12 FL 0Z").
    corrected = re.sub(r"(?<=\d\s)FL\s*0Z", "FL OZ", text, flags=re.IGNORECASE)
    corrected = re.sub(r"\b0Z\b", "OZ", corrected)
    matches = list(_NET_CONTENTS_RE.finditer(corrected))
    if not matches:
        return None
    # Longest raw span wins; ties broken by earliest occurrence.
    best = max(matches, key=lambda m: (len(m.group(0)), -m.start()))
    value = float(best.group(1))
    unit = re.sub(r"\s+|\.", "", best.group(2)).lower()
    # Normalize fl oz variants.
    if unit in ("floz",):
        unit = "fl oz"
    return {"value": value, "unit": unit, "raw": best.group(0)}


_WARNING_SENTINELS = (
    "government warning",
    "surgeon general",
    "birth defects",
    "during pregnancy",
    "operate machinery",
    "health problems",
    "alcoholic beverages",
)

# Allow common OCR artifacts in the all-caps header check: extra spaces
# anywhere, missing colon, "WARN ING" with a space.
_HEADER_CAPS_RE = re.compile(r"GOVERNMENT\s+WARN\s*ING\s*:?")

# Single-word warning vocabulary used as a case-vote signal. The literal
# header phrase "GOVERNMENT WARNING" is rarely reassembled from rotated
# small-print OCR (the two words land on different detected lines), so we
# fall back to checking the case of warning-body words that DO survive
# OCR — those reliably tell us whether the original text was set in caps
# or title case.
_WARNING_KEYWORDS = (
    "alcoholic",
    "beverages",
    "warning",
    "drink",
    "drive",
    "pregnancy",
    "machinery",
    "surgeon",
    "general",
    "government",
    "according",
    "operate",
    "consumption",
    "health",
    "cause",
    "birth",
    "defects",
)


def _warning_caps_vote(raw_text: str) -> tuple[int, int]:
    """Count uppercase vs title/lower-case occurrences of warning keywords.

    Returns (uppercase_hits, titlecase_hits). 'uppercase' means the entire
    word is uppercase (`\\bALCOHOLIC\\b`); 'titlecase' covers both
    `Alcoholic` and `alcoholic` — anything that's not all-caps. We use
    word boundaries so partial garbage matches don't count.
    """
    if not raw_text:
        return 0, 0
    upper_hits = 0
    other_hits = 0
    for kw in _WARNING_KEYWORDS:
        # Use re with case-sensitive flag (default) on the raw text.
        upper_hits += len(re.findall(rf"\b{kw.upper()}\b", raw_text))
        # Title- and lower-case: anything that looks like the word but
        # isn't all-caps. Match case-insensitively, then exclude the all-
        # caps form so we don't double-count.
        for m in re.finditer(rf"\b{kw}\b", raw_text, flags=re.IGNORECASE):
            if m.group(0) != kw.upper():
                other_hits += 1
    return upper_hits, other_hits


def extract_warning(text: str) -> dict:
    """Detect the presence and capitalization of the government warning.

    Returns: {
        'present': bool,            # warning text is recognizable in the OCR output
        'header_caps_ok': bool,     # 'GOVERNMENT WARNING:' appears in ALL CAPS
        'body_score': float,        # 0–100 fuzzy match of the body text
        'extracted': str,           # the text window we considered
    }

    Detection strategy: token_set_ratio against the entire normalized OCR
    text. Empirically this discriminates much better than partial_ratio
    between "warning fragmented across lines" and "no warning, just
    happens to share common words like 'the' / 'of'": measured on our
    eval set, partial_ratio's noise floor for absent-warning labels was
    ~44%, while token_set_ratio sits at ~16% for the same inputs and
    still scores ~95% on clean reads.

    The header caps check is independent and uses the *raw* (un-lowercased)
    text — that's how Jenny's "Government Warning" title-case violation
    is detected.
    """
    if not text:
        return {"present": False, "header_caps_ok": False, "body_score": 0.0, "extracted": ""}

    # Header caps check is a layered decision. The classic regex for the
    # adjacent phrase 'GOVERNMENT WARNING' rarely fires on rotated small-
    # print OCR (the two words almost never land on the same detected
    # line). We use a case-vote on warning-body words instead — but with
    # tolerance for OCR mixed-case artifacts (e.g. 'SuRGEON' on otherwise-
    # all-caps text): uppercase only needs to *dominate*, not be unanimous.
    # Falls back to the regex for the rare case where neither vote signal
    # is available.
    upper_hits, other_hits = _warning_caps_vote(text)
    if upper_hits >= 2 and upper_hits >= other_hits:
        header_caps_ok = True
    elif other_hits > 0 and upper_hits == 0:
        header_caps_ok = False
    else:
        header_caps_ok = bool(_HEADER_CAPS_RE.search(text))

    # Whole-text token_set_ratio: order-independent, ignores duplicates,
    # and (critically) is far less prone to false-positive scores from
    # incidental common-word collisions than partial_ratio.
    norm_text = _norm(text)
    norm_warning = _norm(GOVERNMENT_WARNING_TEXT)
    body_score = float(fuzz.token_set_ratio(norm_warning, norm_text))

    # Locate a window for human display. Try to anchor on a sentinel, fall
    # back to the start of the warning's best-matching region.
    extracted_window = ""
    for sentinel in _WARNING_SENTINELS:
        if sentinel in norm_text:
            idx = norm_text.index(sentinel)
            start = max(0, idx - 30)
            end = min(len(norm_text), idx + len(norm_warning) + 60)
            extracted_window = norm_text[start:end]
            break
    if not extracted_window and body_score >= 50:
        extracted_window = norm_text[: len(norm_warning) + 80]

    # Threshold tuned against measured OCR yield on the eval set:
    # genuinely-missing warnings score 16–33 (incidental shared words);
    # warnings present but OCR-fragmented score 48–73; clean reads ≥95.
    # 45 sits in the gap and discriminates cleanly.
    present = body_score >= 45

    return {
        "present": bool(present),
        "header_caps_ok": header_caps_ok,
        "body_score": body_score,
        "extracted": extracted_window.strip(),
    }


def extract_fields(ocr_text: str, ocr_lines: Optional[list[str]] = None) -> dict:
    """Bundle the per-field extractors into a single dict.

    The matcher does *not* try to identify which line is the brand vs.
    which is the class — that's left to fuzzy comparison against the
    application data, which is robust to ordering and noise.

    `line_count` is exposed so downstream checks can soften their verdict
    when OCR yield is suspiciously low (e.g. tightly cropped bottle
    photos that miss whole regions of the label).
    """
    lines = ocr_lines or []
    return {
        "abv": extract_abv(ocr_text),
        "net_contents": extract_net_contents(ocr_text),
        "warning": extract_warning(ocr_text),
        "full_text": ocr_text,
        "lines": lines,
        "line_count": len(lines),
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _fuzzy_search(needle: str, haystack: str) -> tuple[float, str]:
    """Find the best fuzzy match for `needle` inside `haystack`.

    Tries the whole-text token_sort_ratio and the per-line max via
    partial_ratio, returning the higher of the two with the matching
    snippet. Brand names appear on a single line; class/type may span
    one or two lines.

    Comparison is case-insensitive: alcohol labels routinely render
    brand and class/type in ALL CAPS, while application data tends to
    be title-cased. Dave's "STONE'S THROW" vs "Stone's Throw" example
    is exactly this issue.
    """
    if not needle or not haystack:
        return 0.0, ""
    needle_norm = needle.lower()
    haystack_norm = haystack.lower()
    full_score = fuzz.token_sort_ratio(needle_norm, haystack_norm)
    best_line_score = 0.0
    best_line = ""
    for raw_line in haystack.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        s = fuzz.partial_ratio(needle_norm, line.lower())
        if s > best_line_score:
            best_line_score = s
            best_line = line  # preserve original casing for display
    if best_line_score >= full_score:
        return float(best_line_score), best_line
    return float(full_score), haystack[:120]


def _classify(score: float, hi: float, lo: float) -> str:
    if score >= hi:
        return STATUS_MATCH
    if score >= lo:
        return STATUS_REVIEW
    return STATUS_MISMATCH


def _check_fuzzy_field(
    field_name: str,
    expected: str,
    haystack: str,
    label: str,
) -> FieldResult:
    """Generic fuzzy-string field check used for brand/class/producer/country."""
    hi, lo = _FUZZY_THRESHOLDS[field_name]
    if not (expected or "").strip():
        return FieldResult(label, "", "", 0.0, STATUS_NOT_FOUND, "Not provided in application data")
    score, snippet = _fuzzy_search(expected, haystack or "")
    status = _classify(score, hi, lo)
    if status == STATUS_MATCH:
        notes = f"Fuzzy score {score:.0f}% — strong match"
    elif status == STATUS_REVIEW:
        notes = (
            f"Fuzzy score {score:.0f}% — likely match but verify "
            f"('{expected}' vs '{snippet}')"
        )
    else:
        notes = (
            f"Fuzzy score {score:.0f}% — could not confirm "
            f"'{expected}' on label"
        )
    return FieldResult(label, expected, snippet, score, status, notes)


def _check_abv(expected: str, extracted: Optional[dict]) -> FieldResult:
    if not (expected or "").strip():
        return FieldResult("ABV", "", "", 0.0, STATUS_NOT_FOUND, "Not provided in application data")
    expected_pct_m = _ABV_PERCENT_RE.search(expected)
    if not expected_pct_m:
        return FieldResult(
            "ABV", expected, str(extracted or ""), 0.0, STATUS_MISMATCH,
            f"Could not parse a percentage from expected value '{expected}'",
        )
    expected_pct = float(expected_pct_m.group(1))
    if not extracted:
        return FieldResult(
            "ABV", expected, "", 0.0, STATUS_NOT_FOUND,
            "Could not locate an alcohol-by-volume percentage on the label",
        )
    found_pct = extracted["percent"]
    found_raw = extracted["raw"]
    if abs(found_pct - expected_pct) < 0.05:
        notes = f"Expected {expected_pct}%, found {found_pct}%"
        # Optional consistency check on proof.
        if extracted.get("proof") is not None:
            proof = extracted["proof"]
            implied = expected_pct * 2
            if abs(proof - implied) > 1.0:
                notes += (
                    f" — but proof value {proof} doesn't match the implied "
                    f"{implied:g} for {expected_pct}% ABV (review)"
                )
                return FieldResult(
                    "ABV", f"{expected_pct}%", found_raw, 90.0,
                    STATUS_REVIEW, notes,
                )
        return FieldResult("ABV", f"{expected_pct}%", found_raw, 100.0, STATUS_MATCH, notes)
    return FieldResult(
        "ABV", f"{expected_pct}%", found_raw, 0.0, STATUS_MISMATCH,
        f"Expected {expected_pct}%, found {found_pct}%",
    )


def _check_net_contents(
    expected: str,
    extracted: Optional[dict],
    ocr_line_count: int = 0,
) -> FieldResult:
    if not (expected or "").strip():
        return FieldResult("Net Contents", "", "", 0.0, STATUS_NOT_FOUND, "Not provided in application data")
    exp_m = _NET_CONTENTS_RE.search(expected)
    if not exp_m:
        return FieldResult(
            "Net Contents", expected, str(extracted or ""), 0.0, STATUS_MISMATCH,
            f"Could not parse a volume+unit from expected value '{expected}'",
        )
    exp_value = float(exp_m.group(1))
    exp_unit = re.sub(r"\s+|\.", "", exp_m.group(2)).lower()
    if exp_unit == "floz":
        exp_unit = "fl oz"
    if not extracted:
        # When OCR yield is suspiciously low (< 10 lines on a label that
        # would normally produce 25+), it's more honest to flag for human
        # review than to call this a hard MISMATCH — the value may well
        # be on the label, just outside the cropped/visible area or below
        # OCR's contrast floor. The downstream verdict roll-up treats
        # NOT_FOUND with a populated expected as a mismatch, so we
        # explicitly return REVIEW here with a note explaining why.
        if ocr_line_count and ocr_line_count < 10:
            return FieldResult(
                "Net Contents", expected, "", 0.0, STATUS_REVIEW,
                f"OCR returned only {ocr_line_count} lines from this image — "
                "net contents may be present but unreadable; verify visually",
            )
        return FieldResult(
            "Net Contents", expected, "", 0.0, STATUS_NOT_FOUND,
            "Could not locate net contents (volume) on the label",
        )
    same_value = abs(extracted["value"] - exp_value) < 1e-3
    same_unit = extracted["unit"].lower() == exp_unit.lower()
    found_raw = extracted["raw"]
    if same_value and same_unit:
        return FieldResult(
            "Net Contents", f"{exp_value:g} {exp_unit}", found_raw,
            100.0, STATUS_MATCH,
            f"Expected '{exp_value:g} {exp_unit}', found '{found_raw}'",
        )
    return FieldResult(
        "Net Contents", f"{exp_value:g} {exp_unit}", found_raw,
        0.0, STATUS_MISMATCH,
        f"Expected '{exp_value:g} {exp_unit}', found '{found_raw}'",
    )


def _check_warning(extracted: dict, ocr_line_count: int = 0) -> FieldResult:
    # NOTE: deliberately *no* OCR-yield demotion here. We tried demoting
    # warning-not-found → REVIEW on low yield, but that incorrectly
    # rescues genuinely missing-warning labels (e.g. a clean 3-line
    # `LOCAL CRAFT BEER 12 FL OZ` label has no warning AND low yield —
    # we want REJECT). The OCR yield signal can't disambiguate "label
    # has little text" from "OCR missed text". Leave warning failures
    # as MISMATCH and accept the trade-off.
    del ocr_line_count  # accepted for API symmetry with _check_net_contents
    if not extracted.get("present"):
        return FieldResult(
            "Government Warning", "Required statement present",
            extracted.get("extracted", "") or "(not found)",
            extracted.get("body_score", 0.0), STATUS_MISMATCH,
            "Required government warning statement was not detected on the label",
        )
    body_score = extracted.get("body_score", 0.0)
    caps_ok = extracted.get("header_caps_ok", False)
    snippet = extracted.get("extracted", "")
    # MATCH threshold mirrors the present-threshold (45%) — once we have
    # both the caps-vote signal and the body fragments, the agent's
    # remaining job is visual confirmation; we shouldn't penalise the
    # label for OCR's inability to reconstruct rotated small print.
    if caps_ok and body_score >= 45:
        return FieldResult(
            "Government Warning", "Required statement present",
            snippet, body_score, STATUS_MATCH,
            f"Header in ALL CAPS and body matches official text ({body_score:.0f}%)",
        )
    if not caps_ok:
        return FieldResult(
            "Government Warning", "ALL CAPS header required", snippet,
            body_score, STATUS_REVIEW,
            "Warning header is not in ALL CAPS — required format is "
            "'GOVERNMENT WARNING:' (all uppercase)",
        )
    # Caps OK but body imperfect.
    return FieldResult(
        "Government Warning", "Required statement present", snippet,
        body_score, STATUS_REVIEW,
        f"Body text only {body_score:.0f}% match to official wording — "
        "verify visually",
    )


def validate_fields(extracted: dict, expected: dict) -> list[FieldResult]:
    """Run each field check in a stable, predictable order.

    `expected` keys (all optional except brand): brand, class_type, abv,
    net_contents, producer, country_of_origin, check_warning.
    """
    full_text = extracted.get("full_text", "") or ""
    results: list[FieldResult] = []

    brand_result = _check_fuzzy_field(
        "brand", expected.get("brand", ""), full_text, "Brand Name",
    )
    results.append(brand_result)
    # Class/type inheritance: when the class word is already a token in
    # the brand name (e.g. brand "STONE'S THROW IPA", class "IPA"),
    # validating the brand has already validated the class — running
    # an independent fuzzy check just invites short-needle false
    # positives ("IPA" partial-matches "PAEGNANCY" at 80%). If the
    # brand matched/reviewed, inherit that status; otherwise fall back
    # to the normal independent check.
    class_expected = (expected.get("class_type") or "").strip()
    brand_expected = (expected.get("brand") or "").strip()
    if (
        class_expected
        and brand_expected
        and class_expected.lower() in brand_expected.lower().split()
        and brand_result.status in (STATUS_MATCH, STATUS_REVIEW)
    ):
        results.append(FieldResult(
            "Class/Type", class_expected, brand_result.extracted,
            brand_result.score, brand_result.status,
            f"Inherited from brand match ('{class_expected}' is part of "
            f"brand '{brand_expected}')",
        ))
    else:
        results.append(_check_fuzzy_field(
            "class_type", expected.get("class_type", ""), full_text, "Class/Type",
        ))
    results.append(_check_abv(expected.get("abv", ""), extracted.get("abv")))
    results.append(_check_net_contents(
        expected.get("net_contents", ""),
        extracted.get("net_contents"),
        ocr_line_count=int(extracted.get("line_count", 0) or 0),
    ))
    results.append(_check_fuzzy_field(
        "producer", expected.get("producer", ""), full_text, "Producer/Bottler",
    ))
    # Country of origin: only checked if populated (blank = domestic, skip).
    if (expected.get("country_of_origin") or "").strip():
        results.append(_check_fuzzy_field(
            "country_of_origin", expected["country_of_origin"], full_text,
            "Country of Origin",
        ))
    # Warning check is opt-out from the sidebar — when off, the field is
    # simply omitted from the result rather than reported as "not found".
    if expected.get("check_warning", True):
        results.append(_check_warning(
            extracted.get("warning", {}),
            ocr_line_count=int(extracted.get("line_count", 0) or 0),
        ))
    return results


def overall_verdict(fields: list[FieldResult]) -> str:
    """Roll up per-field statuses into APPROVE / REVIEW / REJECT.

    Rules:
      * Any mismatch or not_found (when the field was provided) => REJECT.
      * Any review                                              => REVIEW.
      * All match                                               => APPROVE.

    A `not_found` for a field the user didn't fill in is benign and is
    excluded from the rollup (see logic below).
    """
    has_mismatch = False
    has_review = False
    for f in fields:
        if f.status == STATUS_MISMATCH:
            has_mismatch = True
        elif f.status == STATUS_NOT_FOUND:
            # Field was not provided in application data — benign skip.
            if not f.expected and "Not provided" in f.notes:
                continue
            if "skip" in (f.notes or "").lower():
                continue
            # Field WAS expected but couldn't be found on label.
            if f.expected:
                has_mismatch = True
        elif f.status == STATUS_REVIEW:
            has_review = True
    if has_mismatch:
        return VERDICT_REJECT
    if has_review:
        return VERDICT_REVIEW
    return VERDICT_APPROVE
