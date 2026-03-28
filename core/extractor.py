"""
extractor.py
────────────
LLM extraction engine. Three focused passes:
  1. extract_metadata  — project-level summary fields (auto-detected)
  2. extract_locations — all location references in the document
  3. extract_custom_fields — user-defined structured rows

No UI dependencies. Uses google-genai directly for single-turn LLM calls.
"""

import json
import logging
import os
from dataclasses import dataclass, field

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"


# ── Schema types ───────────────────────────────────────────────────────────────

@dataclass
class ColumnDef:
    name: str          # Column header used in output
    description: str   # Plain-English description sent to the LLM


@dataclass
class ExtractionSchema:
    columns: list[ColumnDef]
    target_section: str | None = None  # Optional: focus on a specific section/table


# ── Prompts ────────────────────────────────────────────────────────────────────

_METADATA_PROMPT = """
You are extracting project-level metadata from a technical report.

<report>
{report_text}
</report>

Extract these fields. Use ONLY information explicitly stated in the document.
Use "NOT FOUND" for any field you cannot locate.

Fields:
- project_name: The name or title of the project or report
- date: Publication or completion date (use the format shown in the document)
- client: The organisation or person who commissioned the report
- author: The person or organisation who prepared the report
- location_raw: The primary project location as stated (address, site name, or city/state)
- purpose: The project's goal or scope. Two sentences maximum.

Rules:
- Never use HTML entities. Output plain text only.
- Do not add any explanation outside the JSON.

Return ONLY a valid JSON object in this exact format:
{{
  "project_name": "...",
  "date": "...",
  "client": "...",
  "author": "...",
  "location_raw": "...",
  "purpose": "..."
}}
"""

_LOCATIONS_PROMPT = """
You are extracting all location references from a technical report.

<report>
{report_text}
</report>

Find every distinct location mentioned in this document. For each one, determine:
- raw_text: the exact location string as it appears in the document
- type: one of "address" | "coordinates" | "apn" | "plss" | "city_state" | "unknown"
  - address: a street address (e.g. "1234 Main St, San Diego, CA")
  - coordinates: explicit lat/lon or DMS coordinates
  - apn: Assessor Parcel Number (e.g. "APN 123-456-78")
  - plss: Public Land Survey System (e.g. "T12N R5E Section 14")
  - city_state: city and/or state only (e.g. "San Diego, CA")
  - unknown: cannot determine type
- confidence: your confidence this is a real location reference, from 0.0 to 1.0

Exclude generic references like "the site", "the project area", "the study area".
Include only references that could be geocoded or looked up.

Rules:
- Never use HTML entities. Output plain text only.
- Do not add any explanation outside the JSON.

Return ONLY a valid JSON object in this format:
{{
  "locations": [
    {{"raw_text": "1234 Main St, San Diego, CA", "type": "address", "confidence": 0.95}},
    {{"raw_text": "32.7157° N, 117.1611° W", "type": "coordinates", "confidence": 1.0}}
  ]
}}
"""

_CUSTOM_PROMPT = """
You are extracting structured tabular data from a technical report.

<report>
{report_text}
</report>

Focus your search on: {target_section}

Extract ALL rows matching the following column definitions:
{column_definitions}

Rules:
- Return ALL matching rows. Do not skip any.
- Use "N/A" if a value is not found for a cell.
- Never use HTML entities. Output plain text only.
- Do not add any explanation outside the JSON.

Return ONLY a valid JSON object in this format:
{{
  "rows": [
    {example_row}
  ]
}}
"""


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_metadata(parsed_doc: dict) -> dict:
    """
    Pass 1: Extract project-level metadata fields from the document.
    Returns a flat dict with keys: project_name, date, client, author, location_raw, purpose.
    """
    logger.info("Extraction pass 1: metadata")
    report_text = _truncate(parsed_doc["full_text"])
    prompt = _METADATA_PROMPT.format(report_text=report_text)

    response = _call_llm(prompt)
    try:
        result = _parse_json_object(response)
        logger.info(f"Metadata extracted: {list(result.keys())}")
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Metadata extraction failed to parse JSON: {e}")
        return {k: "EXTRACTION FAILED" for k in
                ["project_name", "date", "client", "author", "location_raw", "purpose"]}


def extract_locations(parsed_doc: dict) -> list[dict]:
    """
    Pass 2: Extract all location references from the document.
    Returns list of dicts with: raw_text, type, confidence.
    """
    logger.info("Extraction pass 2: locations")
    report_text = _truncate(parsed_doc["full_text"])
    prompt = _LOCATIONS_PROMPT.format(report_text=report_text)

    response = _call_llm(prompt, max_tokens=16000)
    try:
        result = _parse_json_object(response)
        locations = result.get("locations", [])
        logger.info(f"Locations found: {len(locations)}")
        return locations
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Location extraction failed to parse JSON: {e}")
        return []


def extract_custom_fields(parsed_doc: dict, schema: ExtractionSchema) -> list[dict]:
    """
    Pass 3: Extract user-defined structured rows using the provided schema.
    Returns list of dicts where keys match schema column names.
    """
    logger.info(f"Extraction pass 3: custom fields ({len(schema.columns)} columns)")
    report_text = _truncate(parsed_doc["full_text"])

    column_definitions = "\n".join(
        f"- {col.name}: {col.description}" for col in schema.columns
    )
    example_row = json.dumps(
        {col.name: "example value" for col in schema.columns}, indent=4
    )
    target = schema.target_section or "the entire document"

    prompt = _CUSTOM_PROMPT.format(
        report_text=report_text,
        target_section=target,
        column_definitions=column_definitions,
        example_row=example_row,
    )

    response = _call_llm(prompt, max_tokens=16000)
    try:
        result = _parse_json_object(response)
        rows = result.get("rows", [])
        logger.info(f"Custom rows extracted: {len(rows)}")
        return rows
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Custom extraction failed to parse JSON: {e}")
        return []


# ── Private helpers ────────────────────────────────────────────────────────────

def _call_llm(prompt: str, max_tokens: int = 8000) -> str:
    """Single-turn LLM call using google-genai. Returns raw response text."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY is not set in environment / .env file.")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.1,
        ),
    )
    return response.text or ""


def _parse_json_object(response_text: str) -> dict:
    """Strip markdown fences and parse the first JSON object from a response."""
    raw = response_text.strip()

    # Strip ```json ... ``` fences if present
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # Find the opening brace
    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response.")
    clean = raw[start:]

    # Attempt to close incomplete JSON
    if not clean.endswith("}"):
        last_comma = clean.rfind('",')
        if last_comma > 0:
            clean = clean[:last_comma + 1]
            clean += ',\n"_truncated": true\n}'
        else:
            clean += '\n"_truncated": true\n}'

    return json.loads(clean)


def _truncate(text: str, max_chars: int = 800_000) -> str:
    if len(text) > max_chars:
        logger.warning(f"Report text truncated from {len(text):,} to {max_chars:,} chars.")
        return text[:max_chars]
    return text
