"""
DocBridgeAI — Pipeline Tests

Tests the full pipeline for file types that don't require external OS-level
dependencies (markdown and CSV/XLSX are pure Python + pandas).

PDF and DOCX tests mock the extraction libraries so the pipeline logic
can be verified without installing PyMuPDF, pytesseract, or python-docx.

Run with:  pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.models import (
    CleanedContent,
    ExtractedContent,
    ProcessingIssue,
    ProcessingReport,
    SourceFile,
    ValidatedContent,
)
from pipeline.detector import detect
from pipeline.cleaner import Cleaner
from pipeline.validator import validate_document, validate_tabular
from pipeline.exporter import DocumentExporter, TabularExporter
from pipeline.report import build_report, write_report_json, write_report_markdown
from pipeline.pipeline import PipelineConfig, run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_source(filename: str, content: str | bytes) -> SourceFile:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return SourceFile(filename=filename, size_bytes=len(content), raw_bytes=content)


# ---------------------------------------------------------------------------
# models.py
# ---------------------------------------------------------------------------

class TestModels:
    def test_source_file_extension(self):
        s = make_source("policy.pdf", b"data")
        assert s.extension == "pdf"

    def test_source_file_stem(self):
        s = make_source("autopay_policy.pdf", b"data")
        assert s.stem == "autopay_policy"

    def test_processing_issue_to_dict(self):
        issue = ProcessingIssue(severity="warning", field="ocr_confidence", message="Low OCR")
        d = issue.to_dict()
        assert d["severity"] == "warning"
        assert d["field"] == "ocr_confidence"
        assert d["message"] == "Low OCR"

    def test_processing_report_to_dict(self):
        report = ProcessingReport(
            session_id="abc123",
            timestamp="2026-06-01T00:00:00+00:00",
            total_files=2,
            approved=1,
            review_recommended=0,
            review_required=0,
            rejected=1,
        )
        d = report.to_dict()
        assert d["summary"]["total_files"] == 2
        assert d["summary"]["approved"] == 1


# ---------------------------------------------------------------------------
# detector.py
# ---------------------------------------------------------------------------

class TestDetector:
    def test_detect_markdown(self):
        source = make_source("guide.md", "# Title\nSome content")
        result = detect(source)
        assert result.file_type == "markdown"
        assert result.mode == "document"

    def test_detect_csv(self):
        source = make_source("data.csv", "name,value\nfoo,1")
        result = detect(source)
        assert result.file_type == "csv"
        assert result.mode == "tabular"

    def test_detect_xlsx(self):
        source = make_source("data.xlsx", b"\x50\x4b")  # fake bytes
        result = detect(source)
        assert result.file_type == "xlsx"
        assert result.mode == "tabular"

    def test_detect_docx(self):
        source = make_source("report.docx", b"fake")
        result = detect(source)
        assert result.file_type == "docx"
        assert result.mode == "document"

    def test_detect_unsupported(self):
        source = make_source("audio.mp3", b"fake")
        result = detect(source)
        assert result.file_type == "unsupported"
        assert result.mode == "unsupported"

    def test_detect_pdf_readable(self):
        """PDF that PyMuPDF can extract enough text from → pdf_readable."""
        long_text = "This is a readable policy document. " * 20
        with patch("fitz.open") as mock_open:
            mock_doc = MagicMock()
            mock_page = MagicMock()
            mock_page.get_text.return_value = long_text
            mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
            mock_doc.close = MagicMock()
            mock_open.return_value = mock_doc
            source = make_source("policy.pdf", b"%PDF-fake")
            result = detect(source)
        assert result.file_type == "pdf_readable"

    def test_detect_pdf_scanned(self):
        """PDF that yields very little text → pdf_scanned."""
        with patch("fitz.open") as mock_open:
            mock_doc = MagicMock()
            mock_page = MagicMock()
            mock_page.get_text.return_value = "ab"  # too short
            mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
            mock_doc.close = MagicMock()
            mock_open.return_value = mock_doc
            source = make_source("scanned.pdf", b"%PDF-fake")
            result = detect(source)
        assert result.file_type == "pdf_scanned"


# ---------------------------------------------------------------------------
# cleaner.py — glossary only (no OpenAI)
# ---------------------------------------------------------------------------

class TestCleaner:
    @pytest.fixture
    def cleaner(self):
        """Cleaner with no OpenAI client — glossary only."""
        return Cleaner(openai_client=None)

    def _make_extracted(self, filename: str, text: str) -> ExtractedContent:
        source = make_source(filename, text)
        return ExtractedContent(
            source=source,
            file_type="markdown",
            raw_text=text,
            extraction_method="passthrough",
            confidence_hint=1.0,
        )

    def test_glossary_expands_acct(self, cleaner):
        extracted = self._make_extracted("note.md", "The cust acct is overdue.")
        result = cleaner.clean_document(extracted)
        assert "customer" in result.cleaned_text
        assert "account" in result.cleaned_text

    def test_glossary_expands_txn(self, cleaner):
        extracted = self._make_extracted("note.md", "The txn was decl.")
        result = cleaner.clean_document(extracted)
        assert "transaction" in result.cleaned_text
        assert "declined" in result.cleaned_text

    def test_glossary_whole_word_only(self, cleaner):
        """'account' should NOT be replaced again (avoid 'accountount')."""
        extracted = self._make_extracted("note.md", "The account balance is correct.")
        result = cleaner.clean_document(extracted)
        assert "accountount" not in result.cleaned_text

    def test_structural_clean_hyphen_linebreak(self, cleaner):
        text = "Auto-\npay enrollment is required."
        extracted = self._make_extracted("note.md", text)
        result = cleaner.clean_document(extracted)
        assert "Auto-\npay" not in result.cleaned_text
        assert "Autopay" in result.cleaned_text or "Autoenrollment" not in result.cleaned_text

    def test_llm_skipped_without_client(self, cleaner):
        extracted = self._make_extracted("note.md", "cust bal avail 0")
        result = cleaner.clean_document(extracted)
        assert any("llm_expansion_skipped" in step for step in result.cleaning_applied)

    def test_tabular_clean_selected_columns(self, cleaner):
        source = make_source("interactions.csv", b"")
        rows = [
            {"agent_notes": "cust acct decl", "other_col": "unchanged"},
            {"agent_notes": "txn pndng", "other_col": "also unchanged"},
        ]
        extracted = ExtractedContent(
            source=source,
            file_type="csv",
            raw_text="",
            extraction_method="pandas_csv",
            confidence_hint=1.0,
            column_names=["agent_notes", "other_col"],
            rows=rows,
        )
        result = cleaner.clean_tabular(extracted, selected_columns=["agent_notes"])
        assert len(result.cleaned_rows) == 2
        # Original preserved
        assert result.cleaned_rows[0]["agent_notes"] == "cust acct decl"
        assert result.cleaned_rows[0]["other_col"] == "unchanged"
        # Cleaned version added
        assert "agent_notes_cleaned" in result.cleaned_rows[0]
        cleaned_0 = result.cleaned_rows[0]["agent_notes_cleaned"]
        assert "customer" in cleaned_0 or "account" in cleaned_0

    def test_llm_expansion_called_when_client_present(self):
        """When an OpenAI client is present, it should be called for LLM expansion."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Customer account declined transaction."
        mock_client.chat.completions.create.return_value = mock_response

        cleaner = Cleaner(openai_client=mock_client, model="gpt-4o-mini")
        source = make_source("note.md", "cust acct decl txn")
        extracted = ExtractedContent(
            source=source,
            file_type="markdown",
            raw_text="cust acct decl txn",
            extraction_method="passthrough",
            confidence_hint=1.0,
        )
        result = cleaner.clean_document(extracted)
        assert mock_client.chat.completions.create.called
        assert "Customer account declined transaction." in result.cleaned_text


# ---------------------------------------------------------------------------
# validator.py
# ---------------------------------------------------------------------------

class TestValidator:
    def _make_validated_inputs(
        self,
        filename: str = "policy.md",
        raw_text: str = "# Policy\n\nThis is a policy document with sufficient content. " * 10,
        cleaned_text: str | None = None,
        confidence_hint: float = 0.95,
        file_type: str = "markdown",
    ):
        source = make_source(filename, raw_text)
        extracted = ExtractedContent(
            source=source,
            file_type=file_type,
            raw_text=raw_text,
            extraction_method="passthrough",
            confidence_hint=confidence_hint,
        )
        cleaned = CleanedContent(
            source=source,
            cleaned_text=cleaned_text or raw_text,
            cleaning_applied=["passthrough"],
        )
        return extracted, cleaned

    def test_high_quality_doc_approved(self):
        extracted, cleaned = self._make_validated_inputs()
        result = validate_document(extracted, cleaned)
        assert result.status == "approved"
        assert result.confidence_score >= 0.85

    def test_low_extraction_confidence_flagged(self):
        extracted, cleaned = self._make_validated_inputs(confidence_hint=0.40)
        result = validate_document(extracted, cleaned)
        # Score should be lower and have issues
        assert result.confidence_score < 0.85
        fields = [i.field for i in result.issues]
        assert "extraction_confidence" in fields

    def test_short_content_flagged(self):
        short_text = "Short."
        extracted, cleaned = self._make_validated_inputs(
            raw_text=short_text,
            cleaned_text=short_text,
        )
        result = validate_document(extracted, cleaned)
        fields = [i.field for i in result.issues]
        assert "content_length" in fields

    def test_garbled_text_flagged(self):
        garbled = "Valid text. " * 5 + "\x00\x01\x02\x00\x01\x02" * 50
        extracted, cleaned = self._make_validated_inputs(
            raw_text=garbled, cleaned_text=garbled
        )
        result = validate_document(extracted, cleaned)
        fields = [i.field for i in result.issues]
        assert "garbled_character_ratio" in fields

    def test_tabular_validation_adds_row_scores(self):
        source = make_source("interactions.csv", b"")
        rows = [
            {"agent_notes": "cust acct decl", "agent_notes_cleaned": "customer account declined"},
            {"agent_notes": "txn pndng", "agent_notes_cleaned": "transaction pending"},
        ]
        extracted = ExtractedContent(
            source=source,
            file_type="csv",
            raw_text="",
            extraction_method="pandas_csv",
            confidence_hint=1.0,
            column_names=["agent_notes"],
            rows=rows,
        )
        cleaned = CleanedContent(
            source=source,
            cleaned_text="",
            cleaning_applied=[],
            cleaned_rows=rows,
            cleaned_columns=["agent_notes"],
        )
        result = validate_tabular(extracted, cleaned)
        assert "confidence_score" in result.cleaned.cleaned_rows[0]
        assert "flags" in result.cleaned.cleaned_rows[0]

    def test_routing_thresholds(self):
        for score, expected_status in [
            (0.90, "approved"),
            (0.70, "review_recommended"),
            (0.50, "review_required"),
        ]:
            from pipeline.validator import _route
            assert _route(score) == expected_status


# ---------------------------------------------------------------------------
# exporter.py
# ---------------------------------------------------------------------------

class TestExporter:
    def _make_validated(self, filename: str = "policy.md", mode: str = "document") -> ValidatedContent:
        source = make_source(filename, "# Policy\n\nContent here.")
        extracted = ExtractedContent(
            source=source,
            file_type="markdown",
            raw_text="# Policy\n\nContent here.",
            extraction_method="passthrough",
            confidence_hint=0.95,
        )
        cleaned = CleanedContent(
            source=source,
            cleaned_text="# Policy\n\nContent here.",
            cleaning_applied=["passthrough"],
        )
        return ValidatedContent(
            source=source,
            extracted=extracted,
            cleaned=cleaned,
            confidence_score=0.92,
            status="approved",
            issues=[],
            metadata={
                "doc_id": "POLICY",
                "title": "Policy",
                "doc_type": "policy",
                "source_format": "markdown",
                "source_file": filename,
                "extraction_method": "passthrough",
            },
        )

    def test_document_export_creates_file(self, tmp_path):
        validated = self._make_validated()
        exporter = DocumentExporter()
        out_path = exporter.export(validated, str(tmp_path))
        assert Path(out_path).exists()

    def test_document_export_has_frontmatter(self, tmp_path):
        validated = self._make_validated()
        exporter = DocumentExporter()
        out_path = exporter.export(validated, str(tmp_path))
        content = Path(out_path).read_text()
        assert "---" in content
        assert "doc_id:" in content
        assert "processing_status:" in content
        assert "confidence_score:" in content

    def test_tabular_export_creates_file(self, tmp_path):
        source = make_source("interactions.csv", b"")
        extracted = ExtractedContent(
            source=source, file_type="csv", raw_text="",
            extraction_method="pandas_csv", confidence_hint=1.0,
            column_names=["notes"],
        )
        cleaned = CleanedContent(
            source=source, cleaned_text="",
            cleaning_applied=[],
            cleaned_rows=[
                {"notes": "cust acct", "notes_cleaned": "customer account", "flags": "", "confidence_score": 0.92}
            ],
            cleaned_columns=["notes"],
        )
        validated = ValidatedContent(
            source=source, extracted=extracted, cleaned=cleaned,
            confidence_score=0.92, status="approved", issues=[],
            metadata={"columns": ["notes"], "cleaned_columns": ["notes"], "row_count": 1},
        )
        exporter = TabularExporter()
        out_path = exporter.export(validated, str(tmp_path))
        assert Path(out_path).exists()

    def test_tabular_export_has_cleaned_columns(self, tmp_path):
        source = make_source("data.csv", b"")
        extracted = ExtractedContent(
            source=source, file_type="csv", raw_text="",
            extraction_method="pandas_csv", confidence_hint=1.0,
            column_names=["notes"],
        )
        cleaned = CleanedContent(
            source=source, cleaned_text="",
            cleaning_applied=[],
            cleaned_rows=[
                {"notes": "cust acct", "notes_cleaned": "customer account", "flags": "", "confidence_score": 0.90}
            ],
            cleaned_columns=["notes"],
        )
        validated = ValidatedContent(
            source=source, extracted=extracted, cleaned=cleaned,
            confidence_score=0.90, status="approved", issues=[],
            metadata={"columns": ["notes"], "cleaned_columns": ["notes"], "row_count": 1},
        )
        exporter = TabularExporter()
        out_path = exporter.export(validated, str(tmp_path))

        with open(out_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert "notes_cleaned" in rows[0]
        assert "confidence_score" in rows[0]
        assert rows[0]["notes"] == "cust acct"
        assert rows[0]["notes_cleaned"] == "customer account"


# ---------------------------------------------------------------------------
# report.py
# ---------------------------------------------------------------------------

class TestReport:
    def _make_results(self):
        source = make_source("policy.md", "# Policy\n\nContent.")
        extracted = ExtractedContent(
            source=source, file_type="markdown", raw_text="# Policy\n\nContent.",
            extraction_method="passthrough", confidence_hint=1.0,
        )
        cleaned = CleanedContent(source=source, cleaned_text="# Policy\n\nContent.")
        validated = ValidatedContent(
            source=source, extracted=extracted, cleaned=cleaned,
            confidence_score=0.92, status="approved", issues=[],
            metadata={"doc_id": "POLICY", "title": "Policy", "doc_type": "policy",
                      "source_format": "markdown", "source_file": "policy.md",
                      "extraction_method": "passthrough"},
        )
        return [(validated, "/output/policy_normalized.md", None)]

    def test_build_report_counts(self):
        results = self._make_results()
        report = build_report(results)
        assert report.total_files == 1
        assert report.approved == 1
        assert report.rejected == 0

    def test_write_report_json(self, tmp_path):
        results = self._make_results()
        report = build_report(results)
        path = write_report_json(report, str(tmp_path))
        assert Path(path).exists()
        data = json.loads(Path(path).read_text())
        assert "summary" in data
        assert data["summary"]["total_files"] == 1

    def test_write_report_markdown(self, tmp_path):
        results = self._make_results()
        report = build_report(results)
        path = write_report_markdown(report, str(tmp_path))
        assert Path(path).exists()
        content = Path(path).read_text()
        assert "Processing Report" in content
        assert "policy.md" in content

    def test_rejected_file_in_report(self):
        results = [(None, None, "Unsupported file type: .mp3")]
        report = build_report(results)
        assert report.rejected == 1
        assert report.files[0].status == "rejected"


# ---------------------------------------------------------------------------
# Full pipeline — markdown + CSV (no external lib deps)
# ---------------------------------------------------------------------------

class TestPipelineEndToEnd:
    def test_markdown_file_end_to_end(self, tmp_path):
        """Full pipeline run on a markdown file — no mocks needed."""
        md_content = "# Autopay Policy\n\nThe cust must enroll in autopay. The acct bal must be avail.\n\n" + "Content line. " * 30
        source = make_source("autopay_policy.md", md_content)
        config = PipelineConfig(
            openai_api_key=None,
            output_dir=str(tmp_path),
        )
        report = run(
            files=[source],
            tabular_columns={},
            config=config,
        )
        assert report.total_files == 1
        # Should be approved (clean markdown, good confidence)
        assert report.files[0].status in ("approved", "review_recommended")
        # Output file should exist
        assert report.files[0].output_path is not None
        assert Path(report.files[0].output_path).exists()
        # Frontmatter present
        content = Path(report.files[0].output_path).read_text()
        assert "doc_id:" in content
        assert "processing_status:" in content

    def test_glossary_applied_in_markdown_pipeline(self, tmp_path):
        """Glossary expansions should appear in the normalized output."""
        md_content = "# Notes\n\ncust called re: acct. txn was decl. escl to sup.\n" + "More content. " * 30
        source = make_source("notes.md", md_content)
        config = PipelineConfig(openai_api_key=None, output_dir=str(tmp_path))
        report = run(files=[source], tabular_columns={}, config=config)
        out_path = report.files[0].output_path
        assert out_path is not None
        content = Path(out_path).read_text()
        # Glossary expansions
        assert "customer" in content
        assert "account" in content
        assert "transaction" in content

    def test_csv_file_end_to_end(self, tmp_path):
        """Full pipeline run on a CSV file with tabular mode."""
        csv_content = "agent_notes,ticket_id\ncust acct decl txn,T001\npmt pndng review,T002\n"
        source = make_source("agent_notes.csv", csv_content)
        config = PipelineConfig(openai_api_key=None, output_dir=str(tmp_path))

        # We need pandas for this test — skip if not available
        pytest.importorskip("pandas")

        report = run(
            files=[source],
            tabular_columns={"agent_notes.csv": ["agent_notes"]},
            config=config,
        )
        assert report.total_files == 1
        assert report.files[0].mode == "tabular"
        out_path = report.files[0].output_path
        assert out_path is not None
        assert Path(out_path).exists()

        with open(out_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert "agent_notes_cleaned" in rows[0]
        assert "confidence_score" in rows[0]

    def test_unsupported_file_rejected(self, tmp_path):
        source = make_source("audio.mp3", b"fake audio data")
        config = PipelineConfig(openai_api_key=None, output_dir=str(tmp_path))
        report = run(files=[source], tabular_columns={}, config=config)
        assert report.rejected == 1
        assert report.files[0].output_path is None

    def test_max_files_limit_enforced(self, tmp_path):
        files = [make_source(f"file_{i}.md", b"content") for i in range(6)]
        config = PipelineConfig(openai_api_key=None, output_dir=str(tmp_path), max_files=5)
        with pytest.raises(ValueError, match="Too many files"):
            run(files=files, tabular_columns={}, config=config)

    def test_report_files_written(self, tmp_path):
        source = make_source("doc.md", "# Title\n\n" + "Content line. " * 30)
        config = PipelineConfig(openai_api_key=None, output_dir=str(tmp_path))
        run(files=[source], tabular_columns={}, config=config)
        assert (tmp_path / "processing_report.json").exists()
        assert (tmp_path / "processing_report.md").exists()
