# Sample Labels

This directory holds the test images referenced by `ground_truth.json`
and used by `evaluate.py`.

The repo ships **without** the actual image binaries — they are AI-generated
mock labels, regenerated locally, and intentionally excluded from version
control.

## Required filenames

The current ground-truth set is built around **Stone's Throw IPA** (a
12 fl oz beer), with one imported French wine and one beer with no warning
to round out the failure-mode coverage. To populate this directory:

| Filename | Scenario |
|---|---|
| `perfect_label.jpg` | STONE'S THROW IPA, sans-serif, GOVERNMENT WARNING in ALL CAPS, straight-on (baseline) |
| `angled_glare.jpg` | Same content, ~20° tilt with visible glare (preprocessing test) |
| `warning_violation_titlecase.jpg` | "Stone's Throw" (serif), warning header reads "Government Warning:" — title case violation |
| `brand_caps_mismatch.jpg` | "Stone's Throw" (title case on label) vs "STONE'S THROW" in form — fuzzy match test |
| `low_contrast.jpg` | STONE'S THROW IPA in light gray on off-white paper (OCR stress test) |
| `stylized_font.jpg` | Stone's Throw IPA in connected cursive script (font robustness test) |
| `imported_wine.jpg` | CHÂTEAU DE LA PAIX, Bordeaux 2020, 13.5% alc/vol, "Imported by WineCo, NY" — front label has no warning |
| `missing_warning.jpg` | LOCAL CRAFT BEER, 12 FL OZ — no government warning anywhere on label |

## How to populate

1. Save the AI-generated images into this directory with the filenames above.
2. From the repo root, run:
   ```bash
   python evaluate.py
   ```

For interactive demo use you can also drag any of these images into the
Streamlit app — they'll be processed alongside whatever you pre-populated
in the sidebar form.
