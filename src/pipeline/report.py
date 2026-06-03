"""
DocBridgeAI — Processing Report Generator

Builds a ProcessingReport from per-file results and writes it as:
  - processing_report.json  (machine-readable, full detail)
  - processing_report.md    (human-readable summary)

The report is always generated, even when all files are rejected.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import FileResult, ProcessingReport, ValidatedContent

# Status display labels for the markdown report
_STATUS_LABELS = {
    "approved": "✅ Approved",
    "review_recommended": "⚠️  Review Recommended",
    "review_required": "🔴 Review Required",
    "rejected": "❌ Rejected",
}

_STATUS_EMOJI = {
    "approved": "✅",
    "review_recommended": "⚠️",
    "review_required": "🔴",
    "rejected": "❌",
}


def build_report(
    results: list[tuple[ValidatedContent | None, str | None, str | None]],
) -> ProcessingReport:
    """
    Build a ProcessingReport from a list of per-file results.

    Each result is a tuple of:
      (ValidatedContent or None, output_path or None, rejection_reason or None)

    ValidatedContent is None only if extraction failed entirely before
    a ValidatedContent object could be produced.
    """
    session_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now(timezone.utc).isoformat()

    file_results: list[FileResult] = []
    approved = review_recommended = review_required = rejected = 0

    for validated, output_path, rejection_reason in results:
        if validated is None:
            # Should not happen with current pipeline — kept as safety net
            rejected += 1
            file_results.append(FileResult(
                filename="unknown",
                file_type="unknown",
                mode="unknown",
                extraction_method="none",
                confidence_score=0.0,
                status="rejected",
                issues=[{"severity": "error", "field": "extraction", "message": rejection_reason or "Extraction failed"}],
                output_path=None,
                rejection_reason=rejection_reason,
            ))
            continue

        status = validated.status
        if status == "approved":
            approved += 1
        elif status == "review_recommended":
            review_recommended += 1
        elif status == "review_required":
            review_required += 1
        else:
            rejected += 1

        llm_used = any(
            s.startswith("llm_expansion (") or s.startswith("llm_heading_generation")
            for s in validated.cleaned.cleaning_applied
        )
        file_results.append(FileResult(
            filename=validated.source.filename,
            file_type=validated.extracted.file_type,
            mode="tabular" if validated.extracted.file_type in ("csv", "xlsx") else "document",
            extraction_method=validated.extracted.extraction_method,
            confidence_score=validated.confidence_score,
            status=status,
            issues=[issue.to_dict() for issue in validated.issues],
            output_path=output_path,
            rejection_reason=rejection_reason,
            llm_used=llm_used,
        ))

    return ProcessingReport(
        session_id=session_id,
        timestamp=timestamp,
        total_files=len(results),
        approved=approved,
        review_recommended=review_recommended,
        review_required=review_required,
        rejected=rejected,
        files=file_results,
    )


def write_report_json(report: ProcessingReport, output_dir: str) -> str:
    """Write the processing report as JSON. Returns the output path."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "processing_report.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)

    return str(out_path)


def write_report_markdown(report: ProcessingReport, output_dir: str) -> str:
    """Write the processing report as Markdown. Returns the output path."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "processing_report.md"

    lines: list[str] = []

    # Header
    lines += [
        "# DocBridgeAI — Processing Report",
        "",
        f"**Session ID:** `{report.session_id}`  ",
        f"**Timestamp:** {report.timestamp}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total files processed | {report.total_files} |",
        f"| ✅ Approved | {report.approved} |",
        f"| ⚠️  Review Recommended | {report.review_recommended} |",
        f"| 🔴 Review Required | {report.review_required} |",
        f"| ❌ Rejected | {report.rejected} |",
        "",
        "---",
        "",
        "## File Results",
        "",
    ]

    for i, file in enumerate(report.files, 1):
        status_label = _STATUS_LABELS.get(file.status, file.status)
        lines += [
            f"### {i}. {file.filename}",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Status | {status_label} |",
            f"| File Type | `{file.file_type}` |",
            f"| Mode | {file.mode} |",
            f"| Extraction Method | `{file.extraction_method}` |",
            f"| Confidence Score | `{file.confidence_score:.3f}` |",
        ]

        if file.output_path:
            lines.append(f"| Output File | `{file.output_path}` |")
        if file.rejection_reason:
            lines.append(f"| Rejection Reason | {file.rejection_reason} |")

        lines.append("")

        if file.issues:
            lines += ["**Issues:**", ""]
            for issue in file.issues:
                sev_emoji = "⚠️" if issue.get("severity") == "warning" else "🔴"
                lines.append(f"- {sev_emoji} **{issue.get('field', '')}**: {issue.get('message', '')}")
            lines.append("")
        else:
            lines += ["*No issues detected.*", ""]

        lines.append("---")
        lines.append("")

    content = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return str(out_path)
