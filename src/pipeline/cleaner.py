"""
DocBridgeAI — Text Cleaner

Two-layer text normalization:

  Layer 1 — Domain glossary (financial.json):
    Fast, deterministic, whole-word abbreviation substitution.
    Applied first to handle the most common cases without an LLM call.

  Layer 2 — LLM expansion (GPT-4o-mini):
    Applied after glossary to handle context-dependent shorthand,
    irregular abbreviations, and informal language the glossary misses.
    Prompt instructs the model to expand and normalize only — no paraphrasing,
    no meaning changes, no added facts.

For tabular mode, cleaning is applied row-by-row to selected columns.
The cleaner logs all glossary hits and LLM hits for the processing report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import CleanedContent, ExtractedContent

# Path to the glossary relative to this file
_GLOSSARY_PATH = Path(__file__).parent.parent / "glossary" / "financial.json"

# Structural cleaning patterns
_HYPHEN_LINEBREAK_RE = re.compile(r"(\w)-\n(\w)")
_MULTIPLE_SPACES_RE = re.compile(r"[ \t]+")
_MULTIPLE_NEWLINES_RE = re.compile(r"\n{3,}")
_BULLET_CHARS_RE = re.compile(r"^[•·▪▸➔➢►→]\s*", re.MULTILINE)

# Min chars before heading generation is attempted
_MIN_CHARS_FOR_HEADINGS = 400

# LLM expansion prompt
_LLM_SYSTEM_PROMPT = """You are a text normalization assistant for financial services.
Your task is to expand abbreviations, shorthand, and informal language into clear, readable English.

Rules:
- Expand abbreviations and shorthand to full words.
- Do NOT paraphrase or rewrite sentences.
- Do NOT change the meaning or add information not present in the original.
- Preserve all numbers, dates, names, account references, and proper nouns exactly.
- Preserve sentence structure and paragraph breaks.
- Return only the normalized text. No explanations, no commentary.

Example:
Input:  "cust acct was pd. auth decl on txn. escl to sup for chrgbck."
Output: "Customer account was paid. Authorization declined on transaction. Escalated to supervisor for chargeback."
"""

_LLM_HEADING_PROMPT = """You are a document structure analyst. The document below has no section headings.

Add markdown headings to organize the content into logical sections.

Rules:
- Use # for the document title (if identifiable), ## for main sections, ### for subsections.
- Identify section breaks from the content itself — do NOT invent sections that are not there.
- Do NOT change, add, remove, or reorder any of the original text.
- Do NOT paraphrase, summarize, or add commentary.
- Place headings immediately before the paragraph they introduce.
- If no clear sections exist, add only a single # title at the top.
- Return the complete document text with headings added.
"""


class Cleaner:
    """
    Orchestrates glossary substitution and optional LLM expansion.

    Parameters
    ----------
    openai_client : openai.OpenAI or None
        If None, LLM expansion is skipped and a warning is added to
        cleaning_applied. Glossary-only cleaning still runs.
    model : str
        OpenAI model name for LLM expansion. Defaults to gpt-4o-mini.
    glossary_path : Path or None
        Override the default glossary path (useful for testing).
    """

    def __init__(
        self,
        openai_client=None,
        model: str = "gpt-4o-mini",
        glossary_path: Path | None = None,
    ):
        self._client = openai_client
        self._model = model
        self._glossary = self._load_glossary(glossary_path or _GLOSSARY_PATH)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clean_document(
        self,
        content: ExtractedContent,
        expand_shorthand: bool = True,
        generate_headings: bool = False,
    ) -> CleanedContent:
        """Clean document-mode extracted text."""
        text = content.raw_text
        cleaning_applied: list[str] = []

        # Step 1: structural cleaning (always applied)
        text, struct_steps = self._structural_clean(text)
        cleaning_applied.extend(struct_steps)

        glossary_hits: list[tuple[str, str]] = []
        llm_hits: list[tuple[str, str]] = []

        if expand_shorthand:
            # Step 2: glossary substitution
            text, glossary_hits = self._apply_glossary(text)
            if glossary_hits:
                cleaning_applied.append(f"glossary_expansion ({len(glossary_hits)} terms)")

            # Step 3: LLM expansion
            if self._client is not None:
                text, llm_hits = self._llm_expand(text)
                if llm_hits:
                    cleaning_applied.append(f"llm_expansion ({len(llm_hits)} terms)")
            else:
                cleaning_applied.append("llm_expansion_skipped (no API client)")
        else:
            cleaning_applied.append("shorthand_expansion_skipped (disabled by user)")

        # Step 4: LLM heading generation (only when explicitly requested,
        # API client is available, document is long enough, and has no headings)
        if (
            generate_headings
            and self._client is not None
            and len(text.strip()) >= _MIN_CHARS_FOR_HEADINGS
            and not any(line.strip().startswith("#") for line in text.splitlines())
        ):
            text = self._llm_add_headings(text)
            cleaning_applied.append("llm_heading_generation")
        elif generate_headings and self._client is None:
            cleaning_applied.append("llm_heading_generation_skipped (no API client)")

        return CleanedContent(
            source=content.source,
            cleaned_text=text,
            glossary_hits=glossary_hits,
            llm_hits=llm_hits,
            cleaning_applied=cleaning_applied,
        )

    def clean_tabular(
        self,
        content: ExtractedContent,
        selected_columns: list[str],
        expand_shorthand: bool = True,
    ) -> CleanedContent:
        """
        Clean selected columns in tabular-mode data.
        Each selected column is processed row-by-row.
        """
        if not content.rows:
            return CleanedContent(
                source=content.source,
                cleaned_text="",
                cleaning_applied=["no_rows_to_process"],
                cleaned_columns=selected_columns,
            )

        all_glossary_hits: list[tuple[str, str]] = []
        all_llm_hits: list[tuple[str, str]] = []
        cleaning_applied: list[str] = []

        cleaned_rows: list[dict[str, Any]] = []
        for row in content.rows:
            cleaned_row = dict(row)  # preserve originals
            for col in selected_columns:
                if col not in row:
                    continue
                cell_text = str(row[col])
                if not cell_text.strip():
                    cleaned_row[f"{col}_cleaned"] = ""
                    continue

                if expand_shorthand:
                    # Glossary
                    cell_text, g_hits = self._apply_glossary(cell_text)
                    all_glossary_hits.extend(g_hits)

                    # LLM
                    if self._client is not None:
                        cell_text, l_hits = self._llm_expand(cell_text)
                        all_llm_hits.extend(l_hits)

                cleaned_row[f"{col}_cleaned"] = cell_text

            cleaned_rows.append(cleaned_row)

        if expand_shorthand:
            if all_glossary_hits:
                cleaning_applied.append(f"glossary_expansion ({len(all_glossary_hits)} total hits)")
            if all_llm_hits:
                cleaning_applied.append(f"llm_expansion ({len(all_llm_hits)} total hits)")
            elif self._client is None:
                cleaning_applied.append("llm_expansion_skipped (no API client)")
        else:
            cleaning_applied.append("shorthand_expansion_skipped (disabled by user)")

        return CleanedContent(
            source=content.source,
            cleaned_text="",  # not meaningful for tabular mode
            glossary_hits=all_glossary_hits,
            llm_hits=all_llm_hits,
            cleaning_applied=cleaning_applied,
            cleaned_rows=cleaned_rows,
            cleaned_columns=selected_columns,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_glossary(self, path: Path) -> dict[str, str]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Remove the comment key if present
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except FileNotFoundError:
            return {}

    def _structural_clean(self, text: str) -> tuple[str, list[str]]:
        """Apply structural fixes to raw extracted text."""
        steps: list[str] = []

        # Merge hyphenated line breaks (e.g., "auto-\npay" → "autopay")
        new_text = _HYPHEN_LINEBREAK_RE.sub(r"\1\2", text)
        if new_text != text:
            steps.append("merged_hyphenated_linebreaks")
        text = new_text

        # Normalise bullet characters to "-"
        new_text = _BULLET_CHARS_RE.sub("- ", text)
        if new_text != text:
            steps.append("normalised_bullets")
        text = new_text

        # Collapse multiple spaces/tabs to single space (preserve newlines)
        new_text = _MULTIPLE_SPACES_RE.sub(" ", text)
        if new_text != text:
            steps.append("normalised_whitespace")
        text = new_text

        # Collapse 3+ newlines to 2
        new_text = _MULTIPLE_NEWLINES_RE.sub("\n\n", text)
        if new_text != text:
            steps.append("collapsed_excess_newlines")
        text = new_text

        return text.strip(), steps

    def _apply_glossary(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """
        Apply whole-word glossary substitutions (case-insensitive).
        Returns the updated text and a list of (original, replacement) pairs.
        """
        hits: list[tuple[str, str]] = []
        for abbrev, expansion in self._glossary.items():
            # Escape special regex chars in the abbreviation
            pattern = re.compile(
                r"(?<!\w)" + re.escape(abbrev) + r"(?!\w)",
                re.IGNORECASE,
            )
            new_text, count = pattern.subn(expansion, text)
            if count > 0:
                hits.append((abbrev, expansion))
                text = new_text
        return text, hits

    def _llm_add_headings(self, text: str) -> str:
        """
        Ask the LLM to add markdown headings to a flat document.
        Returns the structured text. Falls back to original on failure.
        """
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _LLM_HEADING_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0,
                max_tokens=8192,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return text  # non-fatal — return original text unchanged

    def _llm_expand(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """
        Send text to GPT-4o-mini for shorthand expansion.
        Returns the expanded text. Hit tracking is best-effort (we note
        that LLM expansion occurred but don't diff word-by-word).
        """
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0,
                max_tokens=4096,
            )
            expanded = response.choices[0].message.content.strip()
            # Record that LLM expansion ran (we can't easily enumerate
            # individual word-level hits without diffing)
            hits: list[tuple[str, str]] = [("__llm__", "LLM expansion applied")]
            return expanded, hits
        except Exception as e:
            # LLM failure is non-fatal: return original text with a note
            return text, [("__llm_error__", str(e))]
