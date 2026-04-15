# Sample Labels

This directory holds the test images referenced by `ground_truth.json`
and used by `evaluate.py`.

The repo ships **without** the actual image binaries — they are AI-generated
mock labels and intentionally excluded from version control. To populate this
directory before running the evaluation harness:

1. Generate (or photograph) labels matching the names in `ground_truth.json`:
   - `perfect_label.jpg` — clean, straight-on, good lighting (happy path)
   - `angled_glare.jpg` — 15–20° angle with visible glare
   - `warning_violation_titlecase.jpg` — "Government Warning:" not in ALL CAPS
   - `brand_caps_mismatch.jpg` — "Stone's Throw" on label vs "STONE'S THROW" in form
   - `low_contrast.jpg` — light text on light background
   - `stylized_font.jpg` — decorative/script font for the brand name
   - `imported_wine.jpg` — French wine with importer address and 13.5% ABV
   - `missing_warning.jpg` — beer label with no government warning at all

2. Drop them in this directory.

3. Run `python evaluate.py` from the repo root.

For interactive demo use you can also drag any of these images into the
Streamlit app — they'll be processed alongside whatever you pre-populated
in the sidebar form.
