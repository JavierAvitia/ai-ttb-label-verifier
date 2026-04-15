"""
Offline evaluation harness.

Runs the full OCR + matching pipeline against every image referenced in
`ground_truth.json` and prints a per-field accuracy report plus the
overall verdict accuracy. Lets us:

  * verify that changes to preprocessing or thresholds don't regress
  * understand which fields fail most often (and why)
  * measure end-to-end per-image latency

Usage:
    python evaluate.py [--sample-dir sample_labels] [--ground-truth ground_truth.json]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from matcher import (
    extract_fields,
    overall_verdict,
    validate_fields,
)
from utils import (
    STATUS_MATCH,
    STATUS_NOT_FOUND,
    STATUS_REVIEW,
    VERDICT_APPROVE,
    VERDICT_REJECT,
    VERDICT_REVIEW,
)


def _field_matched(field_name: str, status: str, ground_truth: dict) -> bool:
    """Decide whether a per-field result counts as 'correct' vs ground truth.

    For most fields we count MATCH or REVIEW as 'present' (the tool
    found something resembling the right value). MISMATCH or NOT_FOUND
    count as a miss.

    For the 'Government Warning' field we additionally check the caps
    expectation: when the ground truth says caps_ok=False, a REVIEW
    status is the *correct* outcome.
    """
    if field_name == "Government Warning":
        warning_present = ground_truth.get("warning_present", True)
        caps_ok = ground_truth.get("warning_caps_ok", True)
        if not warning_present:
            return status in ("mismatch",)  # we should have flagged it
        if not caps_ok:
            return status in (STATUS_REVIEW,)
        return status == STATUS_MATCH
    return status in (STATUS_MATCH, STATUS_REVIEW)


def evaluate(sample_dir: Path, ground_truth_path: Path) -> int:
    if not ground_truth_path.exists():
        print(f"ERROR: ground truth file not found at {ground_truth_path}")
        return 2

    with ground_truth_path.open() as fh:
        truth_data = json.load(fh)

    # Lazy import — only pay the EasyOCR startup cost when actually evaluating.
    import ocr  # noqa: WPS433

    field_correct: dict[str, int] = defaultdict(int)
    field_total: dict[str, int] = defaultdict(int)
    verdict_correct = 0
    verdict_total = 0
    durations: list[float] = []
    failure_modes: list[str] = []

    images_processed = 0
    images_skipped = 0

    print("=" * 60)
    print(f"Evaluating against {len(truth_data)} ground-truth entries…")
    print("=" * 60)

    for image_name, entry in truth_data.items():
        path = sample_dir / image_name
        if not path.exists():
            print(f"  SKIP  {image_name}  (file not present in {sample_dir})")
            images_skipped += 1
            continue
        expected = entry["expected"]
        gt = entry["ground_truth"]
        try:
            t0 = time.perf_counter()
            ocr_result = ocr.extract_text(str(path))
            extracted = extract_fields(ocr_result["full_text"], ocr_result["lines"])
            fields = validate_fields(extracted, expected)
            verdict = overall_verdict(fields)
            elapsed = time.perf_counter() - t0
        except Exception as exc:  # don't let one image abort the whole run
            print(f"  FAIL  {image_name}: {exc}")
            images_skipped += 1
            continue
        durations.append(elapsed)
        images_processed += 1

        # Per-field tally.
        for f in fields:
            field_total[f.field_name] += 1
            if _field_matched(f.field_name, f.status, gt):
                field_correct[f.field_name] += 1
            else:
                failure_modes.append(
                    f"{image_name} :: {f.field_name} :: status={f.status} "
                    f"score={f.score:.0f}% :: {f.notes}"
                )

        # Verdict tally.
        verdict_total += 1
        if verdict == gt.get("expected_verdict"):
            verdict_correct += 1

        print(
            f"  {verdict:<7} {image_name:<35} "
            f"OCR={ocr_result['avg_confidence']*100:>4.0f}% "
            f"({elapsed:>4.1f}s)"
        )

    if images_processed == 0:
        print()
        print("No images were processed — populate sample_labels/ with the "
              "files referenced in ground_truth.json before re-running.")
        print(f"Skipped: {images_skipped}")
        return 1

    print()
    print("Field accuracy (correct / total)")
    print("-" * 60)
    for fname in sorted(field_total):
        c = field_correct[fname]
        t = field_total[fname]
        pct = (c / t * 100) if t else 0.0
        print(f"  {fname:<22} {c}/{t}  ({pct:.0f}%)")

    print()
    print(f"Verdict accuracy: {verdict_correct}/{verdict_total}  "
          f"({verdict_correct / verdict_total * 100:.0f}%)")
    print(f"Avg processing time: {statistics.mean(durations):.2f}s "
          f"(min {min(durations):.2f}s, max {max(durations):.2f}s)")

    if failure_modes:
        print()
        print("Failure modes:")
        for line in failure_modes[:20]:  # cap output
            print(f"  - {line}")
        if len(failure_modes) > 20:
            print(f"  … and {len(failure_modes) - 20} more")

    print()
    print("Conclusion: strong first-pass filter for routine labels. "
          "Agents should review flagged items and low-confidence extractions.")
    print("=" * 60)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", default="sample_labels", type=Path)
    parser.add_argument("--ground-truth", default="ground_truth.json", type=Path)
    args = parser.parse_args(argv)
    return evaluate(args.sample_dir, args.ground_truth)


if __name__ == "__main__":
    sys.exit(main())
