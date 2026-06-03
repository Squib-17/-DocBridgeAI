"""
DocBridgeAI — File Type Detector

Determines file type and processing mode from a SourceFile.

Detection strategy:
  1. Check file extension
  2. For PDFs: attempt text extraction; if character yield < threshold, classify as scanned
  3. Return DetectedFile with mode = 'document' or 'tabular'

File types:
  pdf_readable  → document mode
  pdf_scanned   → document mode (OCR path)
  docx          → document mode
  markdown      → document mode
  csv           → tabular mode
  xlsx          → tabular mode
  unsupported   → rejected at pipeline entry
"""

from __future__ import annotations

import io

from .models import DetectedFile, SourceFile

# Minimum extractable characters to classify a PDF as text-readable.
# Below this threshold we assume it's scanned and needs OCR.
PDF_TEXT_THRESHOLD = 100

DOCUMENT_EXTENSIONS = {"pdf", "docx", "doc", "md", "markdown", "txt"}
TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}

SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS | TABULAR_EXTENSIONS


def detect(source: SourceFile) -> DetectedFile:
    """
    Detect file type and route to the appropriate processing mode.

    Returns a DetectedFile. If the file type is unsupported, returns a
    DetectedFile with file_type='unsupported' and mode='unsupported'.
    The pipeline will skip processing and record this as rejected.
    """
    ext = source.extension

    if ext not in SUPPORTED_EXTENSIONS:
        return DetectedFile(
            source=source,
            file_type="unsupported",
            mode="unsupported",
            detection_confidence=1.0,
            detection_notes=f"Unsupported file extension: .{ext}",
        )

    # Tabular mode
    if ext in TABULAR_EXTENSIONS:
        file_type = "xlsx" if ext in {"xlsx", "xls"} else "csv"
        return DetectedFile(
            source=source,
            file_type=file_type,
            mode="tabular",
            detection_confidence=1.0,
        )

    # Document mode — markdown / txt
    if ext in {"md", "markdown", "txt"}:
        return DetectedFile(
            source=source,
            file_type="markdown",
            mode="document",
            detection_confidence=1.0,
        )

    # Document mode — Word
    if ext in {"docx", "doc"}:
        return DetectedFile(
            source=source,
            file_type="docx",
            mode="document",
            detection_confidence=1.0,
        )

    # Document mode — PDF: distinguish readable vs scanned
    if ext == "pdf":
        return _detect_pdf(source)

    # Fallback (should not reach here given the extension check above)
    return DetectedFile(
        source=source,
        file_type="unsupported",
        mode="unsupported",
        detection_confidence=1.0,
        detection_notes="Could not determine file type.",
    )


def _detect_pdf(source: SourceFile) -> DetectedFile:
    """
    Attempt lightweight text extraction to determine if the PDF is
    text-readable or scanned (image-only).
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=io.BytesIO(source.raw_bytes), filetype="pdf")
        total_text = ""
        for page in doc:
            total_text += page.get_text()
            if len(total_text.strip()) >= PDF_TEXT_THRESHOLD:
                break
        doc.close()

        if len(total_text.strip()) >= PDF_TEXT_THRESHOLD:
            return DetectedFile(
                source=source,
                file_type="pdf_readable",
                mode="document",
                detection_confidence=1.0,
            )
        else:
            return DetectedFile(
                source=source,
                file_type="pdf_scanned",
                mode="document",
                detection_confidence=0.9,
                detection_notes=(
                    f"Extracted only {len(total_text.strip())} characters — "
                    "classified as scanned PDF, OCR will be applied."
                ),
            )

    except Exception as e:
        # If PyMuPDF fails to open at all, treat as scanned and let OCR try
        return DetectedFile(
            source=source,
            file_type="pdf_scanned",
            mode="document",
            detection_confidence=0.7,
            detection_notes=f"PDF open failed ({e}), attempting OCR path.",
        )
