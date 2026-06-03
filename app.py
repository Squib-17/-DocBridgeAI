"""DocBridgeAI — Streamlit UI"""

import io
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

from src.pipeline.detector import detect
from src.pipeline.models import SourceFile
from src.pipeline.pipeline import PipelineConfig, build_cleaner, process_one
from src.pipeline.report import build_report, write_report_json, write_report_markdown

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG = PipelineConfig(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    output_dir="output",
    max_files=int(os.getenv("MAX_FILES", "5")),
    max_file_size_mb=int(os.getenv("MAX_FILE_SIZE_MB", "20")),
)

TABULAR_EXT = {"csv", "xlsx"}

# ── Demo mode config ──────────────────────────────────────────────────────────

DEMO_DIR = Path("demo")
DEMO_FILENAMES = [
    "demo_interactions.csv",
    "demo_interactions.xlsx",
    "demo_autopay_policy.md",
    "demo_autopay_policy.docx",
    "demo_credit_card_policy.pdf",
]
# Pre-configured column selections for tabular demo files
DEMO_COLUMN_DEFAULTS: dict[str, list[str]] = {
    "demo_interactions.csv": ["agent_notes", "conversation_snippet"],
    "demo_interactions.xlsx": ["agent_notes", "conversation_snippet"],
}


class _DemoFile:
    """Wraps a local file to match the Streamlit UploadedFile interface."""
    def __init__(self, path: Path):
        self.name = path.name
        self._data = path.read_bytes()

    def getvalue(self) -> bytes:
        return self._data

METHOD_LABEL = {
    "pymupdf":          "PyMuPDF (text layer)",
    "ocr":              "Tesseract OCR",
    "python-docx":      "python-docx",
    "passthrough":      "Markdown passthrough",
    "pandas_csv":       "pandas",
    "pandas_openpyxl":  "pandas + openpyxl",
    "none":             "—",
}

STATUS_COLOR = {
    "approved": "#1B5E20",
    "review_recommended": "#E65100",
    "review_required": "#B71C1C",
    "rejected": "#B71C1C",
}
STATUS_BG = {
    "approved": "#E8F5E9",
    "review_recommended": "#FFF3E0",
    "review_required": "#FFEBEE",
    "rejected": "#FFEBEE",
}
STATUS_LABEL = {
    "approved": "Approved",
    "review_recommended": "Review Recommended",
    "review_required": "Review Required",
    "rejected": "Rejected",
}

# ── Page setup ─────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DocBridgeAI",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Capital One–inspired design system ────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700;800&family=Barlow:ital,wght@0,400;0,500;0,600;1,400&display=swap');

:root {
    --red:        #CC0000;
    --red-dark:   #990000;
    --red-subtle: #FFF5F5;
    --navy:       #0D1B2A;
    --navy-mid:   #1A2E44;
    --bg:         #F3F4F6;
    --card:       #FFFFFF;
    --text:       #1A1A1A;
    --muted:      #6B7280;
    --border:     #E5E7EB;
    --shadow:     0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md:  0 4px 12px rgba(0,0,0,0.08);
    --radius:     6px;
    --approved-fg: #14532D;
    --approved-bg: #DCFCE7;
    --warn-fg:    #9A3412;
    --warn-bg:    #FFEDD5;
    --danger-fg:  #7F1D1D;
    --danger-bg:  #FEE2E2;
}

/* ── Base ── */
html, body, [class*="css"] {
    font-family: 'Barlow', sans-serif !important;
    color: var(--text) !important;
}
.stApp { background: var(--bg) !important; }
.block-container {
    padding: 0 2rem 4rem !important;
    max-width: 1080px !important;
}
[data-testid="stHeader"] { background: transparent !important; height: 0 !important; }
#MainMenu, footer { visibility: hidden; }
section[data-testid="stSidebar"] { display: none; }

/* ── Typography ── */
h1, h2, h3, h4 {
    font-family: 'Barlow Condensed', sans-serif !important;
    letter-spacing: 0.02em !important;
}

/* ── Primary button ── */
.stButton > button[kind="primary"] {
    background: var(--red) !important;
    border: none !important;
    color: #fff !important;
    font-family: 'Barlow Condensed', sans-serif !important;
    font-size: 1rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    padding: 0.6rem 2.4rem !important;
    border-radius: var(--radius) !important;
    transition: background 0.15s ease, box-shadow 0.15s ease !important;
}
.stButton > button[kind="primary"]:hover {
    background: var(--red-dark) !important;
    box-shadow: 0 3px 10px rgba(204,0,0,0.28) !important;
}
.stButton > button[kind="primary"]:disabled {
    background: #D1D5DB !important;
    color: #9CA3AF !important;
    box-shadow: none !important;
}

/* ── Secondary button (Select All / Clear All) ── */
.stButton > button:not([kind="primary"]) {
    background: white !important;
    border: 1.5px solid var(--red) !important;
    color: var(--red) !important;
    font-family: 'Barlow', sans-serif !important;
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    border-radius: var(--radius) !important;
    transition: background 0.12s ease !important;
}
.stButton > button:not([kind="primary"]):hover {
    background: var(--red-subtle) !important;
}

/* ── Download button ── */
[data-testid="stDownloadButton"] > button {
    background: white !important;
    border: 1.5px solid var(--border) !important;
    color: var(--text) !important;
    font-family: 'Barlow', sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    border-radius: var(--radius) !important;
    width: 100% !important;
    transition: border-color 0.12s ease, color 0.12s ease !important;
}
[data-testid="stDownloadButton"] > button:hover {
    border-color: var(--red) !important;
    color: var(--red) !important;
    background: var(--red-subtle) !important;
}

/* ── Metrics ── */
[data-testid="stMetricValue"] {
    font-family: 'Barlow Condensed', sans-serif !important;
    font-size: 2.1rem !important;
    font-weight: 700 !important;
    color: var(--navy) !important;
    line-height: 1.1 !important;
}
[data-testid="stMetricLabel"] {
    font-family: 'Barlow', sans-serif !important;
    font-size: 0.68rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
    color: var(--muted) !important;
}
[data-testid="stMetricDelta"] { display: none !important; }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 2px !important;
    background: #F9FAFB !important;
    border-radius: var(--radius) var(--radius) 0 0 !important;
    border-bottom: 2px solid var(--border) !important;
    padding: 0 4px !important;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Barlow', sans-serif !important;
    font-size: 0.85rem !important;
    font-weight: 600 !important;
    color: var(--muted) !important;
    padding: 9px 16px !important;
    border-radius: var(--radius) var(--radius) 0 0 !important;
    border-bottom: 2px solid transparent !important;
    transition: color 0.12s ease !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: var(--red) !important;
    border-bottom-color: var(--red) !important;
    background: white !important;
}
.stTabs [data-baseweb="tab-panel"] {
    background: white !important;
    border: 1px solid var(--border) !important;
    border-top: none !important;
    border-radius: 0 0 var(--radius) var(--radius) !important;
    padding: 20px !important;
}

/* ── Expander ── */
[data-testid="stExpander"] {
    background: var(--card) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    box-shadow: var(--shadow) !important;
    margin-bottom: 8px !important;
}
[data-testid="stExpander"] > details > summary {
    font-family: 'Barlow', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    padding: 14px 16px !important;
}
[data-testid="stExpander"] > details > div {
    padding: 0 16px 16px !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: var(--radius) !important;
    font-size: 0.86rem !important;
    border: none !important;
}

/* ── DataFrame ── */
[data-testid="stDataFrame"] {
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
}

/* ── Checkbox ── */
[data-testid="stCheckbox"] label {
    font-family: 'Barlow', sans-serif !important;
    font-size: 0.85rem !important;
    font-weight: 500 !important;
}

/* ── File uploader ── */
[data-testid="stFileUploader"] {
    border-radius: var(--radius) !important;
}

/* ── Multiselect ── */
[data-testid="stMultiSelect"] {
    font-family: 'Barlow', sans-serif !important;
    font-size: 0.85rem !important;
}

/* ── Custom components ── */
.c1-section {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px 24px;
    box-shadow: var(--shadow);
    margin-bottom: 16px;
}
.c1-divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 24px 0;
}
.c1-step-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 0.65rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--red);
    margin-bottom: 2px;
}
.c1-section-title {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.25rem;
    font-weight: 700;
    color: var(--navy);
    letter-spacing: 0.02em;
    margin-bottom: 4px;
}
.c1-section-desc {
    font-size: 0.83rem;
    color: var(--muted);
    margin-bottom: 16px;
}
.mode-badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 10px;
    font-size: 0.7rem;
    font-weight: 700;
    font-family: 'Barlow', sans-serif;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
.mode-doc { background: #EFF6FF; color: #1E40AF; }
.mode-tab { background: #F0FDF4; color: #166534; }
.mode-unk { background: #FEF2F2; color: #991B1B; }
.status-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.72rem;
    font-weight: 700;
    font-family: 'Barlow', sans-serif;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
.opt-row {
    display: flex;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid #F3F4F6;
    gap: 16px;
}
.opt-filename {
    font-family: 'Barlow', sans-serif;
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.opt-type {
    font-family: 'Barlow', sans-serif;
    font-size: 0.72rem;
    color: var(--muted);
    white-space: nowrap;
}
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _source_file(uploaded) -> SourceFile:
    raw = uploaded.getvalue()
    return SourceFile(filename=uploaded.name, size_bytes=len(raw), raw_bytes=raw)

def _ext(uploaded) -> str:
    return uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""

def _is_tabular(uploaded) -> bool:
    return _ext(uploaded) in TABULAR_EXT

def _detect_info(uploaded) -> tuple[str, str]:
    sf = _source_file(uploaded)
    d = detect(sf)
    return d.file_type, d.mode

def _peek_df(uploaded, nrows: int = 3) -> pd.DataFrame | None:
    raw = uploaded.getvalue()
    try:
        if _ext(uploaded) == "csv":
            return pd.read_csv(io.BytesIO(raw), nrows=nrows)
        return pd.read_excel(io.BytesIO(raw), nrows=nrows, engine="openpyxl")
    except Exception:
        return None

def _row_count(uploaded) -> int | None:
    raw = uploaded.getvalue()
    try:
        if _ext(uploaded) == "csv":
            df = pd.read_csv(io.BytesIO(raw))
        else:
            df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
        return len(df)
    except Exception:
        return None


# ── Branded header ─────────────────────────────────────────────────────────────

st.markdown("""
<div style="
    background: linear-gradient(135deg, #0D1B2A 0%, #1A2E44 100%);
    border-radius: 10px;
    padding: 30px 36px 26px;
    margin: 20px 0 8px;
    border-bottom: 4px solid #CC0000;
    position: relative;
    overflow: hidden;
">
  <div style="
      position:absolute; top:0; right:0;
      width:260px; height:100%;
      background: radial-gradient(ellipse at 80% 50%, rgba(204,0,0,0.15) 0%, transparent 70%);
      pointer-events: none;
  "></div>
  <div style="position: relative;">
    <div style="
        font-family:'Barlow Condensed',sans-serif;
        font-size: 2.5rem;
        font-weight: 800;
        color: #FFFFFF;
        letter-spacing: 0.05em;
        line-height: 1;
        margin-bottom: 8px;
    ">DOC<span style="color:#CC0000;">BRIDGE</span>&thinsp;AI</div>
    <div style="
        font-family:'Barlow',sans-serif;
        font-size: 0.88rem;
        color: #94A3B8;
        margin-bottom: 22px;
        letter-spacing: 0.01em;
    ">Enterprise document normalization pipeline &nbsp;&middot;&nbsp; Built for RAG pipelines &amp; AI analytics</div>
    <div style="display:flex; gap:36px; flex-wrap:wrap;">
      <div>
        <div style="font-family:'Barlow',sans-serif;font-size:0.62rem;color:#64748B;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:3px;">Document Formats</div>
        <div style="font-family:'Barlow',sans-serif;font-size:0.82rem;color:#CBD5E1;">PDF &middot; DOCX &middot; MD &rarr; Canonical <code style="background:rgba(255,255,255,0.08);padding:1px 5px;border-radius:3px;font-size:0.78rem;">.md</code></div>
      </div>
      <div>
        <div style="font-family:'Barlow',sans-serif;font-size:0.62rem;color:#64748B;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:3px;">Tabular Formats</div>
        <div style="font-family:'Barlow',sans-serif;font-size:0.82rem;color:#CBD5E1;">CSV &middot; XLSX &rarr; Cleaned <code style="background:rgba(255,255,255,0.08);padding:1px 5px;border-radius:3px;font-size:0.78rem;">.csv</code></div>
      </div>
      <div>
        <div style="font-family:'Barlow',sans-serif;font-size:0.62rem;color:#64748B;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:3px;">Limits</div>
        <div style="font-family:'Barlow',sans-serif;font-size:0.82rem;color:#CBD5E1;">5 files &middot; 20 MB each</div>
      </div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── How it works ───────────────────────────────────────────────────────────────

with st.expander("How it works", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
<div style="font-family:'Barlow Condensed',sans-serif;font-size:1rem;font-weight:700;color:#0D1B2A;letter-spacing:0.03em;margin-bottom:8px;">
DOCUMENT FILES &nbsp;<span style="background:#EFF6FF;color:#1E40AF;font-size:0.7rem;padding:2px 8px;border-radius:10px;font-family:'Barlow',sans-serif;font-weight:700;">PDF &middot; DOCX &middot; MD</span>
</div>""", unsafe_allow_html=True)
        st.markdown("""
- Text is extracted (OCR used automatically for scanned PDFs)
- Repeated headers, footers, and page numbers are removed
- Banking shorthand is expanded — glossary first, then AI for the rest
- Each file gets YAML metadata frontmatter (doc_id, title, doc_type, confidence)
- Output: one clean `.md` file per document, confidence-scored and routed
- **Note:** Documents always output as `.md` — they are not converted to CSV or other formats
""")
    with c2:
        st.markdown("""
<div style="font-family:'Barlow Condensed',sans-serif;font-size:1rem;font-weight:700;color:#0D1B2A;letter-spacing:0.03em;margin-bottom:8px;">
TABULAR FILES &nbsp;<span style="background:#F0FDF4;color:#166534;font-size:0.7rem;padding:2px 8px;border-radius:10px;font-family:'Barlow',sans-serif;font-weight:700;">CSV &middot; XLSX</span>
</div>""", unsafe_allow_html=True)
        st.markdown("""
- You choose which columns contain shorthand or informal text
- Each selected column gets a `_cleaned` companion column added
- Original columns are **never modified** — full audit trail preserved
- A `flags` column and `confidence_score` per row are added to output
- Output: cleaned `.csv` ready for downstream analytics or AI
- **Note:** Tabular files output as `.csv` — they are not converted to markdown
""")

st.markdown('<hr class="c1-divider">', unsafe_allow_html=True)

# ── STEP 1 — Upload ────────────────────────────────────────────────────────────

st.markdown('<div class="c1-step-label">Step 1 of 3</div>', unsafe_allow_html=True)
st.markdown('<div class="c1-section-title">Upload Files</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="c1-section-desc">Upload up to 5 files. Each file is processed independently and gets its own output file and report entry.</div>',
    unsafe_allow_html=True,
)

# ── Demo mode toggle ──────────────────────────────────────────────────────────

demo_mode = st.session_state.get("demo_mode", False)
demo_files_available = all((DEMO_DIR / f).exists() for f in DEMO_FILENAMES)

if not demo_mode:
    # Demo CTA banner
    st.markdown("""
<div style="background:#F8F9FA;border:1.5px dashed #D1D5DB;border-radius:6px;padding:12px 18px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;gap:16px;">
  <div>
    <span style="font-family:'Barlow',sans-serif;font-size:0.85rem;color:#374151;font-weight:600;">New here?</span>
    <span style="font-family:'Barlow',sans-serif;font-size:0.85rem;color:#6B7280;margin-left:8px;">Load 5 pre-built sample files and see the full pipeline in action — mix of CSV, XLSX, Markdown, Word, and PDF.</span>
  </div>
</div>
""", unsafe_allow_html=True)

    demo_col, _ = st.columns([2, 6])
    with demo_col:
        if demo_files_available and st.button("Load Demo Files", key="load_demo"):
            st.session_state["demo_mode"] = True
            # Pre-configure column selections for tabular demo files
            for fname, cols in DEMO_COLUMN_DEFAULTS.items():
                st.session_state[f"cols_{fname}"] = cols
            st.rerun()

    uploaded_files = st.file_uploader(
        "Drop files here or click Browse",
        accept_multiple_files=True,
        type=["pdf", "docx", "md", "csv", "xlsx"],
    )
    effective_files = list(uploaded_files) if uploaded_files else []

else:
    # Demo mode active — bypass the uploader
    st.markdown("""
<div style="background:#F0FDF4;border:1.5px solid #86EFAC;border-radius:6px;padding:12px 18px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
  <div>
    <span style="font-family:'Barlow Condensed',sans-serif;font-size:1rem;font-weight:700;color:#14532D;letter-spacing:0.03em;">DEMO MODE ACTIVE</span>
    <span style="font-family:'Barlow',sans-serif;font-size:0.83rem;color:#166534;margin-left:12px;">5 sample files loaded. Column selections are pre-configured.</span>
  </div>
</div>
""", unsafe_allow_html=True)

    exit_col, _ = st.columns([2, 6])
    with exit_col:
        if st.button("Use your own files", key="exit_demo"):
            st.session_state["demo_mode"] = False
            st.session_state.pop("report", None)
            st.rerun()

    uploaded_files = []
    effective_files = [_DemoFile(DEMO_DIR / f) for f in DEMO_FILENAMES if (DEMO_DIR / f).exists()]

# Validate limits (only for real uploads, not demo)
upload_errors: list[str] = []
if not demo_mode and uploaded_files:
    if len(uploaded_files) > CONFIG.max_files:
        upload_errors.append(f"Too many files — maximum is {CONFIG.max_files}, you uploaded {len(uploaded_files)}.")
    for f in uploaded_files:
        if len(f.getvalue()) > CONFIG.max_file_size_mb * 1024 * 1024:
            upload_errors.append(f"**{f.name}** exceeds the {CONFIG.max_file_size_mb} MB limit ({len(f.getvalue())/1024/1024:.1f} MB).")

for err in upload_errors:
    st.error(err)

upload_ok = bool(effective_files) and not upload_errors

if upload_ok:
    doc_files = [f for f in effective_files if not _is_tabular(f)]
    tab_files = [f for f in effective_files if _is_tabular(f)]

    # File summary table
    rows = []
    for f in effective_files:
        ftype, mode = _detect_info(f)
        mode_label = {"document": "Document", "tabular": "Tabular", "unsupported": "Unsupported"}.get(mode, mode)
        rows.append({"File": f.name, "Size": f"{len(f.getvalue())/1024:.1f} KB", "Detected type": ftype, "Mode": mode_label})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Dynamic callout
    parts = []
    if doc_files:
        names = ", ".join(f"`{f.name}`" for f in doc_files)
        parts.append(f"**{len(doc_files)} document {'file' if len(doc_files)==1 else 'files'}** ({names}) — will extract, clean, and export as canonical `.md` with metadata frontmatter.")
    if tab_files:
        names = ", ".join(f"`{f.name}`" for f in tab_files)
        parts.append(f"**{len(tab_files)} tabular {'file' if len(tab_files)==1 else 'files'}** ({names}) — column configuration required below. Will output cleaned `.csv`.")
    if parts:
        st.info("  \n".join(parts))

st.markdown('<hr class="c1-divider">', unsafe_allow_html=True)

# ── Document processing options ────────────────────────────────────────────────

doc_files = [f for f in effective_files if not _is_tabular(f)] if upload_ok else []

if doc_files:
    st.markdown('<div class="c1-step-label">Document Options</div>', unsafe_allow_html=True)
    st.markdown('<div class="c1-section-title">Processing Options</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="c1-section-desc">Choose what processing to apply to each document. Metadata frontmatter is always added.</div>',
        unsafe_allow_html=True,
    )

    has_api_key = bool(CONFIG.openai_api_key and not CONFIG.openai_api_key.startswith("your-"))

    hdr_cols = st.columns([3, 2, 2, 2])
    hdr_cols[0].markdown("<small style='color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;font-size:0.7rem;'>File</small>", unsafe_allow_html=True)
    hdr_cols[1].markdown("<small style='color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;font-size:0.7rem;'>Type</small>", unsafe_allow_html=True)
    hdr_cols[2].markdown("<small style='color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;font-size:0.7rem;'>Expand Shorthand</small>", unsafe_allow_html=True)
    hdr_cols[3].markdown("<small style='color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;font-size:0.7rem;'>Generate Headings</small>", unsafe_allow_html=True)

    for f in doc_files:
        ftype, _ = _detect_info(f)
        shorthand_key = f"opt_shorthand_{f.name}"
        headings_key = f"opt_headings_{f.name}"
        row_cols = st.columns([3, 2, 2, 2])
        row_cols[0].markdown(f"**{f.name}**")
        row_cols[1].markdown(f"<span style='font-size:0.8rem;color:#6B7280;'>{ftype}</span>", unsafe_allow_html=True)
        with row_cols[2]:
            st.checkbox("On", value=True, key=shorthand_key, label_visibility="collapsed")
        with row_cols[3]:
            if has_api_key:
                st.checkbox("On", value=False, key=headings_key, label_visibility="collapsed")
            else:
                st.markdown("<small style='color:#9CA3AF;'>Needs API key</small>", unsafe_allow_html=True)

    api_note = "" if has_api_key else " &nbsp;·&nbsp; **Generate Headings** requires an OpenAI API key in `.env`."
    st.caption(
        f"Metadata frontmatter is always added. Headers/footers are removed automatically for PDF and DOCX."
        f"{api_note} Generate Headings is automatically skipped if the document already has heading structure — no API cost incurred for structured files like DOCX."
    )
    st.markdown('<hr class="c1-divider">', unsafe_allow_html=True)

# ── STEP 2 — Column selector (tabular) ────────────────────────────────────────

tab_files = [f for f in effective_files if _is_tabular(f)] if upload_ok else []
column_selections: dict[str, list[str]] = {}

if tab_files:
    st.markdown('<div class="c1-step-label">Step 2 of 3</div>', unsafe_allow_html=True)
    st.markdown('<div class="c1-section-title">Configure Columns to Clean</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="c1-section-desc">Select which columns contain shorthand or informal text. Original columns are preserved unchanged.</div>',
        unsafe_allow_html=True,
    )

    tabs_ui = st.tabs([f.name for f in tab_files])

    for tab_ui, f in zip(tabs_ui, tab_files):
        with tab_ui:
            preview_df = _peek_df(f)
            all_cols = list(preview_df.columns) if preview_df is not None else []

            if not all_cols:
                st.warning(f"Could not read columns from `{f.name}`.")
                column_selections[f.name] = []
                continue

            # XLSX single-sheet note
            if _ext(f) == "xlsx":
                st.info("Only the first sheet is processed. To process a different sheet, move it to the first position in your Excel file before uploading.")

            # Row count + preview
            nrows = _row_count(f)
            row_label = f"{nrows:,} rows · {len(all_cols)} columns" if nrows else f"{len(all_cols)} columns"
            st.caption(f"Preview — {row_label}")
            if preview_df is not None:
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

            # Select All / Clear All
            ss_key = f"cols_{f.name}"
            if ss_key not in st.session_state:
                st.session_state[ss_key] = []

            b1, b2, _ = st.columns([1, 1, 6])
            if b1.button("Select All", key=f"sa_{f.name}", use_container_width=True):
                st.session_state[ss_key] = all_cols
                st.rerun()
            if b2.button("Clear All", key=f"ca_{f.name}", use_container_width=True):
                st.session_state[ss_key] = []
                st.rerun()

            selected = st.multiselect(
                "Columns to clean:",
                options=all_cols,
                key=ss_key,
                placeholder="Select columns to expand shorthand in...",
            )
            column_selections[f.name] = selected

            if not selected:
                st.caption("No columns selected — this file will be processed but shorthand expansion will not be applied.")

    st.markdown('<hr class="c1-divider">', unsafe_allow_html=True)

# ── STEP 3 — Process ──────────────────────────────────────────────────────────

st.markdown('<div class="c1-step-label">Step 3 of 3</div>', unsafe_allow_html=True)
st.markdown('<div class="c1-section-title">Process Files</div>', unsafe_allow_html=True)

if upload_ok:
    doc_count = len(doc_files)
    tab_count = len(tab_files)
    summary_parts = []
    if doc_count:
        summary_parts.append(f"{doc_count} document {'file' if doc_count==1 else 'files'}")
    if tab_count:
        configured = sum(1 for f in tab_files if column_selections.get(f.name))
        summary_parts.append(f"{tab_count} tabular {'file' if tab_count==1 else 'files'} ({configured} configured)")

    st.markdown(
        f'<div class="c1-section-desc">Ready to process: {", ".join(summary_parts)}. Outputs and a processing report will be written to the <code>output/</code> folder.</div>',
        unsafe_allow_html=True,
    )

if upload_ok:
    if st.button("Process Files", type="primary"):
        # Collect per-file options
        file_options: dict[str, dict] = {}
        for f in doc_files:
            file_options[f.name] = {
                "expand_shorthand": st.session_state.get(f"opt_shorthand_{f.name}", True),
                "generate_headings": st.session_state.get(f"opt_headings_{f.name}", False),
            }

        source_files = [_source_file(f) for f in effective_files]
        total = len(source_files)

        try:
            cleaner = build_cleaner(CONFIG)
            results = []

            with st.status("Processing files...", expanded=True) as status:
                for i, source in enumerate(source_files):
                    cols = column_selections.get(source.filename, [])
                    opts = file_options.get(source.filename, {})

                    mode_hint = "tabular" if source.filename.rsplit(".", 1)[-1].lower() in {"csv", "xlsx"} else "document"
                    llm_note = " — LLM calls in progress" if CONFIG.openai_api_key and not CONFIG.openai_api_key.startswith("your-") else ""
                    st.write(f"**{source.filename}** ({i + 1} of {total}) — {mode_hint}{llm_note}")

                    result = process_one(source, cols, CONFIG, options=opts, cleaner=cleaner)
                    results.append(result)

                status.update(label="Writing processing report...")
                Path(CONFIG.output_dir).mkdir(parents=True, exist_ok=True)
                report = build_report(results)
                write_report_json(report, CONFIG.output_dir)
                write_report_markdown(report, CONFIG.output_dir)
                status.update(
                    label=f"Done — {report.total_files} file{'s' if report.total_files != 1 else ''} processed. {report.approved} approved, {report.review_recommended + report.review_required} flagged, {report.rejected} rejected.",
                    state="complete",
                )

            st.session_state["report"] = report
            st.rerun()
        except ValueError as e:
            st.error(str(e))
            st.session_state.pop("report", None)
else:
    st.button("Process Files", type="primary", disabled=True)

# ── Results ───────────────────────────────────────────────────────────────────

report = st.session_state.get("report")

if report:
    st.markdown('<hr class="c1-divider">', unsafe_allow_html=True)
    st.markdown('<div class="c1-section-title" style="font-size:1.4rem;">Results</div>', unsafe_allow_html=True)

    # Summary metrics
    mc = st.columns(5)
    mc[0].metric("Total", report.total_files)
    mc[1].metric("Approved", report.approved)
    mc[2].metric("Review Rec.", report.review_recommended)
    mc[3].metric("Review Req.", report.review_required)
    mc[4].metric("Rejected", report.rejected)

    st.markdown("<br>", unsafe_allow_html=True)

    # Per-file result cards
    for fr in report.files:
        color = STATUS_COLOR.get(fr.status, "#374151")
        bg = STATUS_BG.get(fr.status, "#F9FAFB")
        label = STATUS_LABEL.get(fr.status, fr.status)

        badge_html = (
            f'<span class="status-chip" style="background:{bg};color:{color};">'
            f'<span style="width:6px;height:6px;border-radius:50%;background:{color};display:inline-block;"></span>'
            f'{label}</span>'
        )

        with st.expander(f"{fr.filename}", expanded=True):
            method_readable = METHOD_LABEL.get(fr.extraction_method, fr.extraction_method)
            llm_badge = (
                '<span style="background:#EFF6FF;color:#1E40AF;font-size:0.68rem;font-weight:700;'
                'font-family:\'Barlow\',sans-serif;padding:2px 8px;border-radius:10px;letter-spacing:0.04em;">GPT-4o-mini</span>'
                if fr.llm_used else
                '<span style="background:#F3F4F6;color:#9CA3AF;font-size:0.68rem;font-weight:600;'
                'font-family:\'Barlow\',sans-serif;padding:2px 8px;border-radius:10px;letter-spacing:0.04em;">Glossary only</span>'
            )
            st.markdown(f"""
<div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap;padding:4px 0 12px;">
  {badge_html}
  <div style="display:flex;gap:28px;flex-wrap:wrap;align-items:flex-end;">
    <div>
      <div style="font-size:0.65rem;color:#6B7280;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;font-family:'Barlow',sans-serif;">Confidence</div>
      <div style="font-size:1.6rem;font-family:'Barlow Condensed',sans-serif;font-weight:700;color:#0D1B2A;line-height:1.1;">{fr.confidence_score:.2f}</div>
    </div>
    <div>
      <div style="font-size:0.65rem;color:#6B7280;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;font-family:'Barlow',sans-serif;">Type</div>
      <div style="font-size:0.88rem;font-family:'Barlow',sans-serif;font-weight:600;color:#1A1A1A;margin-top:3px;">{fr.file_type}</div>
    </div>
    <div>
      <div style="font-size:0.65rem;color:#6B7280;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;font-family:'Barlow',sans-serif;">Extraction</div>
      <div style="font-size:0.88rem;font-family:'Barlow',sans-serif;font-weight:600;color:#1A1A1A;margin-top:3px;">{method_readable}</div>
    </div>
    <div>
      <div style="font-size:0.65rem;color:#6B7280;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;font-family:'Barlow',sans-serif;">AI Used</div>
      <div style="margin-top:4px;">{llm_badge}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

            # Issues
            if fr.issues:
                for issue in fr.issues:
                    sev = issue.get("severity", "warning")
                    msg = issue.get("message", "")
                    if sev == "error":
                        st.error(msg)
                    else:
                        st.warning(msg)

            if fr.output_path:
                out_name = Path(fr.output_path).name
                st.markdown(f"""
<div style="background:#F0FDF4;border-radius:6px;padding:10px 14px;margin-top:6px;">
  <span style="font-family:'Barlow',sans-serif;font-size:0.84rem;color:#166534;font-weight:500;">Output written to </span>
  <span style="font-family:'Barlow',sans-serif;font-size:0.84rem;color:#166534;font-weight:700;">{out_name}</span>
  <span style="font-family:'Barlow',sans-serif;font-size:0.78rem;color:#4B7A5A;margin-left:8px;">({fr.output_path})</span>
</div>
""", unsafe_allow_html=True)
            elif fr.rejection_reason:
                st.error(f"Rejection reason: {fr.rejection_reason}")

    # What's next
    if report.approved > 0 or report.review_recommended > 0 or report.review_required > 0:
        parts = []
        if report.approved > 0:
            parts.append(f"**{report.approved} approved** — output files are clean and ready to ingest into your RAG pipeline or analytics system.")
        flagged = report.review_recommended + report.review_required
        if flagged > 0:
            parts.append(f"**{flagged} flagged** — output files have been written but are annotated with quality flags. Review the issues above before ingesting.")
        if report.rejected > 0:
            parts.append(f"**{report.rejected} rejected** — no output written for these files. See rejection reasons above.")

        st.markdown("<br>", unsafe_allow_html=True)
        st.info("**What to do with your results:**\n\n" + "\n\n".join(parts))

    # Report preview
    report_md_path = Path(CONFIG.output_dir) / "processing_report.md"
    if report_md_path.exists():
        with st.expander("Processing report (full)", expanded=False):
            st.markdown(report_md_path.read_text())

    # ── Downloads ─────────────────────────────────────────────────────────────

    st.markdown('<hr class="c1-divider">', unsafe_allow_html=True)
    st.markdown('<div class="c1-section-title">Download Output Files</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="c1-section-desc">Download your cleaned files and the full processing report.</div>',
        unsafe_allow_html=True,
    )

    output_dir = Path(CONFIG.output_dir)
    dl_items = []

    for fr in report.files:
        if fr.output_path:
            p = Path(fr.output_path)
            if p.exists():
                mime = "text/markdown" if p.suffix == ".md" else "text/csv"
                dl_items.append((p.name, p.read_bytes(), mime))

    report_json = output_dir / "processing_report.json"
    report_md = output_dir / "processing_report.md"
    if report_json.exists():
        dl_items.append(("processing_report.json", report_json.read_bytes(), "application/json"))
    if report_md.exists():
        dl_items.append(("processing_report.md", report_md.read_bytes(), "text/markdown"))

    if dl_items:
        cols = st.columns(3)
        for i, (name, data, mime) in enumerate(dl_items):
            with cols[i % 3]:
                st.download_button(
                    label=f"↓  {name}",
                    data=data,
                    file_name=name,
                    mime=mime,
                    use_container_width=True,
                    key=f"dl_{i}_{name}",
                )
