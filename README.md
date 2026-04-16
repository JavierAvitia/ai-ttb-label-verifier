# TTB Alcohol Label Verification Tool

A browser-based prototype that lets TTB compliance agents verify alcohol-label
images against COLA application data in seconds. Drop in one or many label
photos, compare them field-by-field against the application form, and get a
color-coded report — failures surfaced first — with one-click CSV export.

This tool is a **first-pass filter** designed to shave time off routine
matching work. It is not a final compliance authority; agents retain
judgment for nuanced cases.

It directly addresses the stakeholder concerns from the project brief:

| Stakeholder concern | How this tool addresses it |
|---|---|
| Janet — needs to process 5–20+ labels at a time | Batch upload with parallel OCR; results sorted by severity |
| Sarah — sub-5s feedback, "my mother could use it" | Streamlit drag-and-drop UI; ~16–28s per image on CPU after model warmup (dual-pass OCR), 2–4× faster on GPU |
| Dave — capitalization/punctuation nuance ("STONE'S THROW" vs "Stone's Throw") | Fuzzy matching with calibrated thresholds; explanatory notes per field |
| Jenny — bad photos (angles, glare, low contrast) | OpenCV preprocessing pipeline (CLAHE, deskew, blur); low-confidence warning per image |
| Marcus — no firewall-blocked vendor APIs, no PII | Fully self-contained: EasyOCR runs locally, no network calls, no persistence |

---

## Approach & Decisions

| Layer | Choice | Why |
|---|---|---|
| **Language** | Python 3.10+ | Single ecosystem covers OCR + matching + UI |
| **UI** | Streamlit | Zero training; built-in batch upload, expanders, progress bars; no frontend build |
| **OCR** | EasyOCR | Self-contained, no API keys, no outbound traffic — sidesteps the firewall problem that killed the prior vendor pilot |
| **Preprocessing** | OpenCV (headless) | CLAHE + Gaussian blur + optional deskew handles glare, angles, low contrast |
| **Matching** | RapidFuzz + regex | Fuzzy for brand/class/producer; numeric comparison for ABV / net contents; presence + caps check for warning |
| **Tabular display & export** | Pandas | Results tables and one-click CSV download |
| **Deployment** | Streamlit Community Cloud | Free, one-click from GitHub, public URL, zero ops |

### Why *not* the alternatives

- **No FastAPI + React** — overkill for a POC; adds a build step, two
  servers, and CORS for no benefit at this stage.
- **No cloud vision APIs** (Azure / GCP / AWS) — the stakeholder
  conversation flagged the federal firewall as a hard blocker; the prior
  vendor pilot died on this exact issue.
- **No database** — Marcus confirmed no PII concerns for the prototype;
  everything is in-memory per session.
- **No Docker in the default path** — Streamlit Cloud handles deployment.
  A `Dockerfile` is included as an enterprise-deployment signal but not
  required to run the app.

---

## Key Features

- **Batch upload** — drop 5–20+ labels at once; processed in parallel with a
  ThreadPoolExecutor so wall-clock time scales sub-linearly.
- **Severity-sorted results** — REJECT first, then REVIEW, then APPROVE.
  Agents see problems immediately without scrolling.
- **Per-field side-by-side comparison** — every row shows
  `Expected · Found · Score · Status · Notes`. Notes explain *what* was
  found vs. *what* was expected, in plain English.
- **Seven label fields** verified: brand, class/type, ABV, net contents,
  producer/bottler, country of origin (when populated), and the
  government warning statement.
- **OCR confidence per image** — surfaced in the result header; a warning
  appears when confidence drops below 70%.
- **Government warning check** — verifies textual presence, ALL CAPS
  header, and ≥90% body match to the official statement.
- **Beverage type selector** — Distilled Spirits / Wine / Beer; included
  in CSV output (informational in the prototype).
- **CSV export** — flattened one-row-per-field; opens cleanly in Excel.
- **Graceful degradation** — individual file failures don't kill the batch;
  empty / unreadable images report a clear message.

---

## Setup & Run

### Local

```bash
pip install -r requirements.txt
streamlit run app.py
```

The first launch downloads the EasyOCR detection + recognition models
(~100 MB) and caches them under `~/.EasyOCR/`. Subsequent launches load
the model in seconds.

### Deployed

The app deploys cleanly to **Streamlit Community Cloud**:

1. Push this repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io) → New app → point
   it at the repo, branch, and `app.py`.
3. The first cold-start downloads the OCR models (~30s) — subsequent
   sessions are fast.

> Live URL: _add the deployed URL here once published._

### Docker (optional)

```bash
docker build -t ttb-label-verifier .
docker run -p 8501:8501 ttb-label-verifier
```

### Evaluation harness

```bash
# Generate the programmatic test labels (OCR-readable Pillow-rendered text)
python generate_labels.py --output-dir sample_labels
# Run the evaluation harness
python evaluate.py
```

---

## Repo Layout

```
ai-ttb-label-verifier/
├── app.py              # Streamlit UI: sidebar form + batch upload + results
├── ocr.py              # EasyOCR wrapper + OpenCV preprocessing (dual-pass, noise filtering)
├── matcher.py          # Field extraction + per-field comparison
├── utils.py            # Constants, helpers, CSV export
├── evaluate.py         # Offline accuracy / latency harness
├── generate_labels.py  # Programmatic test-label generator (Pillow-rendered, OCR-readable)
├── ground_truth.json   # Per-image expected values for evaluation
├── sample_labels/      # Generated test images (run generate_labels.py)
├── requirements.txt
├── Dockerfile          # Optional containerized deploy
└── .streamlit/config.toml
```

Six Python files, each with a single responsibility. No `utils/`
sub-package, no model-switching abstractions, no premature engineering.

---

## Per-Field Strategy

| Field | Extraction | Comparison | Match / Review thresholds |
|---|---|---|---|
| Brand Name | Full-text search | `rapidfuzz.fuzz.token_sort_ratio` | ≥85 / 70 |
| Class/Type | Full-text search; **inherits brand status when class is a token of the brand** (e.g. brand "STONE'S THROW IPA", class "IPA") | `rapidfuzz.fuzz.token_sort_ratio`; inheritance suppresses short-needle false positives like "IPA" partial-matching "PAEGNANCY" | ≥85 / 70 |
| ABV | Regex `(\d+\.?\d*)\s*%` + optional proof | Numeric exact match (±0.05); proof consistency check (proof ≈ 2× ABV) | exact |
| Net Contents | Regex `(\d+\.?\d*)\s*(mL\|L\|fl oz\|oz)`; **longest raw match wins** (so `12 FL OZ` beats stray `1L` from background graphics); demoted to REVIEW when OCR yield is < 10 lines (likely cropped/unreadable label rather than missing field) | Numeric value + unit exact match | exact |
| Producer/Bottler | Full-text search | `rapidfuzz.fuzz.token_sort_ratio` | ≥80 / 65 (lower — addresses are OCR-fragile) |
| Country of Origin | Full-text search (skipped if blank in form) | `rapidfuzz.fuzz.token_sort_ratio` | ≥85 / 70 |
| Government Warning | Whole-text `token_set_ratio` against official wording + sentinel-anchored window for display | (a) ALL CAPS check via case-vote on warning-keyword words (`ALCOHOLIC`, `BEVERAGES`, `DRINK`, …) — uppercase must dominate non-uppercase; tolerates OCR mixed-case artifacts like `SuRGEON`. (b) body ≥45% token_set match counts as present, ≥45% with caps OK is MATCH. Score band 16–33 = noise floor (genuinely missing); 48–73 = present but OCR-fragmented; ≥95 = clean read. | body ≥45 = present; ≥45 + caps = MATCH; <45 = MISMATCH |

### OCR pipeline (`ocr.py`)

Tuned against the programmatic test labels:

- **Dual-pass OCR**: Pass 1 runs on the preprocessed image (upscale + adaptive CLAHE + deskew); Pass 2 runs on the raw BGR image. Results are merged via containment-based dedup, keeping the higher-confidence version. Small-font text (government warning) often survives better on the unprocessed image.
- **Noise filtering**: drops single-character fragments (OCR artifacts from borders/decorations), lines below 25% confidence, and short pure-digit/symbol garbage. Applied after merging both passes.
- **EasyOCR detection**: `text_threshold=0.3`, `low_text=0.3` (defaults 0.7/0.4 skip the small-font warning text).
- **Upscaling**: images with long edge < 2000 px are upscaled to 2400 px (INTER_CUBIC) before OCR. EasyOCR's recognition accuracy improves sharply once character height clears ~25 px.
- **Adaptive CLAHE**: contrast enhancement runs only when `gray.std() < 60`. Already-punchy labels lose detail when CLAHE pushes bright areas to pure white.
- **OCR error correction**: common OCR substitutions are normalized before field extraction (e.g. `0Z` → `OZ` in net contents).

### Verdict roll-up

- All matches → **APPROVE**
- Any review (and no mismatch) → **REVIEW**
- Any mismatch (or any expected field not found) → **REJECT**

Fields the user *didn't* fill in (e.g. blank country of origin) are
skipped, not penalized.

---

## Test Results

Actual output from `python evaluate.py` against the programmatic
test labels in `sample_labels/` (CPU, no GPU acceleration):

```
============================================================
Evaluating against 8 ground-truth entries…
============================================================
  APPROVE perfect_label.png                   OCR=  81% (28.4s)
  REJECT  angled_glare.png                    OCR=  64% (19.9s)
  REVIEW  warning_violation_titlecase.png     OCR=  79% (17.3s)
  APPROVE brand_caps_mismatch.png             OCR=  83% (17.2s)
  APPROVE low_contrast.png                    OCR=  76% (16.9s)
  APPROVE stylized_font.png                   OCR=  73% (17.5s)
  REJECT  imported_wine.png                   OCR=  89% (16.5s)
  REJECT  missing_warning.png                 OCR=  93% (16.2s)

Field accuracy (correct / total)
------------------------------------------------------------
  ABV                    8/8  (100%)
  Brand Name             8/8  (100%)
  Class/Type             8/8  (100%)
  Country of Origin      1/1  (100%)
  Government Warning     7/8  (88%)
  Net Contents           6/8  (75%)
  Producer/Bottler       6/6  (100%)

Verdict accuracy: 7/8  (88%)
Avg processing time: 18.74s (min 16.19s, max 28.41s)
```

| Image | OCR conf | Verdict | Expected | Notes |
|---|---|---|---|---|
| `perfect_label` | 81% | APPROVE | APPROVE ✓ | All fields match cleanly |
| `angled_glare` | 64% | REJECT | APPROVE ✗ | 12° rotation fragments text into 43 lines; net contents and warning caps unrecoverable |
| `warning_violation_titlecase` | 79% | REVIEW | REVIEW ✓ | Title-case "Government Warning:" correctly detected via keyword case-vote |
| `brand_caps_mismatch` | 83% | APPROVE | APPROVE ✓ | "Stone's Throw" fuzzy-matches "STONE'S THROW" |
| `low_contrast` | 76% | APPROVE | APPROVE ✓ | Adaptive CLAHE + OCR error correction (`0Z`→`OZ`) recovers all fields |
| `stylized_font` | 73% | APPROVE | APPROVE ✓ | Dark bg + gold text + small font; dual-pass OCR recovers warning |
| `imported_wine` | 89% | REJECT | REJECT ✓ | Net contents mismatch (label 90 mL ≠ expected 750 mL) + no warning |
| `missing_warning` | 93% | REJECT | REJECT ✓ | No government warning detected (body score 16%) |

Processing times (~16–28 s/image) include dual-pass OCR. On a GPU
or in production with the model warm-started, expect 2–4× faster.

### Failure mode

- **`angled_glare.png` REJECTed (expected APPROVE).** The 12° rotation
  causes EasyOCR to fragment the label into 43 tiny text regions
  instead of ~11 clean lines. "12 FL OZ" splits across non-adjacent
  detections, so net contents is not recoverable. Warning keywords are
  OCR'd in mixed/lowercase, so the caps vote can't confirm ALL CAPS
  formatting. This is a fundamental OCR limitation on significantly
  rotated images — the deskew pipeline corrects angles > 5° on the
  grayscale image, but EasyOCR's text detection still struggles with
  the rotated input. Production mitigation: guide users to photograph
  labels as straight as possible, or add a manual rotation control.

### Common failure modes (by design)

- **Stylized / decorative fonts on brand names** — fuzzy score drops
  below threshold; flagged for review.
- **Glare or low contrast on warning text** — OCR misses characters;
  body fuzzy match falls into the REVIEW band.
- **Multi-line producer addresses** — OCR fragments the address; lower
  thresholds reduce false negatives but some still slip into REVIEW.
- **Highly artistic labels** — OCR returns fragments, regex extraction
  fails; the system honestly reports "could not locate".

---

## Trade-Offs & Limitations

- **Visual formatting can't be checked from text alone** — we can verify
  the warning text and its capitalization, but not bold, font size, or
  physical placement on the label. Flagged in the result notes.
- **Stylized / decorative typography is a known OCR weakness.** The tool
  surfaces this with a "low confidence" warning rather than silently
  failing.
- **Producer/bottler accuracy is lower than other fields** because
  addresses span multiple lines and OCR fragments them. Thresholds are
  set conservatively (80/65) to bias toward REVIEW rather than mismatch.
- **No COLA system integration** — per the brief, this is a standalone
  POC. Production would auto-populate the sidebar from COLA records.
- **English-language labels only** for the prototype.
- **Beverage type selector is informational** — type-specific rule
  variations (e.g. ABV display exceptions for certain beer / wine) are
  a future enhancement.
- **In a production deployment** with network access cleared by IT, a
  swap to Azure Computer Vision or AWS Textract would likely lift OCR
  accuracy 5–15% on hard images. The architecture (single `extract_text`
  function in `ocr.py`) makes that swap a one-file change.

---

## Assumptions

- Standalone POC, no PII, no persistent storage (per Marcus).
- Agents make the final compliance decision — the tool is a first-pass
  filter, not a gating authority.
- English-language labels only.
- Application data arrives via the sidebar form; in production it would
  come from COLA.

---

## Future Ideas

- **Azure Vision / AWS Textract** for higher accuracy when network access is permitted.
- **COLA integration** to auto-populate application data and write back results.
- **Beverage-type-specific rule packs** (beer ABV display exceptions, wine
  vintage / appellation checks, spirits standards-of-identity terms).
- **Agent feedback loop** — capture overrides on REVIEW/REJECT verdicts
  to tune thresholds over time.
- **Side-by-side bounding-box overlay** — highlight on the label image
  exactly where each field was extracted from.
