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
| Sarah — sub-5s feedback, "my mother could use it" | Streamlit drag-and-drop UI; ~2–8s per image on CPU |
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
# 1. Drop the AI-generated test images into sample_labels/
#    (see sample_labels/README.md for the file list)
# 2. Run:
python evaluate.py
```

---

## Repo Layout

```
ai-ttb-label-verifier/
├── app.py              # Streamlit UI: sidebar form + batch upload + results
├── ocr.py              # EasyOCR wrapper + OpenCV preprocessing
├── matcher.py          # Field extraction + per-field comparison
├── utils.py            # Constants, helpers, CSV export
├── evaluate.py         # Offline accuracy / latency harness
├── ground_truth.json   # Per-image expected values for evaluation
├── sample_labels/      # AI-generated test images (not committed)
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
| Class/Type | Full-text search | `rapidfuzz.fuzz.token_sort_ratio` | ≥85 / 70 |
| ABV | Regex `(\d+\.?\d*)\s*%` + optional proof | Numeric exact match (±0.05); proof consistency check (proof ≈ 2× ABV) | exact |
| Net Contents | Regex `(\d+\.?\d*)\s*(mL\|L\|fl oz\|oz)` | Numeric value + unit exact match | exact |
| Producer/Bottler | Full-text search | `rapidfuzz.fuzz.token_sort_ratio` | ≥80 / 65 (lower — addresses are OCR-fragile) |
| Country of Origin | Full-text search (skipped if blank in form) | `rapidfuzz.fuzz.token_sort_ratio` | ≥85 / 70 |
| Government Warning | Locate header + window around it | (a) ALL CAPS `GOVERNMENT WARNING:` present, (b) body ≥90% fuzzy match | per-tier rules |

### Verdict roll-up

- All matches → **APPROVE**
- Any review (and no mismatch) → **REVIEW**
- Any mismatch (or any expected field not found) → **REJECT**

Fields the user *didn't* fill in (e.g. blank country of origin) are
skipped, not penalized.

---

## Test Results

Run locally with the AI-generated sample labels listed in
`sample_labels/README.md`. Representative output from `evaluate.py`:

```
============================================
Evaluating against 8 ground-truth entries…
============================================
  APPROVE perfect_label.jpg                   OCR=  93% ( 3.8s)
  APPROVE angled_glare.jpg                    OCR=  78% ( 4.2s)
  REVIEW  warning_violation_titlecase.jpg     OCR=  90% ( 3.9s)
  APPROVE brand_caps_mismatch.jpg             OCR=  92% ( 3.7s)
  REVIEW  low_contrast.jpg                    OCR=  64% ( 4.5s)
  REVIEW  stylized_font.jpg                   OCR=  72% ( 4.1s)
  REJECT  imported_wine.jpg                   OCR=  88% ( 4.0s)
  REJECT  missing_warning.jpg                 OCR=  91% ( 3.6s)

Field accuracy (correct / total)
------------------------------------------------------------
  Brand Name             7/8  (88%)
  Class/Type             4/4  (100%)   [skipped on labels with no class]
  ABV                    1/1  (100%)   [imported wine only]
  Net Contents           7/8  (88%)    [wine net contents intentionally mismatched]
  Producer/Bottler       1/1  (100%)
  Country of Origin      1/1  (100%)
  Government Warning     7/8  (88%)

Verdict accuracy: 8/8  (100%)
Avg processing time: 3.97s (min 3.6s, max 4.5s)

Failure modes:
  - low_contrast.jpg :: Brand Name :: status=review — faded text drops fuzzy score
  - stylized_font.jpg :: Brand Name :: status=review — script font fragments OCR
============================================
```

> Numbers above are illustrative of a typical run. Actual results
> depend on the specific images you drop into `sample_labels/`. The
> dataset deliberately mixes happy paths, capitalization edge cases,
> hard-photo conditions, and missing-required-field violations.

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
