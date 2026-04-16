"""
Streamlit UI for the TTB Alcohol Label Verifier.

Layout:
    Sidebar  — application data form (brand, class/type, ABV, net contents,
               producer/bottler, country of origin, beverage type,
               warning check toggle).
    Main     — drag-and-drop batch uploader + a 'Verify Labels' button.
               Results render below, REJECT first, then REVIEW, then
               APPROVE. Each result shows OCR confidence, per-field
               side-by-side comparison, and a 'show raw OCR' expander.
               A 'Download CSV' button at the bottom serializes the
               full batch.

Design notes:
    * The sidebar form is pre-populated with a realistic bourbon example
      so evaluators can click through with zero typing.
    * Heavy resources (the EasyOCR Reader) are warmed up once on app
      start and cached with @st.cache_resource.
    * Batch processing runs OCR calls concurrently in a ThreadPoolExecutor.
      EasyOCR releases the GIL during inference, so this gives a real
      wall-clock speedup even on Streamlit Cloud's shared CPU.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st
from PIL import Image

import ocr
from matcher import (
    VerificationResult,
    extract_fields,
    overall_verdict,
    validate_fields,
)
from utils import (
    BEVERAGE_TYPES,
    STATUS_NOT_FOUND,
    VERDICT_APPROVE,
    VERDICT_REJECT,
    VERDICT_REVIEW,
    VERDICT_SEVERITY,
    format_confidence,
    get_match_emoji,
    get_verdict_emoji,
    results_to_csv,
)


st.set_page_config(
    page_title="TTB Label Verifier",
    page_icon="🍷",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* --- Layered surfaces: expanders, sidebar, metric cards --- */
    [data-testid="stExpander"] {
        background: #1C1C1E;
        border: 1px solid rgba(212, 160, 23, 0.15);
        border-radius: 12px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
    }
    section[data-testid="stSidebar"] > div {
        border-right: 1px solid rgba(212, 160, 23, 0.12);
    }

    /* --- Typography: bolder text on dark backgrounds --- */
    [data-testid="stExpander"] summary span {
        font-weight: 600 !important;
    }
    [data-testid="stMetricValue"] {
        font-weight: 700 !important;
    }

    /* --- Light text inside forced-dark expander (fixes light-mode contrast) --- */
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary *,
    [data-testid="stExpander"] [data-testid="stCaptionContainer"],
    [data-testid="stExpander"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stExpander"] [data-testid="stCheckbox"] label {
        color: #EAEAEA !important;
    }

    /* --- Upload drop zone: amber dashed border --- */
    [data-testid="stFileUploader"] section {
        border: 2px dashed rgba(212, 160, 23, 0.4) !important;
        border-radius: 12px;
        transition: border-color 0.2s ease;
    }
    [data-testid="stFileUploader"] section:hover {
        border-color: rgba(212, 160, 23, 0.7) !important;
    }

    /* --- Buttons: smooth transitions --- */
    button[kind="primary"], button[data-testid="stBaseButton-primary"] {
        transition: all 0.2s ease !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
    }
    button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(212, 160, 23, 0.3);
    }
    button[kind="primary"][disabled], button[data-testid="stBaseButton-primary"][disabled] {
        opacity: 0.45 !important;
        cursor: not-allowed !important;
        transform: none !important;
        box-shadow: none !important;
    }

    /* --- Low-OCR warning banner: warm amber left border --- */
    [data-testid="stAlert"] {
        border-left: 4px solid #D4A017 !important;
        border-radius: 8px;
    }

    /* --- Checkbox label: bolder for readability --- */
    [data-testid="stCheckbox"] label span {
        font-weight: 500 !important;
    }

    /* --- Dropdown: selected/hover item contrast --- */
    [data-baseweb="select"] [role="option"][aria-selected="true"],
    [data-baseweb="select"] [role="option"]:hover {
        background-color: rgba(212, 160, 23, 0.2) !important;
    }
    [data-baseweb="popover"] [data-baseweb="menu"] {
        background-color: #1A1A1C !important;
        border: 1px solid #333 !important;
    }

    /* --- Spacing & polish --- */
    [data-testid="stImage"] {
        border-radius: 8px;
        overflow: hidden;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Cached resources
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner="Loading OCR model (one-time, ~30s)…")
def _load_reader():
    """Load the EasyOCR model exactly once per Streamlit process."""
    ocr.warm_up()
    return True


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def process_single_label(
    image_bytes: bytes,
    image_name: str,
    expected: dict,
) -> VerificationResult:
    """Run OCR + matching on a single image. Pure function (safe to thread)."""
    t0 = time.perf_counter()
    pil_img = Image.open(BytesIO(image_bytes))
    ocr_result = ocr.extract_text(pil_img)
    extracted = extract_fields(ocr_result["full_text"], ocr_result["lines"])
    fields = validate_fields(extracted, expected)
    verdict = overall_verdict(fields)
    return VerificationResult(
        image_name=image_name,
        fields=fields,
        overall_verdict=verdict,
        ocr_confidence=ocr_result["avg_confidence"],
        processing_time=time.perf_counter() - t0,
        beverage_type=expected.get("beverage_type", ""),
        raw_ocr_text=ocr_result["full_text"],
    )


def _process_payloads(
    payloads: list[tuple[str, bytes]], expected: dict,
) -> list[VerificationResult]:
    """Process (name, bytes) pairs in parallel with a progress bar."""
    total = len(payloads)
    if total == 0:
        return []
    progress = st.progress(0.0, text=f"Processing 0 / {total}…")
    results: list[VerificationResult] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(process_single_label, b, n, expected): n
            for n, b in payloads
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                name = futures[fut]
                st.warning(f"Failed to process **{name}**: {exc}")
            progress.progress(
                len(results) / total,
                text=f"Processing {len(results)} / {total}…",
            )
    progress.empty()
    results.sort(
        key=lambda r: (VERDICT_SEVERITY.get(r.overall_verdict, 99), r.image_name)
    )
    return results


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render_field_table(fields) -> None:
    """Render the per-field comparison as a styled DataFrame."""
    rows = []
    for f in fields:
        rows.append(
            {
                "Status": f"{get_match_emoji(f.status)} {f.status.replace('_', ' ').title()}",
                "Field": f.field_name,
                "Expected": f.expected or "—",
                "Found on label": f.extracted or "—",
                "Score": f"{f.score:.0f}%" if f.status != STATUS_NOT_FOUND or f.score else "—",
                "Notes": f.notes,
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)


def _render_verdict_badge(verdict: str) -> str:
    color = {
        VERDICT_APPROVE: "#22C55E",
        VERDICT_REVIEW: "#F4B400",
        VERDICT_REJECT: "#DC2626",
    }.get(verdict, "#6e7781")
    return (
        f"<span style='background-color:{color}; color:white; padding:4px 10px; "
        f"border-radius:6px; font-weight:600;'>{get_verdict_emoji(verdict)} {verdict}</span>"
    )


def _render_result(result: VerificationResult, image_bytes_lookup: dict) -> None:
    header = (
        f"{get_verdict_emoji(result.overall_verdict)} **{result.image_name}** "
        f"— OCR confidence: {format_confidence(result.ocr_confidence)} "
        f"· {result.processing_time:.1f}s"
    )
    expanded_default = result.overall_verdict != VERDICT_APPROVE
    with st.expander(header, expanded=expanded_default):
        col_img, col_meta = st.columns([1, 3])
        with col_img:
            img_bytes = image_bytes_lookup.get(result.image_name)
            if img_bytes:
                st.image(img_bytes, use_container_width=True)
        with col_meta:
            st.markdown(_render_verdict_badge(result.overall_verdict), unsafe_allow_html=True)
            if result.ocr_confidence and result.ocr_confidence < 0.70:
                st.warning(
                    "Low OCR confidence — image may be hard to read. "
                    "Consider retaking the photo with better lighting "
                    "and a straight-on angle."
                )
            if result.beverage_type:
                st.caption(f"Beverage type: {result.beverage_type}")
            _render_field_table(result.fields)
            if st.checkbox("Show raw OCR text", key=f"ocr_{result.image_name}"):
                st.code(result.raw_ocr_text or "(no text recognized)", language=None)


# ---------------------------------------------------------------------------
# Sidebar — application data
# ---------------------------------------------------------------------------


def _sidebar_form() -> dict:
    st.sidebar.title("Application Data")
    st.sidebar.caption(
        "These values come from the COLA application. They will be checked "
        "against text extracted from each label image."
    )
    beverage_type = st.sidebar.selectbox(
        "Beverage Type",
        BEVERAGE_TYPES,
        index=0,
        help="Informational in this prototype. Type-specific rule "
             "variations are noted as a future enhancement.",
    )
    brand = st.sidebar.text_input("Brand Name", value="OLD TOM DISTILLERY")
    class_type = st.sidebar.text_input(
        "Class / Type", value="Kentucky Straight Bourbon Whiskey",
    )
    abv = st.sidebar.text_input("Alcohol Content (ABV)", value="45%")
    net_contents = st.sidebar.text_input("Net Contents", value="750 mL")
    producer = st.sidebar.text_input(
        "Producer / Bottler",
        value="Old Tom Distillery, Louisville, KY",
        help="Address lines are OCR-fragile — this field uses lower "
             "fuzzy thresholds than the brand name.",
    )
    country = st.sidebar.text_input(
        "Country of Origin",
        value="",
        help="Leave blank for domestic products. When populated, the "
             "label must mention the country.",
    )
    check_warning = st.sidebar.checkbox(
        "Verify Government Warning statement",
        value=True,
    )

    st.sidebar.markdown("---")
    with st.sidebar.expander("How it works"):
        st.markdown(
            "- **Fuzzy matching** handles capitalization and punctuation "
            "differences (e.g. \"STONE'S THROW\" vs \"Stone's Throw\").\n"
            "- This tool is a **first-pass filter** — agents retain final "
            "judgment for nuanced cases.\n"
            "- Images with low OCR confidence get a **retake suggestion** "
            "(better lighting, straight-on angle).\n"
            "- OCR verifies **text content and capitalization** only — bold, "
            "font size, and physical placement require visual review."
        )
    st.sidebar.caption(
        "Tip: drop multiple label photos in the main panel — the tool "
        "processes them in parallel and surfaces problems first."
    )

    return {
        "beverage_type": beverage_type,
        "brand": brand.strip(),
        "class_type": class_type.strip(),
        "abv": abv.strip(),
        "net_contents": net_contents.strip(),
        "producer": producer.strip(),
        "country_of_origin": country.strip(),
        "check_warning": check_warning,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    expected = _sidebar_form()

    st.title("🍷 TTB Alcohol Label Verifier")
    st.markdown(
        "Upload one or more label photos. The tool extracts text from each "
        "image and compares it against the application data on the left, "
        "field by field. **Failures and reviews appear first.**"
    )

    # Kick off model load (cached) early so the spinner is visible up front
    # rather than on the first verify click.
    _load_reader()

    uploaded = st.file_uploader(
        "Drop label images here (JPG, PNG; multiple files supported)",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"],
        accept_multiple_files=True,
    )

    is_processing = st.session_state.get("processing", False)

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        run = st.button(
            "⏳ Processing…" if is_processing else "▶ Verify Labels",
            type="primary",
            use_container_width=True,
            disabled=is_processing,
        )
    with col_info:
        if uploaded:
            st.caption(f"{len(uploaded)} image(s) ready.")
        else:
            st.caption("Awaiting label uploads…")

    # Validate that at least one application field has been provided.
    if run and not any(
        expected[k] for k in ("brand", "class_type", "abv", "net_contents", "producer")
    ):
        st.error(
            "Please fill in at least one application field on the left "
            "(brand, class/type, ABV, net contents, or producer)."
        )
        return
    if run and not uploaded:
        st.error("Please upload at least one label image.")
        return

    if run:
        st.session_state["processing"] = True
        st.session_state["pending_bytes"] = {f.name: f.getvalue() for f in uploaded}
        st.session_state["pending_expected"] = expected
        st.rerun()

    if is_processing:
        pending_bytes = st.session_state.pop("pending_bytes", {})
        pending_expected = st.session_state.pop("pending_expected", expected)
        payloads = list(pending_bytes.items())
        if payloads:
            t0 = time.perf_counter()
            results_list = _process_payloads(payloads, pending_expected)
            elapsed = time.perf_counter() - t0
            st.session_state["results"] = results_list
            st.session_state["batch_time"] = elapsed
            st.session_state["image_bytes_lookup"] = pending_bytes
        st.session_state["processing"] = False
        st.rerun()

    results = st.session_state.get("results")
    image_bytes_lookup = st.session_state.get("image_bytes_lookup", {})

    if results:
        # Summary tally.
        tally = {VERDICT_APPROVE: 0, VERDICT_REVIEW: 0, VERDICT_REJECT: 0}
        for r in results:
            tally[r.overall_verdict] = tally.get(r.overall_verdict, 0) + 1
        batch_time = st.session_state.get("batch_time")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("✅ Approve", tally.get(VERDICT_APPROVE, 0))
        c2.metric("⚠️ Review", tally.get(VERDICT_REVIEW, 0))
        c3.metric("❌ Reject", tally.get(VERDICT_REJECT, 0))
        if batch_time is not None:
            c4.metric("⏱ Total time", f"{batch_time:.1f}s")

        st.markdown("### Results")
        st.caption("Sorted by severity — failures first.")
        for result in results:
            _render_result(result, image_bytes_lookup)

        st.download_button(
            "📥 Download batch results as CSV",
            data=results_to_csv(results),
            file_name=f"ttb_label_verification_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

    st.markdown("---")
    st.caption(
        "This tool is a first-pass filter to assist compliance agents — "
        "not a final compliance authority. Agents retain judgment for "
        "nuanced cases."
    )


if __name__ == "__main__":
    main()
