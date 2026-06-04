"""
DocBridgeAI — Validator

Scores each processed document or tabular file for quality and routes
it to an appropriate status.

Routing thresholds (from PRD):
  >= 0.85  → approved
  0.60–0.84 → review_recommended
  < 0.60   → review_required
  extraction failed → rejected

Document mode scoring (weighted composite):
  - Extraction confidence hint    35%
  - Garbled character ratio       25%
  - Minimum content length        15%
  - Metadata completeness         15%
  - Heading structure presence    10%

Tabular mode scoring:
  Per-row score based on shorthand density and null ratio.
  Row flags added to cleaned_rows as 'flags' and 'confidence_score' keys.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import (
    CleanedContent,
    ExtractedContent,
    ProcessingIssue,
    SourceFile,
    ValidatedContent,
)

# Routing thresholds
APPROVAL_THRESHOLD = 0.85
REVIEW_RECOMMENDED_THRESHOLD = 0.60

# Document scoring weights (must sum to 1.0)
W_EXTRACTION = 0.35
W_GARBLED    = 0.25
W_LENGTH     = 0.15
W_METADATA   = 0.15
W_STRUCTURE  = 0.10   # heading structure — affects RAG chunking quality

# Min content length to not penalise
MIN_CONTENT_CHARS = 200

# Min chars before heading-structure check matters (short docs don't need headings)
MIN_CHARS_FOR_HEADING_CHECK = 400

# Max acceptable garbled char ratio
MAX_GARBLED_RATIO = 0.10

# High shorthand density flag threshold for tabular rows
SHORTHAND_DENSITY_HIGH = 0.30


def validate_document(
    extracted: ExtractedContent,
    cleaned: CleanedContent,
) -> ValidatedContent:
    """
    Score and route a document-mode file.
    Also infers document metadata (doc_id, title, doc_type).
    """
    issues: list[ProcessingIssue] = []
    source = extracted.source

    # --- Signal 1: Extraction confidence from extractor ---
    extraction_score = extracted.confidence_hint
    if extraction_score < 0.50:
        issues.append(ProcessingIssue(
            severity="error",
            field="extraction_confidence",
            message=f"Low extraction confidence ({extraction_score:.2f}). "
                    "Content may be incomplete or unreliable.",
        ))
    elif extraction_score < 0.75:
        issues.append(ProcessingIssue(
            severity="warning",
            field="extraction_confidence",
            message=f"Moderate extraction confidence ({extraction_score:.2f}). "
                    "Human review recommended.",
        ))

    # --- Signal 2: Garbled character ratio ---
    garbled_score = _garbled_score(cleaned.cleaned_text)
    if garbled_score < 0.70:
        ratio = 1.0 - garbled_score
        issues.append(ProcessingIssue(
            severity="warning" if garbled_score >= 0.50 else "error",
            field="garbled_character_ratio",
            message=f"{ratio:.0%} of characters appear garbled or non-standard. "
                    "OCR quality may be poor.",
        ))

    # --- Signal 3: Content length ---
    char_count = len(cleaned.cleaned_text.strip())
    length_score = min(1.0, char_count / MIN_CONTENT_CHARS)
    if char_count < MIN_CONTENT_CHARS:
        issues.append(ProcessingIssue(
            severity="warning",
            field="content_length",
            message=f"Extracted text is very short ({char_count} characters). "
                    "Document may be mostly images or empty.",
        ))

    # --- Signal 4: Metadata completeness ---
    # Use raw_text for title extraction so the original heading is preserved
    # (cleaned text may have already expanded abbreviations in the heading)
    metadata = _infer_metadata(source, extracted.raw_text, extracted)
    metadata_score = _metadata_completeness_score(metadata)
    if metadata_score < 0.67:
        issues.append(ProcessingIssue(
            severity="warning",
            field="metadata_completeness",
            message="Could not infer all required metadata fields (title, doc_type). "
                    "Manual review of frontmatter recommended.",
        ))

    # --- Signal 5: Heading structure ---
    # Flat documents (no # headings) will produce poor results with section-based
    # RAG chunking. Flag them explicitly so consumers can choose an appropriate
    # chunking strategy or opt into LLM heading generation.
    structure_score, has_headings = _heading_structure_score(cleaned.cleaned_text)
    llm_generated_structure = any(
        "llm_heading_generation" in step for step in cleaned.cleaning_applied
    )
    if structure_score == 0.0:
        issues.append(ProcessingIssue(
            severity="warning",
            field="heading_structure",
            message=(
                "No heading structure detected in the output. "
                "Section-based RAG chunking will not work — use semantic or sentence-level "
                "chunking instead. Enable 'Generate headings' in processing options to have "
                "the LLM add structure automatically."
            ),
        ))
    elif llm_generated_structure:
        issues.append(ProcessingIssue(
            severity="warning",
            field="heading_structure",
            message=(
                "Heading structure was generated by LLM (not present in the source document). "
                "Review headings before ingesting into a section-based RAG pipeline."
            ),
        ))

    # LLM-generated structure is usable but not organic — cap at 0.5 so the
    # composite score reflects that the source document was unstructured.
    if llm_generated_structure:
        structure_score = 0.5

    metadata["heading_structure"] = (
        "generated" if llm_generated_structure else ("detected" if has_headings else "none")
    )
    metadata["llm_generated_structure"] = llm_generated_structure

    # --- Signal 6: DOCX table rendering ---
    # Warn if source had tables but none made it into cleaned output.
    if extracted.tables and not _has_markdown_table(cleaned.cleaned_text):
        issues.append(ProcessingIssue(
            severity="warning",
            field="table_rendering",
            message=(
                f"{len(extracted.tables)} table(s) detected in source document "
                "but not rendered into output. Review the source and add table "
                "content manually if needed."
            ),
        ))

    # --- Composite score ---
    composite = (
        W_EXTRACTION * extraction_score
        + W_GARBLED   * garbled_score
        + W_LENGTH    * length_score
        + W_METADATA  * metadata_score
        + W_STRUCTURE * structure_score
    )
    composite = round(min(1.0, max(0.0, composite)), 3)

    status = _route(composite)

    return ValidatedContent(
        source=source,
        extracted=extracted,
        cleaned=cleaned,
        confidence_score=composite,
        status=status,
        issues=issues,
        metadata=metadata,
    )


def validate_tabular(
    extracted: ExtractedContent,
    cleaned: CleanedContent,
) -> ValidatedContent:
    """
    Score and route a tabular-mode file.
    Adds per-row confidence_score and flags to cleaned_rows.
    """
    issues: list[ProcessingIssue] = []
    source = extracted.source

    if not cleaned.cleaned_rows:
        issues.append(ProcessingIssue(
            severity="error",
            field="rows",
            message="No rows were produced after cleaning.",
        ))
        return ValidatedContent(
            source=source,
            extracted=extracted,
            cleaned=cleaned,
            confidence_score=0.0,
            status="rejected",
            issues=issues,
            metadata={"columns": extracted.column_names, "row_count": 0},
        )

    total_glossary_hits = len(cleaned.glossary_hits)
    total_llm_hits = len([h for h in cleaned.llm_hits if h[0] != "__llm_error__"])
    row_count = len(cleaned.cleaned_rows)

    scored_rows: list[dict[str, Any]] = []
    row_scores: list[float] = []

    for row in cleaned.cleaned_rows:
        row_flags: list[str] = []
        cleaned_col_values: list[str] = []

        for col in cleaned.cleaned_columns:
            cleaned_key = f"{col}_cleaned"
            original_val = str(row.get(col, ""))
            cleaned_val = str(row.get(cleaned_key, ""))

            if not original_val.strip():
                row_flags.append(f"missing_data:{col}")
                continue

            cleaned_col_values.append(cleaned_val)

            # Estimate shorthand density for this cell
            orig_tokens = original_val.split()
            cleaned_tokens = cleaned_val.split()
            if orig_tokens:
                expansion_ratio = (len(cleaned_tokens) - len(orig_tokens)) / len(orig_tokens)
                if expansion_ratio > SHORTHAND_DENSITY_HIGH:
                    row_flags.append(f"shorthand_density_high:{col}")

        # Row confidence: penalise for flags
        flag_penalty = len(row_flags) * 0.08
        row_score = max(0.0, round(0.92 - flag_penalty, 3))
        row_scores.append(row_score)

        new_row = dict(row)
        new_row["flags"] = " | ".join(row_flags) if row_flags else ""
        new_row["confidence_score"] = row_score
        scored_rows.append(new_row)

    # Session-level checks
    if total_glossary_hits == 0 and total_llm_hits == 0:
        issues.append(ProcessingIssue(
            severity="warning",
            field="shorthand_expansion",
            message="No shorthand terms were expanded. "
                    "Verify that the correct columns were selected for cleaning.",
        ))

    low_conf_rows = sum(1 for s in row_scores if s < REVIEW_RECOMMENDED_THRESHOLD)
    if low_conf_rows > 0:
        pct = low_conf_rows / row_count
        issues.append(ProcessingIssue(
            severity="warning",
            field="low_confidence_rows",
            message=f"{low_conf_rows} of {row_count} rows ({pct:.0%}) have confidence below 0.60. "
                    "These rows are flagged in the output.",
        ))

    avg_score = round(sum(row_scores) / len(row_scores), 3) if row_scores else 0.0
    status = _route(avg_score)

    # Update cleaned_rows in-place on the CleanedContent object
    cleaned.cleaned_rows = scored_rows

    metadata = {
        "columns": extracted.column_names,
        "cleaned_columns": cleaned.cleaned_columns,
        "row_count": row_count,
        "low_confidence_rows": low_conf_rows,
        "total_glossary_hits": total_glossary_hits,
        "total_llm_hits": total_llm_hits,
    }

    return ValidatedContent(
        source=source,
        extracted=extracted,
        cleaned=cleaned,
        confidence_score=avg_score,
        status=status,
        issues=issues,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _route(score: float) -> str:
    if score >= APPROVAL_THRESHOLD:
        return "approved"
    elif score >= REVIEW_RECOMMENDED_THRESHOLD:
        return "review_recommended"
    else:
        return "review_required"


def _heading_structure_score(text: str) -> tuple[float, bool]:
    """
    Returns (score, has_headings).

    1.0 if the document has at least one markdown heading (#, ##, ###).
    0.0 if none found AND the document is long enough to warrant structure.
    Short documents (<MIN_CHARS_FOR_HEADING_CHECK chars) are not penalised.
    """
    if not text or len(text.strip()) < MIN_CHARS_FOR_HEADING_CHECK:
        return 1.0, True  # too short to need headings — don't penalise
    has_headings = any(
        line.strip().startswith("#") for line in text.splitlines()
    )
    return (1.0 if has_headings else 0.0), has_headings


def _garbled_score(text: str) -> float:
    """
    Returns a score 0.0–1.0 representing text cleanliness.
    1.0 = no garbled characters. 0.0 = entirely garbled.

    Garbled characters: non-printable, control characters, replacement
    character (U+FFFD), and excessive runs of non-ASCII symbols that
    aren't expected in banking text.
    """
    if not text:
        return 1.0

    # Whitespace that is normal in document text
    _SAFE_WHITESPACE = frozenset({"\n", "\r", "\t", "\r\n"})

    garbled = 0
    for ch in text:
        if ch in _SAFE_WHITESPACE:
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cs", "Co", "Cn"):  # control, surrogate, private, unassigned
            garbled += 1
        elif ch == "�":  # replacement character
            garbled += 1

    ratio = garbled / len(text)
    return round(1.0 - min(ratio / MAX_GARBLED_RATIO, 1.0), 3)


def _infer_metadata(
    source: SourceFile,
    text: str,
    extracted: ExtractedContent,
) -> dict[str, Any]:
    """
    Infer document metadata from filename, content, and extraction info.
    Heuristic-based; covers the common cases without an LLM call.
    """
    # doc_id: slugify filename stem
    stem = source.stem.upper().replace(" ", "-").replace("_", "-")
    doc_id = re.sub(r"[^A-Z0-9\-]", "", stem) or "UNKNOWN"

    # title: first H1 in markdown, or filename stem title-cased
    title = _extract_title(text) or source.stem.replace("_", " ").replace("-", " ").title()

    # doc_type: keyword heuristic
    doc_type = _classify_doc_type(source.filename, text)

    return {
        "doc_id": doc_id,
        "title": title,
        "doc_type": doc_type,
        "source_format": extracted.file_type,
        "source_file": source.filename,
        "extraction_method": extracted.extraction_method,
    }


def _extract_title(text: str) -> str | None:
    """Extract the first Markdown H1 heading from text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _classify_doc_type(filename: str, text: str) -> str:
    """Heuristic doc_type classification based on filename and content."""
    combined = (filename + " " + text[:500]).lower()

    if any(kw in combined for kw in ("policy", "policies", "terms", "conditions", "compliance")):
        return "policy"
    if any(kw in combined for kw in ("faq", "frequently asked", "q&a", "questions")):
        return "faq"
    if any(kw in combined for kw in ("guide", "how to", "how-to", "instructions", "tutorial", "steps")):
        return "guide"
    if any(kw in combined for kw in ("report", "analysis", "summary", "overview", "review")):
        return "report"
    if any(kw in combined for kw in ("transcript", "call", "conversation", "interaction", "chat")):
        return "transcript"
    if any(kw in combined for kw in ("glossary", "definition", "terminology")):
        return "glossary"
    if any(kw in combined for kw in ("notice", "notification", "alert", "noc")):
        return "notice"
    if any(kw in combined for kw in ("product", "loan", "card", "account", "agreement", "enrollment")):
        return "product"
    return "document"


def _has_markdown_table(text: str) -> bool:
    """Return True if text contains at least one rendered markdown table row."""
    return bool(re.search(r"^\|.+\|$", text, re.MULTILINE))


def _metadata_completeness_score(metadata: dict[str, Any]) -> float:
    """
    Score metadata completeness. Required fields: doc_id, title, doc_type.
    0.0 = all missing. 1.0 = all present with non-unknown values.
    """
    required = ["doc_id", "title", "doc_type"]
    present = sum(
        1 for k in required
        if metadata.get(k) and metadata[k] not in ("UNKNOWN", "document", "")
    )
    return round(present / len(required), 3)
