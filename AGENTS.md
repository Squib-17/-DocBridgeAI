# AGENTS.md — DocBridgeAI

Read this before writing any code for this project.

---

## What DocBridgeAI Is

DocBridgeAI is a standalone document normalization pipeline. It takes messy enterprise source files — PDFs, scanned documents, Word docs, Markdown, CSV/Excel — and produces clean, validated, metadata-rich output files that downstream AI systems (RAG pipelines, analytics, LLM agents) can ingest directly.

It is designed as a reusable upstream layer. Any project that needs to go from raw documents to structured, clean knowledge can use it. The two primary consumers in this portfolio are:

- **NextGenCapitalRAG** — feeds clean markdown into a RAG retrieval system for a banking assistant
- **AIServicingIntelligence** — feeds cleaned tabular data (customer interaction logs) into an analytics and AI servicing layer

DocBridgeAI solves the problem that most RAG tutorials skip: real enterprise knowledge does not start as clean markdown.

---

## Session Startup Flow

At the start of every session, read these files in order:

1. `CLAUDE.md`
2. `AGENTS.md`
3. `private/PROJECT_STATE.md`
4. `private/NEXT_ACTIONS.md`

At the end of every session, update:

- `private/PROJECT_STATE.md`
- `private/NEXT_ACTIONS.md`

---

## Core Design Principle

DocBridgeAI does not guess. It routes.

- High-confidence output → auto-approved, written to output folder
- Flagged output → written with review annotations, clearly marked
- Rejected output → processing report explains why, no output file written

Quality validation is a first-class feature, not an afterthought.

---

## Two Processing Modes

### Document Mode
Input: PDF (readable or scanned), DOCX, Markdown
Output: A single canonical `.md` file per input document, with YAML frontmatter

Use case: Preparing documents for a RAG knowledge base. One document in, one clean markdown out.

### Tabular Mode
Input: CSV or XLSX
Output: A cleaned `.csv` with original columns preserved, new `[col]_cleaned` columns, a `flags` column, and a `confidence_score` column per row

Use case: Cleaning structured data such as customer interaction logs, agent notes, or support records where shorthand, abbreviations, and informal language need to be normalized.

---

## Supported File Types (v1)

| Format | Mode | Extraction method |
|--------|------|-------------------|
| `.md` | Document | Passthrough with metadata enrichment |
| `.pdf` (readable) | Document | PyMuPDF |
| `.pdf` (scanned) | Document | OCR via pytesseract (Tesseract backend) |
| `.docx` | Document | python-docx |
| `.csv` | Tabular | pandas |
| `.xlsx` | Tabular | pandas + openpyxl |

Audio is out of scope for v1. It is noted in the README as a designed-for future extension.

---

## Upload Constraints

- Maximum 5 files per session
- Maximum 20 MB per file
- Mixed file types allowed in a single session
- Each file is processed independently and gets its own section in the processing report

---

## Target Project Structure

```
docbridgeai/
  src/
    pipeline/
      __init__.py
      models.py          # SourceFile, ProcessedDocument, ProcessingIssue, ProcessingReport
      detector.py        # file type detection, mode routing (document vs tabular)
      extractors.py      # per-format extraction classes, all return ExtractedContent
      cleaner.py         # text cleaning, shorthand expansion (glossary + LLM)
      validator.py       # confidence scoring, quality checks, flag/route logic
      exporter.py        # write canonical .md (document mode) or .csv (tabular mode)
      report.py          # build ProcessingReport as JSON + Markdown
      pipeline.py        # orchestrates all steps for 1–5 files
    glossary/
      financial.json     # banking/fintech shorthand dictionary
  app.py                 # Streamlit UI
  output/                # processed files land here (gitignored)
  .env                   # local secrets (gitignored)
  .env.example           # placeholder template (committed)
  pyproject.toml
  README.md
```

---

## Build Order

Phase 1 — Core pipeline (no UI):
1. `models.py` — data objects
2. `detector.py` — file type + mode detection
3. `extractors.py` — extraction classes for all 6 formats
4. `cleaner.py` — text normalization + shorthand expansion
5. `validator.py` — confidence scoring + routing logic
6. `exporter.py` — output writing
7. `report.py` — processing report generation
8. `pipeline.py` — wires all steps together

Phase 2 — Streamlit UI:
1. File upload (up to 5, size-gated)
2. Column selector for tabular mode
3. Processing progress display
4. Output preview
5. Download buttons (output files + processing report)

Phase 3 — Hardening:
1. Error handling for corrupt or unreadable files
2. Duplicate detection (hash-based)
3. OCR confidence fallback messaging
4. README scalability section
5. End-to-end test with NextGenCapitalRAG sample docs and AIServicingIntelligence sample data

---

## Cleaning and Shorthand Expansion

Two-layer approach:

**Layer 1 — Domain glossary** (`glossary/financial.json`):
Apply a dictionary of known banking/fintech abbreviations before any LLM call. This is fast, deterministic, and auditable.

Examples: `acct → account`, `cust → customer`, `bal → balance`, `txn → transaction`, `auth → authorization`, `pymnt → payment`, `stmnt → statement`, `lmt → limit`, `avail → available`, `chrgbck → chargeback`

**Layer 2 — LLM expansion** (GPT-4o-mini):
After glossary substitution, send remaining text to the LLM with a prompt that instructs it to expand remaining informal language, abbreviations, and shorthand into clean readable English. Do not paraphrase or change meaning. Preserve all facts.

Log which terms were expanded by glossary vs. LLM for the processing report.

---

## Quality Validation

Validation does not block processing. It routes output.

### Document mode checks:
- Extraction confidence (character yield, structure detection)
- Minimum content length (reject if under threshold)
- Garbled character ratio (flag if above threshold, e.g. >10%)
- Required metadata presence (doc_id, title, doc_type)
- Duplicate detection (SHA-256 hash of extracted text)
- Language detection

### Tabular mode checks:
- Column mapping validation (did we find expected columns?)
- Null/empty field ratio per selected column
- Shorthand density (ratio of expanded terms to total tokens — high ratio = flag for review)
- Row-level confidence score (based on how many tokens needed LLM expansion)

### Routing logic:
```
confidence >= 0.85  → status: approved
0.60 <= confidence < 0.85 → status: review_recommended
confidence < 0.60  → status: review_required
extraction failed  → status: rejected
```

---

## Processing Report

Every pipeline run produces:
- `processing_report.json` — machine-readable, full detail
- `processing_report.md` — human-readable summary

Report covers per-file: file name, detected type, extraction method, confidence score, status, issues list, output path or rejection reason.

---

## Document Mode Output Format

Every canonical markdown output file must have YAML frontmatter:

```yaml
---
doc_id: auto-generated or inferred from filename/title
title: inferred or extracted from document
doc_type: inferred (policy, faq, guide, report, transcript, etc.)
source_format: pdf | docx | md
source_file: original filename
extraction_method: pymupdf | ocr | python-docx | passthrough
extraction_confidence: 0.00–1.00
processing_status: approved | review_recommended | review_required
flags: []
processed_date: ISO 8601
---
```

---

## Tabular Mode Output Format

Output CSV columns:
- All original columns (unchanged)
- `[col]_cleaned` — cleaned version for each selected column
- `flags` — pipe-separated list of issues for that row (empty if clean)
- `confidence_score` — float 0.0–1.0 per row

---

## Scalability Story (for interviews and README)

DocBridgeAI v1 is intentionally scoped to 5 files for demo purposes. The architecture is designed to scale:

- Extraction and cleaning are stateless per document — trivially parallelizable
- Replace single-process pipeline with a task queue (Celery + Redis) for batch processing
- Replace local output folder with cloud object storage (S3, GCS, Azure Blob)
- Add connector adapters for SharePoint, Google Drive, Confluence, and Notion without touching the core pipeline
- Replace Tesseract OCR with cloud OCR (AWS Textract, Google Document AI) for higher accuracy at scale
- Add a human review queue (simple database table + review UI) for flagged documents
- For billion-row tabular data: swap pandas for streaming processors (Polars, DuckDB, Spark)

The core pipeline contracts (ExtractedContent → CleanedContent → ValidatedContent → Output) remain unchanged at any scale.

---

## Guardrails

Do:
- Preserve original data. Never overwrite source files.
- Always produce a processing report, even for failures.
- Flag uncertainty explicitly — do not silently pass low-confidence output.
- Keep extraction, cleaning, and validation as separate, testable steps.

Do not:
- Invent content that was not in the source file.
- Use the LLM to paraphrase or summarize — only to expand and normalize.
- Merge multiple source documents into one output without explicit user instruction.
- Expose raw LLM prompts in the UI.

---

## Interview Story

> "DocBridgeAI is a standalone pre-RAG document normalization layer I built to solve the problem that most RAG demos skip: enterprise knowledge never starts clean. It handles PDFs, scanned documents, Word files, Markdown, and structured tabular data. Every file goes through type detection, extraction, cleaning, quality validation, and export. The output is canonical markdown or cleaned CSV that any downstream AI system can ingest directly. I designed it as a reusable layer — it feeds both my RAG assistant and my customer servicing intelligence project. The quality validation step was the most important design decision: instead of blindly ingesting everything, the system scores each document, routes low-confidence output to a human review queue, and rejects anything it cannot reliably process. The architecture is stateless per document, so scaling from 5 files to 50,000 is a matter of adding a task queue and object storage — the pipeline contracts don't change."

---

## Out of Scope for v1

- Audio transcription (MP3, WAV)
- SharePoint / Google Drive / Confluence connectors
- Human review workflow with approval queue
- Real-time streaming ingestion
- Authentication or multi-user sessions
- Cloud deployment
