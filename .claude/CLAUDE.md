# Watkins - GIS Technician Agent

## Overview
Agentic Pipeline to perform work that is typically left to the entry-level GIS Technician. This mostly involves digitizing unstructured documents (Environmental Impact Statements, Geotechnical Reports, Boring Logs) into structured, geospatial (and non-spatial) datasets. 

## Current Stack:
- Agents: google-adk (Migrating to pydantic ai)
- PDF-Parsing: pymupdf/fitz
- Geocoding: Nomatim (OpenStreetMap Based).
- User Interface: streamlit (**Future Development**: Migrate to a React app once the core library is functioning properly)

## Pipeline Stages:
```
parse_pdf() -> [Triage Agent] -> [Extraction Agent(s) -- Location and Tabular] -> [Normalizer] -> [Exporter]
```

### PDF Parsing
- Parses pdf into a dictionary containing relevant section information for extraction:
```
{
    "full_text": str,                    # complete extracted text (fallback)
    "relevant_text": str,               # spatially-focused pages — primary LLM input
    "pages": list[str],                 # text per page (0-indexed)
    "num_pages": int,
    "metadata": dict,                   # PDF document metadata
    "toc": list[dict],                  # [{title, page_number}]
    "relevant_sections": list[dict],    # TOC entries that scored ≥ 1
    "tables_index": list[dict],         # [{page}] for pages with tables
    "coordinate_candidates": list[dict],# regex-found spatial refs
    "is_scanned": bool,
}
```

### Triage Agent
- Uses the output of the parsed PDF to read structured signals, classifies doc type, returns routing plan
- Finds geocodable information references -> proceeds to attempt geocoding/location extraction
- Warns user if only narrative, vague descriptions are available in the report
- No Location information detected -> Doesn't push to Location Extraction Agent 

### Extraction Agent(s)
- Location Extraction Agent: runs if coordinate_candidates or relevant_sections suggest it
- Table Extraction Agent: Runs if relevant tables are found in the tables_index

### Normalizer
- Normalizes outputs for export (GeoDataFrame, DataFrame)
- Stages the data for user QA

### Exporter
- Exports the data to a GPKG, GeoJSON, CSV, EXCEL
- Future support for Insert / Upsert into a PostGIS database

