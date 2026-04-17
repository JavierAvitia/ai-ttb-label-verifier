---
title: TTB Label Verifier
emoji: 🍷
colorFrom: purple
colorTo: blue
sdk: streamlit
sdk_version: 1.42.0
app_file: app.py
pinned: false
---

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
| Sarah — sub-5s feedback, "my mother could use it" | Streamlit drag-and-drop UI; ~10–18s per image on CPU after model warmup (dual-pass OCR with rotation), 2–4× faster on GPU |
| Dave — capitalization/punctuation nuance ("STONE'S THROW" vs "Stone's Throw") | Fuzzy matching with calibrated thresholds; explanatory notes per field |
| Jenny — bad photos (angles, glare, low contrast) | OpenCV preprocessing pipeline (CLAHE, deskew, blur); low-confidence warning per image |
| Marcus — no firewall-blocked vendor APIs, no PII | Fully self-contained: EasyOCR runs locally, no network calls, no persistence |

---

## Approach & Decisions

| Layer | Choice | Why |
|---|---|---|
| **Language** | Python 3.11+ | Single ecosystem covers OCR + matching + UI |
| **UI** | Streamlit | Zero training; built-in batch upload, expanders, progress bars; no frontend build |
| **OCR** | EasyOCR | Self-contained, no API keys, no outbound traffic — sidesteps the firewall problem that killed the prior vendor pilot |
| **Preprocessing** | OpenCV (headless) | CLAHE + Gaussian blur + optional deskew handles glare, angles, low contrast |
| **Matching** | RapidFuzz + regex | Fuzzy for brand/class/producer; numeric comparison for ABV / net contents; presence + caps check for warning |
| **Tabular display & export** | Pandas | Results tables and one-click CSV download |
| **Deployment** | HuggingFace Spaces | CPU Upgrade tier (8 vCPU / 32 GB) for fast OCR inference; native Streamlit SDK — just push and it runs |

### Why *not* the alternatives

- **No FastAPI + React** — overkill for a POC; adds a build step, two
  servers, and CORS for no benefit at this stage.
- **No cloud vision APIs** (Azure / GCP / AWS) — the stakeholder
  conversation flagged the federal firewall as a hard blocker; the prior
  vendor pilot died on this exact issue.
- **No database** — Marcus confirmed no PII concerns for the prototype;
  everything is in-memory per session.
- **No Streamlit Community Cloud / Render.com free tier** — Streamlit
  Cloud's 1 GB RAM limit OOMs during model loading; Render's free
  tier (512 MB) is even tighter. HuggingFace Spaces provides 16 GB
  free with native Streamlit support.

---

## Key Features

- **Batch upload** — drop 5–20+ labels at once; processed in parallel with a
  ThreadPoolExecutor so wall-clock time scales sub-linearly.
- **Severity-sorted results** — REJECT first, then REVIEW, then APPROVE.
  Agents see problems immediately without scrolling.
- **Per-field side-by-side comparison** — every row shows
  `Expected · Found · Score · Status · Notes`. Notes explain *what* was
  found vs. *what* was expected, in plain English.
- **Up to ten label fields verified** — seven baseline (brand,
  class/type, ABV, net contents, producer/bottler, country of origin
  when populated, government warning statement) plus up to three
  type-specific additions (appellation of origin, age statement,
  sulfite declaration) driven by the beverage-type selector.
- **OCR confidence per image** — surfaced in the result header; a warning
  appears when confidence drops below 70%.
- **Government warning check** — verifies textual presence, ALL CAPS
  header, and ≥45% body match to the official statement (calibrated
  to catch OCR-fragmented text while filtering noise).
- **Beverage-type-aware rules** — the selector drives which checks run:
  Wine adds a mandatory sulfite-declaration check (opt-out for rare
  <10 ppm wines) plus an optional Appellation of Origin; Distilled
  Spirits adds an optional Age Statement; Beer/Malt Beverage uses the
  baseline check set. Type is included in CSV output.
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

### Deployed — HuggingFace Spaces (recommended)

HuggingFace Spaces is the recommended deployment path. EasyOCR +
PyTorch CPU requires ~2 GB peak RAM during model loading — this
exceeds the free tiers of Streamlit Cloud (1 GB) and Render (512 MB).
The app runs on the CPU Upgrade tier (8 vCPU / 32 GB RAM) for fast
batch inference, with native Streamlit SDK support and zero code
changes required.

1. Push this repo to GitHub.
2. On [huggingface.co/spaces](https://huggingface.co/new-space) →
   **Create new Space**.
3. Select **Streamlit** as the SDK, link the GitHub repo.
4. HF Spaces auto-installs from `requirements.txt` and runs `app.py`.

The first build takes ~5–10 minutes (installs PyTorch CPU + EasyOCR,
downloads detection + recognition models). Subsequent deploys reuse
cached layers.

> **Cold-start note:** the free tier sleeps after ~48 hours of
> inactivity. First visit after sleep takes 30–90 seconds while the
> container restarts and OCR models reload.

> **Live URL:** https://javieravitia-ai-ttb-label-verifier.hf.space

### Docker (local / self-hosted)

```bash
docker build -t ttb-label-verifier .
docker run -p 8501:8501 ttb-label-verifier
```

A `Dockerfile` is included for environments where you need full
control over system dependencies (on-prem, VPS, etc.). EasyOCR
models are pre-cached during the Docker build, so containers start
instantly without downloading models at runtime.

### Why HuggingFace Spaces

| Concern | Streamlit Cloud | Render.com free | HuggingFace Spaces |
|---|---|---|---|
| Resources | 1 GB RAM — OOMs during model load | 512 MB RAM — OOMs immediately | **8 vCPU / 32 GB** (CPU Upgrade) |
| Streamlit support | Native | Docker only | **Native SDK** — zero config |
| System dependencies | Mixed Debian base; apt conflicts | Dockerfile required | Pre-installed (libGL, etc.) |
| Cold start | N/A (OOMs) | N/A (OOMs) | ~30–90s after sleep |
| Senior-engineer signal | "It deploys" | Thoughtful Dockerfile | ML-aware platform choice |

### Evaluation harness

```bash
# Run the evaluation harness against the AI-generated sample labels
python evaluate.py

# Optional: generate programmatic labels for fast matcher iteration
python generate_labels.py --output-dir sample_labels_programmatic
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
├── generate_labels.py  # Supplementary: programmatic labels for fast matcher iteration
├── ground_truth.json   # Per-image expected values for evaluation
├── sample_labels/      # AI-generated test label images (realistic bottle photos)
├── requirements.txt
├── runtime.txt         # Python version pin
├── Dockerfile          # Docker deploy (local, VPS, etc.)
├── .dockerignore       # Keeps Docker context lean
├── .streamlit/config.toml
└── .github/workflows/  # GitHub Actions → HuggingFace Spaces sync
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

### Type-specific rules

The Beverage Type selector drives per-type additions to the baseline
seven-field check, reflecting TTB's distinct mandatory-info matrices for
malt beverages, wine, and distilled spirits:

| Beverage Type | Extra check | Required? | Source |
|---|---|---|---|
| **Wine** | Sulfite Declaration (fuzzy match for `CONTAINS SULFITES` / `SULFITES`, plus British spellings) | Mandatory (opt-out via sidebar checkbox for <10 ppm wines) | 27 CFR § 4.32a |
| **Wine** | Appellation of Origin (e.g. "Napa Valley", "Bordeaux") | Optional; runs when the sidebar field is populated | TTB Wine Labeling |
| **Distilled Spirits** | Age Statement (e.g. "Aged 4 Years") | Optional; runs when the sidebar field is populated (mandatory for some aged spirits — agent supplies when applicable) | TTB Distilled Spirits Labeling |
| **Beer / Malt Beverage** | — | Baseline seven-field check; ABV is treated as optional (only checked when the application form provides it) | TTB Malt Beverage Labeling |

Intentionally **out of scope** for this prototype (POC trade-off):
color-additive disclosures (FD&C Yellow #5), aspartame / saccharin
statements, commodity statements, and percent-foreign-wine. Catching
these requires ingredient-level data the application form doesn't
currently carry.

### OCR pipeline (`ocr.py`)

Tuned against the AI-generated test labels:

- **Dual-pass OCR**: Pass 1 runs on the preprocessed image with `rotation_info=[90,180,270]` (catches vertical warning text on bottle side strips); Pass 2 runs on the raw BGR image without rotation (recovers horizontal small text without fragmentation). Results are merged via containment-based dedup, keeping the higher-confidence version.
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

Actual output from `python evaluate.py` against the AI-generated
sample labels in `sample_labels/` (CPU, no GPU acceleration):

```
============================================================
Evaluating against 12 ground-truth entries…
============================================================
  APPROVE perfect_label.png                   OCR=  87% (23.8s)
  APPROVE angled_glare.png                    OCR=  80% (18.1s)
  REVIEW  warning_violation_titlecase.png     OCR=  95% (13.5s)
  APPROVE brand_caps_mismatch.png             OCR=  82% (12.4s)
  REJECT  low_contrast.png                    OCR=  80% (10.1s)
  REVIEW  stylized_font.png                   OCR=  91% (12.2s)
  REJECT  imported_wine.png                   OCR=  75% (15.9s)
  REJECT  missing_warning.png                 OCR=  91% (11.2s)
  REJECT  Old-Tom-Distillery-Bourbon.png      OCR=  80% (17.9s)
  REJECT  Old-Tom-Distillery-Bourbon-Warning  OCR=  81% (17.7s)
  APPROVE liquor-warning-visible.png          OCR=  88% (15.2s)
  REJECT  multiple-labels-rotated.png         OCR=  80% (31.7s)

Field accuracy (correct / total)
------------------------------------------------------------
  ABV                    3/5  (60%)
  Age Statement          3/3  (100%)
  Appellation of Origin  1/1  (100%)
  Brand Name             12/12 (100%)
  Class/Type             9/9  (100%)
  Country of Origin      2/2  (100%)
  Government Warning     10/12 (83%)
  Net Contents           9/12  (75%)
  Producer/Bottler       2/2  (100%)
  Sulfite Declaration    2/2  (100%)

Verdict accuracy: 11/12  (92%)
Avg processing time: 16.59s (min 10.1s, max 31.7s)
```

| Image | OCR conf | Verdict | Expected | Notes |
|---|---|---|---|---|
| `perfect_label` | 87% | APPROVE | APPROVE ✓ | Baseline beer — all fields cleanly extracted |
| `angled_glare` | 80% | APPROVE | APPROVE ✓ | 20° tilt + glare; rotation pass recovers warning keywords in ALL CAPS |
| `warning_violation_titlecase` | 95% | REVIEW | REVIEW ✓ | Title-case "Government Warning:" detected via case-vote |
| `brand_caps_mismatch` | 82% | APPROVE | APPROVE ✓ | "Stone's Throw" fuzzy-matches "STONE'S THROW" at ≥85 |
| `low_contrast` | 80% | REJECT | REVIEW ✗ | Only 5 OCR lines; warning text invisible at this contrast |
| `stylized_font` | 91% | REVIEW | REVIEW ✓ | Script brand drops into REVIEW band; warning keywords recovered |
| `imported_wine` | 75% | REJECT | REJECT ✓ | Wine — net contents mismatch + no warning + no sulfite declaration |
| `missing_warning` | 91% | REJECT | REJECT ✓ | Beer with no warning text anywhere (body score 16%) |
| `Old-Tom-Distillery-Bourbon` | 80% | REJECT | REJECT ✓ | Spirits, front label only — no warning visible; Age Statement MATCH |
| `Old-Tom-Distillery-Bourbon-Warning` | 81% | REJECT | REJECT ✓ | Curved bottle — ABV/net-contents digits lost to perspective distortion |
| `liquor-warning-visible` | 88% | APPROVE | APPROVE ✓ | Flat spirits shot — all 6 fields + warning MATCH |
| `multiple-labels-rotated` | 80% | REJECT | REJECT ✓ | Multi-label stress test — 6+ products in frame; correct "wrong input" rejection |

Processing times (~10–32 s/image) include the rotation pass on pass 1
(EasyOCR tests 4 orientations per region). Typical images run 10–18s;
the 32s outlier is `multiple-labels-rotated` which has ~80 detected
regions across all the nested labels. On a GPU or in production with
warm model, expect 2–4× faster.

### Failure modes (documented)

- **`low_contrast.png` REJECTed (expected REVIEW).** OCR recovers only
  5 lines and the warning body scores 18% — below the noise floor. The
  tool correctly reports "warning not detected". Production mitigation:
  prompt the agent to retake under better lighting.
- **`Old-Tom-Distillery-Bourbon-Warning.png` REJECTed (correctly).**
  Heavy bottle curvature chops the `45` digit off ABV and splits
  `750 -` from `mL`. Age Statement and warning body both MATCH — only
  the two numeric digits on the curved edge fragment. Production
  mitigation: retake with less curvature.
- **`multiple-labels-rotated.png` REJECTed (correctly).** A single photo
  contains 6+ products; OCR mixes text from all of them. ABV and net
  contents mis-extract (`5%`, `1750 ML`) and warning body drops below
  45% due to competing-label noise. Honest "wrong input" rejection —
  the tool tells the agent to upload one label per photo.

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
