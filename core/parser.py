"""
parser.py
─────────
PDF ingestion using PyMuPDF (fitz). Returns structured text and section metadata.

Strategy: mimic a GIS technician skimming a document — extract the TOC, score
sections for spatial relevance and table content, then build a focused
`relevant_text` from only those pages. The LLM receives a tight context instead
of the full document.

No UI dependencies — safe to call from any context.
"""

import logging
import re
from collections import Counter

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Pages with fewer characters than this (and at least one image) are flagged as scanned.
_SCANNED_CHARS_THRESHOLD = 100

# Maximum pages beyond a section's start to include (prevents running into unrelated content).
_MAX_SECTION_PAGE_SPREAD = 5

# Spatial keyword tiers for section scoring.
_HIGH_SPATIAL = {
    "location", "coordinates", "latitude", "longitude",
    "site description", "project site", "legal description",
    "plss", "apn", "parcel", "vicinity",
}
_MED_SPATIAL = {
    "study area", "project area", "geographic", "spatial", "map",
    "address", "boundary", "survey", "datum", "monitoring",
}

# Table indicator words for section scoring.
_TABLE_WORDS = {"table", "inventory", "appendix"}

# Titles that match these patterns are excluded from relevant sections (meta-pages, not content).
_EXCLUDE_TITLE_PATTERNS = re.compile(
    r"^(table of contents|contents|cover|title page|acronyms?|abbreviations?|references?)$",
    re.IGNORECASE,
)


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_pdf(source) -> dict:
    """
    Extract text and structure from a PDF.

    Args:
        source: File path (str | Path) or a file-like object (e.g. Streamlit UploadedFile).

    Returns:
        {
            "full_text": str,                    # complete extracted text (fallback)
            "relevant_text": str,               # spatially-focused pages — primary LLM input
            "pages": list[str],                 # text per page (0-indexed)
            "num_pages": int,
            "metadata": dict,                   # PDF document metadata
            "toc": list[dict],                  # [{title, page_number}]
            "relevant_sections": list[dict],    # TOC entries that scored ≥ 1
            "tables_index": list[dict],         # [{page, headers}] for pages with tables
            "coordinate_candidates": list[dict],# regex-found spatial refs
            "is_scanned": bool,
        }
    """
    # fitz accepts file paths (str/Path) and bytes-like / file-like objects.
    if hasattr(source, "read"):
        doc = fitz.open(stream=source.read(), filetype="pdf")
    else:
        doc = fitz.open(str(source))

    pages = [page.get_text("text") for page in doc]
    full_text = "\n\n".join(pages)

    metadata = _extract_pdf_metadata(doc)
    is_scanned = _check_for_scanned_pdf(doc)
    toc = _extract_toc(doc, pages)
    relevant_sections = _score_section_relevance(toc)
    tables_index = _detect_tables(doc)
    relevant_text = _extract_relevant_pages(pages, toc, relevant_sections, tables_index)
    coordinate_candidates = _extract_coordinate_candidates(pages)

    doc.close()

    return {
        "full_text": full_text,
        "relevant_text": relevant_text,
        "pages": pages,
        "num_pages": len(pages),
        "metadata": metadata,
        "toc": toc,
        "relevant_sections": relevant_sections,
        "tables_index": tables_index,
        "coordinate_candidates": coordinate_candidates,
        "is_scanned": is_scanned,
    }


# ── Private helpers ────────────────────────────────────────────────────────────

def _extract_pdf_metadata(doc: fitz.Document) -> dict:
    meta = doc.metadata or {}
    return {
        "title":   meta.get("title", ""),
        "author":  meta.get("author", ""),
        "subject": meta.get("subject", ""),
        "creator": meta.get("creator", ""),
    }


def _check_for_scanned_pdf(doc: fitz.Document) -> bool:
    """
    Returns True if the majority of pages appear to be scanned images
    (very little extractable text but embedded images present).
    """
    scanned_pages = 0
    for page in doc:
        text = page.get_text().strip()
        has_images = len(page.get_images()) > 0
        if len(text) < _SCANNED_CHARS_THRESHOLD and has_images:
            scanned_pages += 1

    is_scanned = scanned_pages > len(doc) / 2
    if is_scanned:
        logger.warning(
            f"{scanned_pages}/{len(doc)} pages appear to be scanned images. "
            "OCR processing may be required for accurate extraction."
        )
    return is_scanned


def _extract_toc(doc: fitz.Document, pages: list[str]) -> list[dict]:
    """
    Extract table of contents as [{title, page_number}].

    Method A: doc.get_toc() — uses PDF bookmarks/outline (preferred).
    Method B: Text-based dot-leader pattern scan of the first 5 pages (fallback).
    """
    raw_toc = doc.get_toc()  # [[level, title, page_num], ...]
    if raw_toc:
        return [{"title": title, "page_number": page_num}
                for _, title, page_num in raw_toc
                if title and title.strip()]

    # Fallback: scan for dot-leader TOC lines, e.g. "Project Location ........ 12"
    toc = []
    dot_leader = re.compile(r"^(.+?)\s*\.{2,}\s*(\d+)\s*$")
    for page_text in pages[:5]:
        for line in page_text.splitlines():
            m = dot_leader.match(line.strip())
            if m:
                title = m.group(1).strip()
                try:
                    page_num = int(m.group(2))
                    toc.append({"title": title, "page_number": page_num})
                except ValueError:
                    pass

    if toc:
        logger.info(f"TOC extracted via text fallback: {len(toc)} entries")
    else:
        logger.info("No TOC found — full document will be used as relevant_text")

    return toc


def _score_section_relevance(toc: list[dict]) -> list[dict]:
    """
    Score each TOC entry and return entries with score ≥ 1.

    Scoring:
      +2  title contains a high-value spatial keyword
      +1  title contains a medium-value spatial keyword
      +2  title contains a table indicator word or numbered-table pattern (e.g. "3-3")
    """
    relevant = []
    numbered_table = re.compile(r"\d+[-–]\d+")

    for entry in toc:
        title_lower = entry["title"].lower().strip()

        # Skip meta-pages that are never content (TOC page, cover, references, etc.)
        if _EXCLUDE_TITLE_PATTERNS.match(title_lower):
            continue

        score = 0

        for kw in _HIGH_SPATIAL:
            if kw in title_lower:
                score += 2
                break
        for kw in _MED_SPATIAL:
            if kw in title_lower:
                score += 1
                break

        for word in _TABLE_WORDS:
            if word in title_lower:
                score += 2
                break
        if numbered_table.search(entry["title"]):
            score += 2

        if score >= 1:
            relevant.append({**entry, "score": score})

    logger.info(f"Relevant sections scored: {len(relevant)}/{len(toc)}")
    return relevant


def _detect_tables(doc: fitz.Document) -> list[dict]:
    """
    Detect pages that contain tabular data using fitz's table finder.
    Returns [{page}] — just the page numbers (1-indexed).

    We deliberately don't store column headers here: complex multi-page tables
    (e.g. species inventories) often have merged category rows as their first
    detected row, making extracted headers misleading. Instead, the full page
    text for each detected page is included in relevant_text, and the LLM
    matches table content against the user-defined schema column descriptions.
    """
    seen_pages: set[int] = set()
    tables_index = []

    for page_num, page in enumerate(doc, start=1):
        try:
            finder = page.find_tables()
            if finder.tables and page_num not in seen_pages:
                seen_pages.add(page_num)
                tables_index.append({"page": page_num})
        except Exception:
            # find_tables() can raise on malformed pages — skip gracefully.
            pass

    logger.info(f"Table pages detected: {len(tables_index)}")
    return tables_index


def _extract_relevant_pages(
    pages: list[str],
    toc: list[dict],
    relevant_sections: list[dict],
    tables_index: list[dict],
) -> str:
    """
    Build relevant_text from:
      - Page ranges of relevant TOC sections
      - Pages flagged by table detection

    Falls back to full_text if nothing qualifies.
    Page numbers in TOC and tables_index are 1-indexed.
    """
    if not toc and not tables_index:
        return "\n\n".join(pages)

    # Build page_num → section_title map from TOC for labelling
    page_to_section: dict[int, str] = {}
    for entry in toc:
        page_to_section[entry["page_number"]] = entry["title"]

    # Collect relevant page numbers (1-indexed)
    relevant_page_nums: set[int] = set()

    # From relevant_sections: start page up to next section start (capped)
    all_toc_pages = sorted(e["page_number"] for e in toc)
    for entry in relevant_sections:
        start = entry["page_number"]
        # Find the next TOC entry's page to bound the range
        next_pages = [p for p in all_toc_pages if p > start]
        end = (next_pages[0] - 1) if next_pages else (start + _MAX_SECTION_PAGE_SPREAD)
        end = min(end, start + _MAX_SECTION_PAGE_SPREAD)
        for p in range(start, end + 1):
            relevant_page_nums.add(p)

    # From tables_index
    for t in tables_index:
        relevant_page_nums.add(t["page"])

    if not relevant_page_nums:
        logger.info("No relevant pages identified — using full document text")
        return "\n\n".join(pages)

    # Build the focused text in page order
    chunks = []
    for page_num in sorted(relevant_page_nums):
        idx = page_num - 1  # convert to 0-indexed
        if 0 <= idx < len(pages):
            label = page_to_section.get(page_num, "")
            header = f"--- Page {page_num}" + (f" ({label})" if label else "") + " ---"
            chunks.append(f"{header}\n{pages[idx]}")

    relevant_text = "\n\n".join(chunks)
    logger.info(
        f"relevant_text: {len(relevant_page_nums)} pages selected "
        f"({len(relevant_text):,} / {sum(len(p) for p in pages):,} chars)"
    )
    return relevant_text


# ── Coordinate patterns ────────────────────────────────────────────────────────

_COORD_PATTERNS: list[tuple[str, str, float]] = [
    # Labeled latitude: "Latitude: 34.1234" or "Lat = -117.456"
    (r"[Ll]at(?:itude)?[\s:=]+(-?\d{1,3}\.\d{4,})", "coordinates", 0.9),
    # DMS: 34°12'56"N  or  117°09'33.1"W
    (r"\d{1,3}°\d{1,2}[\'′]\d{1,2}(?:\.\d+)?[\"″]?\s*[NSEWnsew]", "coordinates", 0.95),
    # Decimal degree pair (comma-separated, optional degree symbol + cardinal)
    (r"-?\d{1,3}\.\d{4,}[°]?\s*[NSns]?,?\s*-?\d{1,3}\.\d{4,}[°]?\s*[EWew]?", "coordinates", 0.85),
    # PLSS: T12N R5E or T12N R5E Section 14
    (r"T\d{1,2}[NS]\s*R\d{1,2}[EW](?:\s*(?:Sec(?:tion)?\s*)?\d{1,2})?", "plss", 0.9),
    # APN: APN 123-456-789
    (r"APN\s*[\d\-]+", "apn", 0.95),
]


def _extract_coordinate_candidates(pages: list[str]) -> list[dict]:
    """
    Regex scan of the full document for coordinate-like patterns.
    Returns [{raw_text, type, page, confidence}] (page is 1-indexed).
    Deduplicates identical raw_text values across pages.
    """
    candidates = []
    seen: Counter = Counter()

    for page_num, page_text in enumerate(pages, start=1):
        for pattern, coord_type, confidence in _COORD_PATTERNS:
            for match in re.finditer(pattern, page_text):
                raw = match.group(0).strip()
                if not raw:
                    continue
                seen[raw] += 1
                # Only emit the first occurrence of each unique value
                if seen[raw] == 1:
                    candidates.append({
                        "raw_text":   raw,
                        "type":       coord_type,
                        "page":       page_num,
                        "confidence": confidence,
                    })

    logger.info(f"Coordinate candidates found: {len(candidates)}")
    return candidates


# ── CLI smoke test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "data/Morongo_Canyon_Highway_Broadband_Comms_Site.pdf"
    result = parse_pdf(path)

    print(f"\n{'='*60}")
    print(f"Pages:        {result['num_pages']}")
    print(f"Is scanned:   {result['is_scanned']}")
    print(f"Full text:    {len(result['full_text']):,} chars")
    print(f"Relevant text:{len(result['relevant_text']):,} chars  "
          f"({100*len(result['relevant_text'])//max(len(result['full_text']),1)}% of full)")
    print(f"\nTOC entries ({len(result['toc'])}):")
    for entry in result["toc"][:20]:
        print(f"  p{entry['page_number']:>3}  {entry['title']}")
    if len(result["toc"]) > 20:
        print(f"  ... and {len(result['toc']) - 20} more")

    print(f"\nRelevant sections ({len(result['relevant_sections'])}):")
    for s in result["relevant_sections"]:
        print(f"  p{s['page_number']:>3}  [{s['score']}] {s['title']}")

    print(f"\nTable pages ({len(result['tables_index'])}):")
    for t in result["tables_index"][:10]:
        print(f"  p{t['page']:>3}")

    print(f"\nCoordinate candidates ({len(result['coordinate_candidates'])}):")
    for c in result["coordinate_candidates"][:10]:
        print(f"  p{c['page']:>3}  [{c['type']}]  {c['raw_text']}")
