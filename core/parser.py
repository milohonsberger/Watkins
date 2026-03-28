"""
parser.py
─────────
PDF ingestion. Returns structured text and section metadata.
No UI dependencies — safe to call from any context.
"""

import logging
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# If average characters per page falls below this threshold, warn that OCR may be needed.
OCR_CHARS_PER_PAGE_THRESHOLD = 100


def parse_pdf(source) -> dict:
    """
    Extract text and structure from a PDF.

    Args:
        source: File path (str | Path) or a file-like object (e.g. Streamlit UploadedFile).

    Returns:
        {
            "full_text": str,         # complete extracted text
            "pages": list[str],       # text per page
            "num_pages": int,
            "metadata": dict,         # PDF document metadata if available
            "sections": list[dict],   # detected section headers: {page, header}
        }
    """
    reader = PdfReader(source)

    pages = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n\n".join(pages)

    _check_for_scanned_pdf(pages)

    return {
        "full_text": full_text,
        "pages": pages,
        "num_pages": len(pages),
        "metadata": _extract_pdf_metadata(reader),
        "sections": _detect_sections(pages),
    }


# ── Private helpers ────────────────────────────────────────────────────────────

def _check_for_scanned_pdf(pages: list[str]) -> None:
    total_chars = sum(len(p) for p in pages)
    avg_chars = total_chars / max(len(pages), 1)
    if avg_chars < OCR_CHARS_PER_PAGE_THRESHOLD:
        logger.warning(
            f"Low text content detected ({avg_chars:.0f} chars/page average). "
            "This PDF may be a scanned image. OCR processing may be required for "
            "accurate extraction — consider using a tool like Tesseract or Adobe Acrobat."
        )


def _extract_pdf_metadata(reader: PdfReader) -> dict:
    if not reader.metadata:
        return {}
    meta = reader.metadata
    return {
        "title":   meta.get("/Title", ""),
        "author":  meta.get("/Author", ""),
        "subject": meta.get("/Subject", ""),
        "creator": meta.get("/Creator", ""),
    }


def _detect_sections(pages: list[str]) -> list[dict]:
    """
    Heuristic section detection.
    Looks for short lines that resemble headers: numbered sections,
    all-caps lines, or title-case short phrases.
    """
    sections = []
    for page_num, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            stripped = line.strip()
            if _is_likely_header(stripped):
                sections.append({"page": page_num, "header": stripped})
    return sections


def _is_likely_header(line: str) -> bool:
    if not line or len(line) < 3 or len(line) > 120:
        return False
    if line.endswith(".") or line.endswith(",") or line.endswith(";"):
        return False
    words = line.split()
    if len(words) > 10:
        return False
    # Numbered section: "1.0 Introduction", "Section 3.2 Flora"
    if words and (words[0][0].isdigit() or words[0].lower() == "section"):
        return True
    # All-caps heading
    if line.isupper():
        return True
    # Title case: majority of words capitalised
    capitalised = sum(1 for w in words if w and w[0].isupper())
    if len(words) >= 2 and capitalised / len(words) >= 0.6:
        return True
    return False
