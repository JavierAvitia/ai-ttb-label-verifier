"""
Supplementary: generate simple test labels with programmatic text.

The primary test set is the AI-generated images in sample_labels/ —
those are realistic bottle-label photos that exercise the full OCR +
preprocessing pipeline (rotation, CLAHE, deskew, noise filtering).

This script produces plain Pillow-rendered labels useful for fast
iteration when tuning the matcher (no OCR ambiguity, instant
regeneration, deterministic output). Run it to populate a separate
directory for quick smoke tests:

    python generate_labels.py --output-dir sample_labels_programmatic

Do NOT overwrite sample_labels/ — those contain the AI-generated images
used by the evaluation harness and ground_truth.json.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# The official government warning text (from TTB).
GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, "
    "women should not drink alcoholic beverages during pregnancy "
    "because of the risk of birth defects. (2) Consumption of "
    "alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)

# Title-case variant for the violation test case.
GOVERNMENT_WARNING_TITLECASE = GOVERNMENT_WARNING.replace(
    "GOVERNMENT WARNING:", "Government Warning:"
)


def _get_font(size: int) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """Try to load a clean sans-serif font; fall back to default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, draw: ImageDraw.Draw, font, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_label(
    width: int,
    height: int,
    bg_color: str,
    text_color: str,
    lines: list[tuple[str, int]],  # (text, font_size)
    warning_text: str | None = None,
    warning_font_size: int = 14,
    rotate_degrees: float = 0.0,
    low_contrast: bool = False,
) -> Image.Image:
    """Create a label image with the given text lines."""
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    y = 40
    for text, size in lines:
        font = _get_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        draw.text((x, y), text, fill=text_color, font=font)
        y += int(size * 1.4)

    # Draw government warning at the bottom if provided.
    if warning_text:
        warn_font = _get_font(warning_font_size)
        warn_color = text_color if not low_contrast else "#999999"
        wrapped = _wrap_text(warning_text, draw, warn_font, width - 60)
        wy = height - 30 - len(wrapped) * int(warning_font_size * 1.3)
        for wline in wrapped:
            draw.text((30, wy), wline, fill=warn_color, font=warn_font)
            wy += int(warning_font_size * 1.3)

    if rotate_degrees:
        img = img.rotate(rotate_degrees, expand=True, fillcolor=bg_color)

    return img


def generate_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Perfect label — clean, straight, all fields present and correct.
    img = _draw_label(
        600, 800, "#F5F0E8", "#1A1A1A",
        [
            ("STONE'S THROW", 42),
            ("IPA", 28),
            ("India Pale Ale", 22),
            ("6.5% Alc./Vol.", 20),
            ("12 FL OZ", 18),
            ("", 10),
            ("Brewed by Stone's Throw Brewing Co.", 14),
            ("Little Rock, AR", 14),
        ],
        warning_text=GOVERNMENT_WARNING,
        warning_font_size=11,
    )
    img.save(output_dir / "perfect_label.png")
    print("  Created perfect_label.png")

    # 2. Angled + glare simulation (rotation).
    img = _draw_label(
        600, 800, "#F5F0E8", "#1A1A1A",
        [
            ("STONE'S THROW", 42),
            ("IPA", 28),
            ("India Pale Ale", 22),
            ("6.5% Alc./Vol.", 20),
            ("12 FL OZ", 18),
            ("", 10),
            ("Brewed by Stone's Throw Brewing Co.", 14),
            ("Little Rock, AR", 14),
        ],
        warning_text=GOVERNMENT_WARNING,
        warning_font_size=11,
        rotate_degrees=12.0,
    )
    img.save(output_dir / "angled_glare.png")
    print("  Created angled_glare.png")

    # 3. Warning violation — title case instead of ALL CAPS.
    img = _draw_label(
        600, 800, "#F5F0E8", "#1A1A1A",
        [
            ("STONE'S THROW", 42),
            ("IPA", 28),
            ("India Pale Ale", 22),
            ("6.5% Alc./Vol.", 20),
            ("12 FL OZ", 18),
            ("", 10),
            ("Brewed by Stone's Throw Brewing Co.", 14),
            ("Little Rock, AR", 14),
        ],
        warning_text=GOVERNMENT_WARNING_TITLECASE,
        warning_font_size=11,
    )
    img.save(output_dir / "warning_violation_titlecase.png")
    print("  Created warning_violation_titlecase.png")

    # 4. Brand capitalization mismatch — label says "Stone's Throw"
    #    but application data will say "STONE'S THROW".
    img = _draw_label(
        600, 800, "#F5F0E8", "#1A1A1A",
        [
            ("Stone's Throw", 42),
            ("IPA", 28),
            ("India Pale Ale", 22),
            ("6.5% Alc./Vol.", 20),
            ("12 FL OZ", 18),
            ("", 10),
            ("Brewed by Stone's Throw Brewing Co.", 14),
            ("Little Rock, AR", 14),
        ],
        warning_text=GOVERNMENT_WARNING,
        warning_font_size=11,
    )
    img.save(output_dir / "brand_caps_mismatch.png")
    print("  Created brand_caps_mismatch.png")

    # 5. Low contrast — light text on light background.
    img = _draw_label(
        600, 800, "#E8E4DC", "#A09888",
        [
            ("STONE'S THROW", 42),
            ("IPA", 28),
            ("India Pale Ale", 22),
            ("6.5% Alc./Vol.", 20),
            ("12 FL OZ", 18),
        ],
        warning_text=GOVERNMENT_WARNING,
        warning_font_size=11,
        low_contrast=True,
    )
    img.save(output_dir / "low_contrast.png")
    print("  Created low_contrast.png")

    # 6. Stylized font — uses the same font but smaller, tighter spacing.
    #    (True stylized fonts would need custom .ttf files; this simulates
    #    the OCR difficulty with reduced text size on a dark background.)
    img = _draw_label(
        600, 800, "#1A1A1A", "#D4AF37",
        [
            ("STONE'S THROW", 36),
            ("IPA", 24),
            ("India Pale Ale", 18),
            ("6.5% Alc./Vol.", 16),
            ("12 FL OZ", 14),
        ],
        warning_text=GOVERNMENT_WARNING,
        warning_font_size=9,
    )
    img.save(output_dir / "stylized_font.png")
    print("  Created stylized_font.png")

    # 7. Imported wine — different beverage type, deliberate net-contents
    #    mismatch (label says "90 mL" but application expects 750 mL),
    #    and no government warning (simulating a front-label-only photo
    #    where the warning sits on the back label).
    img = _draw_label(
        600, 800, "#FFF8F0", "#2C1810",
        [
            ("CHATEAU DE LA PAIX", 36),
            ("BORDEAUX", 24),
            ("Red Wine", 20),
            ("13.5% Alc./Vol.", 18),
            ("90 mL", 16),
            ("", 10),
            ("Product of France", 16),
            ("Imported by WineCo, New York, NY", 12),
        ],
        warning_text=None,  # front label only — warning on back
    )
    img.save(output_dir / "imported_wine.png")
    print("  Created imported_wine.png")

    # 8. Missing warning — no government warning at all.
    img = _draw_label(
        600, 800, "#F0F0F0", "#333333",
        [
            ("LOCAL CRAFT BEER", 36),
            ("Pale Ale", 24),
            ("5.2% Alc./Vol.", 18),
            ("12 FL OZ", 16),
            ("", 10),
            ("Brewed by Local Brewing Co.", 14),
            ("Portland, OR", 14),
        ],
        warning_text=None,  # intentionally missing
    )
    img.save(output_dir / "missing_warning.png")
    print("  Created missing_warning.png")

    print(f"\nDone — 8 labels written to {output_dir}/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="sample_labels", type=Path)
    args = parser.parse_args(argv)
    generate_all(args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
