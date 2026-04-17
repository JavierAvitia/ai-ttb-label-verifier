# Sample Labels

This directory holds the test images referenced by `ground_truth.json`
and used by `evaluate.py`.

The repo ships **without** the actual image binaries — they are AI-generated
mock labels, regenerated locally, and intentionally excluded from version
control.

## Required filenames

The ground-truth set spans all three beverage types — Beer / Malt
Beverage (Stone's Throw IPA family, one unlabeled craft beer), Wine
(one imported Bordeaux plus one multi-label stress test), and Distilled
Spirits (three Old Tom Distillery bourbon shots at varying perspectives).
To populate this directory:

| Filename | Type | Scenario |
|---|---|---|
| `perfect_label.jpg` | Beer | STONE'S THROW IPA, sans-serif, GOVERNMENT WARNING in ALL CAPS, straight-on (baseline) |
| `angled_glare.jpg` | Beer | Same content, ~20° tilt with visible glare (preprocessing test) |
| `warning_violation_titlecase.jpg` | Beer | "Stone's Throw" (serif), warning header reads "Government Warning:" — title case violation |
| `brand_caps_mismatch.jpg` | Beer | "Stone's Throw" (title case on label) vs "STONE'S THROW" in form — fuzzy match test |
| `low_contrast.jpg` | Beer | STONE'S THROW IPA in light gray on off-white paper (OCR stress test) |
| `stylized_font.jpg` | Beer | Stone's Throw IPA in connected cursive script (font robustness test) |
| `imported_wine.jpg` | Wine | CHÂTEAU DE LA PAIX, Bordeaux 2020, 13.5% alc/vol, "Imported by WineCo, NY" — front label has no warning and no sulfite declaration (wine-specific miss) |
| `missing_warning.jpg` | Beer | LOCAL CRAFT BEER, 12 FL OZ — no government warning anywhere on label |
| `Old-Tom-Distillery-Bourbon.jpg` | Spirits | OLD TOM DISTILLERY Kentucky Straight Bourbon, Aged 4 Years, 45% / 90 proof, 750 mL — front-label shot with no visible warning (back-label only) |
| `Old-Tom-Distillery-Bourbon-Warning.jpg` | Spirits | Same product, warning panel wrapping around the bottle curvature — exercises the OCR ceiling when text is perspective-distorted (ABV/net-contents digits fragment) |
| `liquor-warning-visible.jpg` | Spirits | Upright flat bourbon shot with clean warning — the happy-path Distilled Spirits APPROVE case |
| `multiple-labels-rotated.jpg` | Wine | Multi-product stress test — one photo containing 6+ rotated labels (GRITT, CRAFT BEER, ROK, JIM BEAM, CHÂTEAU DE LA ROCHE Cabernet Sauvignon); exercises correct REJECT for wrong-input photos |

## How to populate

1. Save the AI-generated images into this directory with the filenames above.
2. From the repo root, run:
   ```bash
   python evaluate.py
   ```

For interactive demo use you can also drag any of these images into the
Streamlit app — they'll be processed alongside whatever you pre-populated
in the sidebar form.
