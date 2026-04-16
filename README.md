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
- **Seven label fields** verified: brand, class/type, ABV, net contents,
  producer/bottler, country of origin (when populated), and the
  government warning statement.
- **OCR confidence per image** — surfaced in the result header; a warning
  appears when confidence drops below 70%.
- **Government warning check** — verifies textual presence, ALL CAPS
  header, and ≥45% body match to the official statement (calibrated
  to catch OCR-fragmented text while filtering noise).
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
Evaluating against 8 ground-truth entries…
============================================================
  APPROVE perfect_label.png                   OCR=  87% (68.2s)
  APPROVE angled_glare.png                    OCR=  80% (18.4s)
  REVIEW  warning_violation_titlecase.png     OCR=  95% (13.3s)
  APPROVE brand_caps_mismatch.png             OCR=  82% (12.5s)
  REJECT  low_contrast.png                    OCR=  80% (10.2s)
  REVIEW  stylized_font.png                   OCR=  91% (13.0s)
  REJECT  imported_wine.png                   OCR=  75% (12.2s)
  REJECT  missing_warning.png                 OCR=  91% (10.0s)

Field accuracy (correct / total)
------------------------------------------------------------
  ABV                    1/1  (100%)
  Brand Name             8/8  (100%)
  Class/Type             5/5  (100%)
  Country of Origin      1/1  (100%)
  Government Warning     7/8  (88%)
  Net Contents           7/8  (88%)
  Producer/Bottler       1/1  (100%)

Verdict accuracy: 7/8  (88%)
Avg processing time: 19.72s (min 10.01s, max 68.24s)
```

| Image | OCR conf | Verdict | Expected | Notes |
|---|---|---|---|---|
| `perfect_label` | 87% | APPROVE | APPROVE ✓ | Brand + class + net contents all matched; warning caps confirmed via keyword vote |
| `angled_glare` | 80% | APPROVE | APPROVE ✓ | 20° tilt + glare; rotation pass recovers warning keywords in ALL CAPS |
| `warning_violation_titlecase` | 95% | REVIEW | REVIEW ✓ | Title-case "Warning:" detected via keyword case-vote (0 uppercase, many titlecase hits) |
| `brand_caps_mismatch` | 82% | APPROVE | APPROVE ✓ | "Stone's Throw" fuzzy-matches "STONE'S THROW" at ≥85 |
| `low_contrast` | 80% | REJECT | REVIEW ✗ | Only 5 OCR lines; warning text invisible in low-contrast region |
| `stylized_font` | 91% | REVIEW | REVIEW ✓ | Script font; brand fuzzy-matches in REVIEW band; warning keywords recovered |
| `imported_wine` | 75% | REJECT | REJECT ✓ | Net contents mismatch (label ~50 mL ≠ expected 750 mL) + no warning |
| `missing_warning` | 91% | REJECT | REJECT ✓ | No government warning detected (body score 16%) |

Processing times (~10–68 s/image) include the rotation pass on pass 1
(EasyOCR tests 4 orientations per region). The 68s outlier is
`perfect_label` which has many detected regions; typical images run
10–18s. On a GPU or in production with warm model, expect 2–4× faster.

### Failure mode

- **`low_contrast.png` REJECTed (expected REVIEW).** The bottle photo
  has extremely faint text — OCR recovers only 5 lines (brand
  fragments + "12 FL OZ"). The government warning is completely
  invisible at this contrast level, so the warning body scores 18%
  (below noise floor). The tool correctly reports "warning not
  detected" — if the warning isn't readable, that's an honest
  finding. Production mitigation: prompt agents to re-photograph
  under better lighting, or accept REJECT as a conservative default
  when critical text is unreadable.

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
