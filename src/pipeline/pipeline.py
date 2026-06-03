"""
DocBridgeAI — Pipeline Orchestrator

Wires all pipeline stages together for a batch of 1–5 uploaded files.

Flow per file:
  SourceFile
    → detect()            [detector.py]
    → get_extractor()     [extractors.py]
    → extractor.extract() [extractors.py]
    → cleaner.clean_*()   [cleaner.py]
    → validate_*()        [validator.py]
    → exporter.export()   [exporter.py]

Per-file exceptions are caught and logged as rejected — the pipeline
never raises and aborts remaining files.

After all files:
  → build_report()         [report.py]
  → write_report_json()    [report.py]
  → write_report_markdown() [report.py]
"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cleaner import Cleaner
from .detector import detect
from .exporter import DocumentExporter, TabularExporter
from .extractors import get_extractor
from .models import ProcessingReport, SourceFile, ValidatedContent
from .report import build_report, write_report_json, write_report_markdown
from .validator import validate_document, validate_tabular


@dataclass
class PipelineConfig:
    """Runtime configuration for a pipeline run."""
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    output_dir: str = "output"
    max_files: int = 5
    max_file_size_mb: int = 20


def run(
    files: list[SourceFile],
    tabular_columns: dict[str, list[str]],
    config: PipelineConfig,
    file_options: dict[str, dict] | None = None,
) -> ProcessingReport:
    """
    Process a list of SourceFiles and return a ProcessingReport.

    Parameters
    ----------
    files : list[SourceFile]
        Raw uploaded files. Validated for count and size limits here.
    tabular_columns : dict[str, list[str]]
        Maps filename → list of column names to clean.
        Required for tabular-mode files; ignored for document-mode files.
    config : PipelineConfig
        Runtime settings (API key, output dir, limits).

    Returns
    -------
    ProcessingReport
        Full session report including per-file results and summary stats.
        Report files are written to config.output_dir.
    """
    # Validate limits
    if len(files) > config.max_files:
        raise ValueError(
            f"Too many files. Maximum allowed is {config.max_files}, "
            f"but {len(files)} were provided."
        )

    max_bytes = config.max_file_size_mb * 1024 * 1024
    for f in files:
        if f.size_bytes > max_bytes:
            raise ValueError(
                f"File '{f.filename}' exceeds the {config.max_file_size_mb} MB size limit "
                f"({f.size_bytes / 1024 / 1024:.1f} MB)."
            )

    # Build cleaner (shared across all files in the session)
    openai_client = _build_openai_client(config.openai_api_key)
    cleaner = Cleaner(openai_client=openai_client, model=config.openai_model)

    doc_exporter = DocumentExporter()
    tab_exporter = TabularExporter()

    # results: list of (ValidatedContent | None, output_path | None, rejection_reason | None)
    results: list[tuple[ValidatedContent | None, str | None, str | None]] = []

    for source in files:
        opts = (file_options or {}).get(source.filename, {})
        try:
            result = _process_file(
                source=source,
                tabular_columns=tabular_columns.get(source.filename, []),
                cleaner=cleaner,
                doc_exporter=doc_exporter,
                tab_exporter=tab_exporter,
                output_dir=config.output_dir,
                options=opts,
            )
            results.append(result)
        except Exception as e:
            reason = f"Unhandled error processing '{source.filename}': {e}"
            results.append((None, None, reason))

    # Build and write report
    report = build_report(results)
    write_report_json(report, config.output_dir)
    write_report_markdown(report, config.output_dir)

    return report


def _process_file(
    source: SourceFile,
    tabular_columns: list[str],
    cleaner: Cleaner,
    doc_exporter: DocumentExporter,
    tab_exporter: TabularExporter,
    output_dir: str,
    options: dict | None = None,
) -> tuple[ValidatedContent | None, str | None, str | None]:
    """
    Process a single file through the full pipeline.
    Returns (ValidatedContent, output_path, rejection_reason).
    """
    options = options or {}
    expand_shorthand = options.get("expand_shorthand", True)
    generate_headings = options.get("generate_headings", False)

    # Stage 1: Detect
    detected = detect(source)

    if detected.mode == "unsupported":
        from .models import CleanedContent, ExtractedContent, ValidatedContent
        # Build a minimal ValidatedContent so the report has the correct filename
        _ext = ExtractedContent(source=source, file_type="unsupported", raw_text="",
                                extraction_method="none", confidence_hint=0.0)
        _cln = CleanedContent(source=source, cleaned_text="")
        _val = ValidatedContent(source=source, extracted=_ext, cleaned=_cln,
                                confidence_score=0.0, status="rejected", issues=[],
                                metadata={})
        return (_val, None, f"Unsupported file type: {detected.detection_notes}")

    # Stage 2: Extract
    extractor = get_extractor(detected)
    extracted = extractor.extract(source)

    # Stage 3: Clean + Stage 4: Validate + Stage 5: Export
    if detected.mode == "document":
        cleaned = cleaner.clean_document(
            extracted,
            expand_shorthand=expand_shorthand,
            generate_headings=generate_headings,
        )
        validated = validate_document(extracted, cleaned)

        if validated.status == "rejected":
            return (validated, None, "Rejected due to low confidence score.")

        output_path = doc_exporter.export(validated, output_dir)
        return (validated, output_path, None)

    else:  # tabular
        if not tabular_columns:
            # No columns selected — still process but flag it
            tabular_columns = extracted.column_names[:3] if extracted.column_names else []

        cleaned = cleaner.clean_tabular(extracted, tabular_columns, expand_shorthand=expand_shorthand)
        validated = validate_tabular(extracted, cleaned)

        if validated.status == "rejected":
            return (validated, None, "Rejected: no usable rows after tabular processing.")

        output_path = tab_exporter.export(validated, output_dir)
        return (validated, output_path, None)


def build_cleaner(config: PipelineConfig) -> Cleaner:
    """Build a shared Cleaner instance from config. Reuse across files in the same session."""
    client = _build_openai_client(config.openai_api_key)
    return Cleaner(openai_client=client, model=config.openai_model)


def process_one(
    source: SourceFile,
    tabular_columns: list[str],
    config: PipelineConfig,
    options: dict | None = None,
    cleaner: Cleaner | None = None,
) -> tuple[ValidatedContent | None, str | None, str | None]:
    """
    Process a single SourceFile. Returns (ValidatedContent, output_path, rejection_reason).

    Pass a shared Cleaner to avoid rebuilding the OpenAI client for each file.
    If cleaner is None, one is built from config.
    """
    _cleaner = cleaner or build_cleaner(config)
    doc_exporter = DocumentExporter()
    tab_exporter = TabularExporter()
    try:
        return _process_file(
            source=source,
            tabular_columns=tabular_columns,
            cleaner=_cleaner,
            doc_exporter=doc_exporter,
            tab_exporter=tab_exporter,
            output_dir=config.output_dir,
            options=options or {},
        )
    except Exception as e:
        reason = f"Unhandled error processing '{source.filename}': {e}"
        return (None, None, reason)


def _build_openai_client(api_key: str | None):
    """
    Build an OpenAI client if an API key is available.
    Returns None if the key is missing — cleaning falls back to glossary-only.
    """
    if not api_key or api_key.startswith("your-"):
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except ImportError:
        return None
