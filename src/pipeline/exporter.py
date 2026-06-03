"""
DocBridgeAI — Exporter

Writes validated output files to the output directory.

DocumentExporter  → canonical .md with YAML frontmatter
TabularExporter   → cleaned .csv with added columns

Neither exporter makes routing decisions — those come from ValidatedContent.
The status and confidence are written into the output as metadata so the
consumer knows what quality they're working with.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import ValidatedContent


class DocumentExporter:
    """
    Writes a canonical markdown file with YAML frontmatter.

    Output filename: <stem>_normalized.md
    """

    def export(self, validated: ValidatedContent, output_dir: str) -> str:
        """
        Write the canonical markdown file and return the output path.
        Raises OSError if the output directory cannot be created or written to.
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = validated.source.stem
        src_ext = Path(validated.source.filename).suffix.lstrip(".").lower()
        out_path = out_dir / f"{stem}_{src_ext}_normalized.md"

        frontmatter = self._build_frontmatter(validated)
        body = validated.cleaned.cleaned_text

        content = f"{frontmatter}\n{body}\n"

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

        return str(out_path)

    def _build_frontmatter(self, validated: ValidatedContent) -> str:
        """Build YAML frontmatter block from validated metadata."""
        meta = validated.metadata
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Collect issue flags as a list of short strings
        flags = [issue.field for issue in validated.issues]

        heading_structure = meta.get("heading_structure", "unknown")
        llm_structure = meta.get("llm_generated_structure", False)

        lines = [
            "---",
            f"doc_id: {meta.get('doc_id', 'UNKNOWN')}",
            f"title: {_yaml_str(meta.get('title', validated.source.stem))}",
            f"doc_type: {meta.get('doc_type', 'document')}",
            f"source_format: {meta.get('source_format', '')}",
            f"source_file: {meta.get('source_file', validated.source.filename)}",
            f"extraction_method: {meta.get('extraction_method', '')}",
            f"extraction_confidence: {validated.extracted.confidence_hint}",
            f"heading_structure: {heading_structure}",
            f"llm_generated_structure: {str(llm_structure).lower()}",
            f"processing_status: {validated.status}",
            f"confidence_score: {validated.confidence_score}",
            f"flags: {_yaml_list(flags)}",
            f"processed_date: {now}",
            "---",
        ]
        return "\n".join(lines)


class TabularExporter:
    """
    Writes a cleaned CSV with per-row flags and confidence scores.

    Output filename: <stem>_cleaned.csv
    """

    def export(self, validated: ValidatedContent, output_dir: str) -> str:
        """
        Write the cleaned CSV and return the output path.
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = validated.source.stem
        src_ext = Path(validated.source.filename).suffix.lstrip(".").lower()
        out_path = out_dir / f"{stem}_{src_ext}_cleaned.csv"

        rows = validated.cleaned.cleaned_rows
        if not rows:
            # Write an empty file with a note
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("# No rows to export\n")
            return str(out_path)

        # Determine column order: originals first, then cleaned variants, then flags/score
        original_cols = validated.extracted.column_names
        cleaned_cols = [f"{c}_cleaned" for c in validated.cleaned.cleaned_columns]
        meta_cols = ["flags", "confidence_score"]

        # Build final column list — include only columns that actually exist in rows
        sample = rows[0]
        all_output_cols = []
        for col in original_cols + cleaned_cols + meta_cols:
            if col in sample and col not in all_output_cols:
                all_output_cols.append(col)

        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_output_cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        return str(out_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yaml_str(value: str) -> str:
    """Wrap a string in quotes if it contains special YAML characters."""
    if any(c in value for c in (':', '#', '[', ']', '{', '}', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '`', '"', "'")):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(items: list[str]) -> str:
    """Render a Python list as a YAML inline list."""
    if not items:
        return "[]"
    inner = ", ".join(items)
    return f"[{inner}]"
