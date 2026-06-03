"""
DocBridgeAI — Data Models

All dataclasses passed between pipeline stages. No business logic here.
Each stage consumes one type and produces the next.

Pipeline contract:
  SourceFile → DetectedFile → ExtractedContent → CleanedContent
  → ValidatedContent → OutputFile + ProcessingReport
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Stage 0: Raw input
# ---------------------------------------------------------------------------

@dataclass
class SourceFile:
    """A raw file as uploaded by the user."""
    filename: str
    size_bytes: int
    raw_bytes: bytes

    @property
    def extension(self) -> str:
        return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""

    @property
    def stem(self) -> str:
        """Filename without extension."""
        return self.filename.rsplit(".", 1)[0] if "." in self.filename else self.filename


# ---------------------------------------------------------------------------
# Stage 1: Detection
# ---------------------------------------------------------------------------

@dataclass
class DetectedFile:
    """Output of detector.py — file type and processing mode resolved."""
    source: SourceFile
    file_type: str          # pdf_readable | pdf_scanned | docx | markdown | csv | xlsx | unsupported
    mode: str               # document | tabular | unsupported
    detection_confidence: float = 1.0
    detection_notes: str = ""


# ---------------------------------------------------------------------------
# Stage 2: Extraction
# ---------------------------------------------------------------------------

@dataclass
class ExtractedContent:
    """Raw content extracted from a source file. Unpacked but not yet cleaned."""
    source: SourceFile
    file_type: str
    raw_text: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    page_count: int | None = None
    extraction_method: str = ""
    # Extractor's internal quality hint: 0.0 (bad) – 1.0 (reliable)
    confidence_hint: float = 1.0
    # For tabular mode: column names detected
    column_names: list[str] = field(default_factory=list)
    # For tabular mode: raw DataFrame (stored as list of dicts for serializability)
    rows: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 3: Cleaning
# ---------------------------------------------------------------------------

@dataclass
class CleanedContent:
    """Text after structural cleaning and shorthand expansion."""
    source: SourceFile
    cleaned_text: str
    # Terms expanded by the domain glossary: [(original, replacement), ...]
    glossary_hits: list[tuple[str, str]] = field(default_factory=list)
    # Terms expanded by the LLM: [(original, replacement), ...]
    llm_hits: list[tuple[str, str]] = field(default_factory=list)
    # Human-readable list of cleaning steps applied
    cleaning_applied: list[str] = field(default_factory=list)
    # For tabular mode: cleaned rows as list of dicts
    cleaned_rows: list[dict[str, Any]] = field(default_factory=list)
    # For tabular mode: which columns were cleaned
    cleaned_columns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 4: Validation
# ---------------------------------------------------------------------------

@dataclass
class ProcessingIssue:
    """A single quality issue flagged during validation."""
    severity: str       # warning | error
    field: str          # which aspect triggered this (e.g. ocr_confidence, content_length)
    message: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "field": self.field, "message": self.message}


@dataclass
class ValidatedContent:
    """Output of validator.py — confidence score, routing status, and enriched metadata."""
    source: SourceFile
    extracted: ExtractedContent
    cleaned: CleanedContent
    confidence_score: float             # 0.0 – 1.0
    status: str                         # approved | review_recommended | review_required | rejected
    issues: list[ProcessingIssue] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stage 5: Report
# ---------------------------------------------------------------------------

@dataclass
class FileResult:
    """Per-file result entry in the processing report."""
    filename: str
    file_type: str
    mode: str
    extraction_method: str
    confidence_score: float
    status: str
    issues: list[dict]              # serialized ProcessingIssue dicts
    output_path: str | None         # None if rejected
    rejection_reason: str | None    # None if not rejected
    llm_used: bool = False          # True if LLM expansion actually ran (not just skipped)

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "file_type": self.file_type,
            "mode": self.mode,
            "extraction_method": self.extraction_method,
            "confidence_score": round(self.confidence_score, 3),
            "status": self.status,
            "issues": self.issues,
            "output_path": self.output_path,
            "rejection_reason": self.rejection_reason,
            "llm_used": self.llm_used,
        }


@dataclass
class ProcessingReport:
    """Full session-level processing report."""
    session_id: str
    timestamp: str
    total_files: int
    approved: int
    review_recommended: int
    review_required: int
    rejected: int
    files: list[FileResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "summary": {
                "total_files": self.total_files,
                "approved": self.approved,
                "review_recommended": self.review_recommended,
                "review_required": self.review_required,
                "rejected": self.rejected,
            },
            "files": [f.to_dict() for f in self.files],
        }
