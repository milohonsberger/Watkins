"""
tests/test_parser.py
────────────────────
Unit and integration tests for core/parser.py.

Unit tests: cover pure-Python helpers with synthetic data — no PDF required.
Integration tests: exercise parse_pdf() against the real sample PDF.
  Skipped automatically when data/Morongo_Canyon_Highway_Broadband_Comms_Site.pdf
  is not present (the data/ directory is gitignored).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv
import os

import pytest

from core.parser import (
    _extract_coordinate_candidates,
    _extract_relevant_pages,
    _extract_toc,
    _score_section_relevance,
    parse_pdf,
)

load_dotenv()

SAMPLE_PDF_NAME = os.environ.get("SAMPLE_PDF_NAME")

SAMPLE_PDF = Path(__file__).parent.parent / "data" / SAMPLE_PDF_NAME
requires_sample_pdf = pytest.mark.skipif(
    not SAMPLE_PDF.exists(),
    reason="Sample PDF not present (data/ is gitignored)",
)


# ── _score_section_relevance ───────────────────────────────────────────────────

class TestScoreSectionRelevance:

    def test_high_spatial_keyword_scores_at_least_2(self):
        toc = [{"title": "Project Location", "page_number": 5}]
        result = _score_section_relevance(toc)
        assert len(result) == 1
        assert result[0]["score"] >= 2

    def test_medium_spatial_keyword_scores_at_least_1(self):
        toc = [{"title": "Study Area Overview", "page_number": 3}]
        result = _score_section_relevance(toc)
        assert len(result) == 1
        assert result[0]["score"] >= 1

    def test_table_word_scores_at_least_2(self):
        toc = [{"title": "Species Table", "page_number": 10}]
        result = _score_section_relevance(toc)
        assert len(result) == 1
        assert result[0]["score"] >= 2

    def test_numbered_table_pattern_scores(self):
        toc = [{"title": "Table 3-3. Special Status Species", "page_number": 28}]
        result = _score_section_relevance(toc)
        assert len(result) == 1

    def test_table_of_contents_excluded(self):
        toc = [{"title": "Table of Contents", "page_number": 5}]
        result = _score_section_relevance(toc)
        assert result == []

    def test_cover_page_excluded(self):
        toc = [{"title": "Cover", "page_number": 1}]
        result = _score_section_relevance(toc)
        assert result == []

    def test_references_excluded(self):
        toc = [{"title": "References", "page_number": 80}]
        result = _score_section_relevance(toc)
        assert result == []

    def test_irrelevant_section_not_included(self):
        toc = [{"title": "Chapter 1. Introduction", "page_number": 9}]
        result = _score_section_relevance(toc)
        assert result == []

    def test_mixed_toc_returns_only_relevant(self):
        toc = [
            {"title": "Chapter 1. Introduction", "page_number": 9},
            {"title": "Project Location", "page_number": 15},
            {"title": "Table of Contents", "page_number": 5},
            {"title": "Species Inventory Table", "page_number": 30},
        ]
        result = _score_section_relevance(toc)
        titles = [r["title"] for r in result]
        assert "Project Location" in titles
        assert "Species Inventory Table" in titles
        assert "Chapter 1. Introduction" not in titles
        assert "Table of Contents" not in titles

    def test_result_preserves_title_and_page_number(self):
        toc = [{"title": "Site Description", "page_number": 12}]
        result = _score_section_relevance(toc)
        assert result[0]["title"] == "Site Description"
        assert result[0]["page_number"] == 12

    def test_empty_toc_returns_empty(self):
        assert _score_section_relevance([]) == []

    def test_keyword_match_is_case_insensitive(self):
        toc = [{"title": "PROJECT LOCATION", "page_number": 5}]
        result = _score_section_relevance(toc)
        assert len(result) == 1

    def test_appendix_scores_as_table_indicator(self):
        toc = [{"title": "Appendix A – Biological Report", "page_number": 70}]
        result = _score_section_relevance(toc)
        assert len(result) == 1


# ── _extract_coordinate_candidates ────────────────────────────────────────────

class TestExtractCoordinateCandidates:

    def test_decimal_degree_pair_detected(self):
        pages = ["Site coordinates: 34.1234, -117.5678"]
        results = _extract_coordinate_candidates(pages)
        assert any(c["type"] == "coordinates" for c in results)

    def test_labeled_latitude_detected(self):
        pages = ["Latitude: 34.5678"]
        results = _extract_coordinate_candidates(pages)
        assert any(c["type"] == "coordinates" for c in results)

    def test_labeled_lat_abbreviation_detected(self):
        pages = ["Lat = 34.5678"]
        results = _extract_coordinate_candidates(pages)
        assert any(c["type"] == "coordinates" for c in results)

    def test_dms_coordinates_detected(self):
        pages = ["Location: 34°07'24\"N 117°34'05\"W"]
        results = _extract_coordinate_candidates(pages)
        assert any(c["type"] == "coordinates" for c in results)

    def test_plss_detected(self):
        pages = ["Legal Description: T12N R5E Section 14"]
        results = _extract_coordinate_candidates(pages)
        assert any(c["type"] == "plss" for c in results)

    def test_plss_without_section_detected(self):
        pages = ["The parcel is within T12N R5E."]
        results = _extract_coordinate_candidates(pages)
        assert any(c["type"] == "plss" for c in results)

    def test_apn_detected(self):
        pages = ["Assessor Parcel Number: APN 123-456-789"]
        results = _extract_coordinate_candidates(pages)
        assert any(c["type"] == "apn" for c in results)

    def test_page_number_is_1_indexed(self):
        pages = ["nothing here", "APN 123-456-789"]
        results = _extract_coordinate_candidates(pages)
        apn = next(c for c in results if c["type"] == "apn")
        assert apn["page"] == 2

    def test_duplicate_raw_text_deduplicated(self):
        # Same string appears on two pages — should emit only once.
        pages = ["APN 123-456-789", "APN 123-456-789"]
        results = _extract_coordinate_candidates(pages)
        apn_results = [c for c in results if c["type"] == "apn"]
        assert len(apn_results) == 1

    def test_each_result_has_required_keys(self):
        pages = ["APN 123-456-789"]
        results = _extract_coordinate_candidates(pages)
        for r in results:
            assert "raw_text" in r
            assert "type" in r
            assert "page" in r
            assert "confidence" in r

    def test_confidence_between_0_and_1(self):
        pages = ["APN 123-456-789", "T12N R5E", "Latitude: 34.5678"]
        results = _extract_coordinate_candidates(pages)
        for r in results:
            assert 0.0 <= r["confidence"] <= 1.0

    def test_empty_pages_returns_empty(self):
        assert _extract_coordinate_candidates([]) == []

    def test_no_coords_returns_empty(self):
        pages = ["This document contains no spatial references."]
        results = _extract_coordinate_candidates(pages)
        assert results == []


# ── _extract_relevant_pages ───────────────────────────────────────────────────

class TestExtractRelevantPages:

    def _make_pages(self, n: int) -> list[str]:
        return [f"Content of page {i + 1}." for i in range(n)]

    def test_page_markers_embedded_in_output(self):
        pages = self._make_pages(5)
        toc = [{"title": "Project Location", "page_number": 1},
               {"title": "Chapter 2", "page_number": 4}]
        sections = [{"title": "Project Location", "page_number": 1, "score": 2}]
        result = _extract_relevant_pages(pages, toc, sections, tables_index=[])
        assert "--- Page 1" in result

    def test_section_title_in_page_marker(self):
        pages = self._make_pages(5)
        toc = [{"title": "Site Description", "page_number": 1},
               {"title": "Chapter 2", "page_number": 4}]
        sections = [{"title": "Site Description", "page_number": 1, "score": 2}]
        result = _extract_relevant_pages(pages, toc, sections, tables_index=[])
        assert "Site Description" in result

    def test_relevant_text_shorter_than_full_when_sections_found(self):
        pages = self._make_pages(20)
        toc = [{"title": "Site Description", "page_number": 1},
               {"title": "Chapter 2", "page_number": 8}]
        sections = [{"title": "Site Description", "page_number": 1, "score": 2}]
        result = _extract_relevant_pages(pages, toc, sections, tables_index=[])
        full = "\n\n".join(pages)
        assert len(result) < len(full)

    def test_fallback_to_full_text_when_no_sections_and_no_tables(self):
        pages = ["page one", "page two"]
        result = _extract_relevant_pages(pages, toc=[], relevant_sections=[], tables_index=[])
        assert result == "\n\n".join(pages)

    def test_fallback_when_empty_toc_and_no_tables(self):
        pages = self._make_pages(3)
        result = _extract_relevant_pages(pages, toc=[], relevant_sections=[], tables_index=[])
        assert "Content of page 1" in result
        assert "Content of page 2" in result
        assert "Content of page 3" in result

    def test_table_page_included_in_output(self):
        pages = ["Page one text.", "Page two has a big table."]
        result = _extract_relevant_pages(
            pages, toc=[], relevant_sections=[], tables_index=[{"page": 2}]
        )
        assert "Page two has a big table." in result

    def test_out_of_range_table_page_ignored(self):
        pages = self._make_pages(3)
        result = _extract_relevant_pages(
            pages, toc=[], relevant_sections=[], tables_index=[{"page": 99}]
        )
        # Should not crash; if no valid pages, falls back to full text.
        assert isinstance(result, str)

    def test_section_page_spread_capped(self):
        # A section starting at page 1 with no next section should not pull
        # in more than _MAX_SECTION_PAGE_SPREAD pages.
        from core.parser import _MAX_SECTION_PAGE_SPREAD
        pages = self._make_pages(50)
        toc = [{"title": "Site Description", "page_number": 1}]
        sections = [{"title": "Site Description", "page_number": 1, "score": 2}]
        result = _extract_relevant_pages(pages, toc, sections, tables_index=[])
        page_markers = [line for line in result.splitlines() if "--- Page" in line]
        assert len(page_markers) <= _MAX_SECTION_PAGE_SPREAD + 1


# ── _extract_toc (text fallback) ──────────────────────────────────────────────

class TestExtractTocTextFallback:
    """Test the dot-leader text fallback without a real fitz document."""

    def _make_mock_doc(self, toc_entries=None):
        doc = MagicMock()
        doc.get_toc.return_value = toc_entries or []
        return doc

    def test_returns_bookmark_toc_when_available(self):
        doc = self._make_mock_doc(toc_entries=[[1, "Project Location", 5]])
        result = _extract_toc(doc, pages=[])
        assert result == [{"title": "Project Location", "page_number": 5}]

    def test_text_fallback_parses_dot_leader_lines(self):
        doc = self._make_mock_doc(toc_entries=[])
        pages = ["Project Location ........ 12\nSite Description ........ 15\n"]
        result = _extract_toc(doc, pages)
        titles = [e["title"] for e in result]
        assert "Project Location" in titles
        assert "Site Description" in titles

    def test_text_fallback_extracts_correct_page_numbers(self):
        doc = self._make_mock_doc(toc_entries=[])
        pages = ["Site Description ........ 15\n"]
        result = _extract_toc(doc, pages)
        assert result[0]["page_number"] == 15

    def test_empty_toc_and_no_dot_leaders_returns_empty(self):
        doc = self._make_mock_doc(toc_entries=[])
        pages = ["This page has no table of contents."]
        result = _extract_toc(doc, pages)
        assert result == []

    def test_bookmark_toc_takes_precedence_over_text(self):
        doc = self._make_mock_doc(toc_entries=[[1, "From Bookmarks", 3]])
        pages = ["From Text ........ 10\n"]
        result = _extract_toc(doc, pages)
        assert result[0]["title"] == "From Bookmarks"

    def test_blank_titles_filtered_from_bookmark_toc(self):
        doc = self._make_mock_doc(toc_entries=[[1, "  ", 3], [1, "Real Section", 5]])
        result = _extract_toc(doc, pages=[])
        assert len(result) == 1
        assert result[0]["title"] == "Real Section"


# ── Integration: parse_pdf() data contract ────────────────────────────────────

@requires_sample_pdf
class TestParsePdfContract:
    """
    Verify that parse_pdf() returns the expected data contract.
    These tests act as a regression guard — if any key is renamed or its type
    changes, the tests will catch it before downstream consumers break.
    """

    @pytest.fixture(scope="class")
    def parsed(self):
        return parse_pdf(SAMPLE_PDF)

    def test_full_text_is_nonempty_string(self, parsed):
        assert isinstance(parsed["full_text"], str)
        assert len(parsed["full_text"]) > 0

    def test_relevant_text_is_nonempty_string(self, parsed):
        assert isinstance(parsed["relevant_text"], str)
        assert len(parsed["relevant_text"]) > 0

    def test_pages_is_list_of_strings(self, parsed):
        assert isinstance(parsed["pages"], list)
        assert all(isinstance(p, str) for p in parsed["pages"])

    def test_num_pages_is_positive_int(self, parsed):
        assert isinstance(parsed["num_pages"], int)
        assert parsed["num_pages"] > 0

    def test_num_pages_matches_pages_list_length(self, parsed):
        assert parsed["num_pages"] == len(parsed["pages"])

    def test_metadata_is_dict_with_expected_keys(self, parsed):
        meta = parsed["metadata"]
        assert isinstance(meta, dict)
        for key in ("title", "author", "subject", "creator"):
            assert key in meta

    def test_toc_is_list_of_dicts_with_title_and_page(self, parsed):
        assert isinstance(parsed["toc"], list)
        for entry in parsed["toc"]:
            assert "title" in entry
            assert "page_number" in entry
            assert isinstance(entry["title"], str)
            assert isinstance(entry["page_number"], int)

    def test_relevant_sections_is_list_of_dicts(self, parsed):
        assert isinstance(parsed["relevant_sections"], list)
        for s in parsed["relevant_sections"]:
            assert "title" in s
            assert "page_number" in s
            assert "score" in s

    def test_tables_index_is_list_of_dicts_with_page(self, parsed):
        assert isinstance(parsed["tables_index"], list)
        for t in parsed["tables_index"]:
            assert "page" in t
            assert isinstance(t["page"], int)

    def test_coordinate_candidates_is_list(self, parsed):
        assert isinstance(parsed["coordinate_candidates"], list)

    def test_is_scanned_is_bool(self, parsed):
        assert isinstance(parsed["is_scanned"], bool)

    def test_all_required_keys_present(self, parsed):
        required = {
            "full_text", "relevant_text", "pages", "num_pages",
            "metadata", "toc", "relevant_sections", "tables_index",
            "coordinate_candidates", "is_scanned",
        }
        assert required.issubset(parsed.keys())


@requires_sample_pdf
class TestParsePdfBehavior:
    """
    Verify that parse_pdf() produces sensible output for the known sample PDF.
    These tests encode expectations specific to the sample document and serve as
    a canary if parsing logic changes in unexpected ways.
    """

    @pytest.fixture(scope="class")
    def parsed(self):
        return parse_pdf(SAMPLE_PDF)

    def test_relevant_text_shorter_than_full_text(self, parsed):
        assert len(parsed["relevant_text"]) < len(parsed["full_text"])

    def test_sample_pdf_is_not_scanned(self, parsed):
        assert parsed["is_scanned"] is False

    def test_toc_has_multiple_entries(self, parsed):
        assert len(parsed["toc"]) > 10

    def test_relevant_text_contains_page_markers(self, parsed):
        assert "--- Page" in parsed["relevant_text"]

    def test_relevant_sections_are_subset_of_toc(self, parsed):
        toc_titles = {e["title"] for e in parsed["toc"]}
        for s in parsed["relevant_sections"]:
            assert s["title"] in toc_titles

    def test_tables_detected_on_expected_pages(self, parsed):
        table_pages = {t["page"] for t in parsed["tables_index"]}
        # Pages 29 and 32 contain the special status species tables.
        assert table_pages & {29, 32}

    def test_file_like_object_accepted(self):
        with open(SAMPLE_PDF, "rb") as f:
            result = parse_pdf(f)
        assert result["num_pages"] > 0
        assert isinstance(result["full_text"], str)
