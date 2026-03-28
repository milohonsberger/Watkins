"""
app.py
──────
Streamlit UI for the Watkins GeoExtraction Platform.
This file is a thin wrapper — all logic lives in core/.
"""

import logging
import os
import re
import tempfile

import streamlit as st
from dotenv import load_dotenv

from core.extractor import ColumnDef, ExtractionSchema
from core.exporter import to_csv, to_excel, to_geojson, to_geopackage
from core.geocoder import geocode_locations
from core.parser import parse_pdf
from core.extractor import extract_custom_fields, extract_metadata
from core.validator import validate_schema, validate_spatial

load_dotenv()
logging.basicConfig(level=logging.INFO)

st.set_page_config(
    page_title="Watkins GeoExtraction Platform",
    page_icon="🗺️",
    layout="wide",
)

st.markdown(
    "<style>div[data-testid='InputInstructions'] { display: none; }</style>",
    unsafe_allow_html=True,
)

# ── Session state defaults ─────────────────────────────────────────────────────

def _init_state():
    defaults = {
        "schema_columns": [
            {"name": "Sample_ID",    "description": "Unique identifier for each sample or record"},
            {"name": "Description",  "description": "A brief description of the item or finding"},
        ],
        # batch_results: list of {filename, metadata, geocoded_locations, custom_rows}
        "batch_results":      [],
        # combined editable rows (with source_file column prepended)
        "custom_rows":        None,
        "schema_validation":  None,
        "spatial_validation": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🗺️ Watkins GeoExtraction Platform")
    st.caption("Extract → Geocode → Export")
    st.divider()

    uploaded_files = st.file_uploader("Upload PDF(s)", type=["pdf"], accept_multiple_files=True)

    st.divider()
    st.subheader("Extraction Schema")
    st.caption("Define what data you want to extract. The LLM uses these descriptions to find values.")

    # Schema builder
    columns_to_remove = []
    for i, col in enumerate(st.session_state.schema_columns):
        with st.container():
            c1, c2 = st.columns([3, 1])
            with c1:
                col["name"] = st.text_input(
                    "Column name", value=col["name"], key=f"col_name_{i}"
                )
            with c2:
                st.write("")
                st.write("")
                if st.button("✕", key=f"remove_{i}", help="Remove this column"):
                    columns_to_remove.append(i)
            col["description"] = st.text_input(
                "Description", value=col["description"], key=f"col_desc_{i}"
            )

    for i in reversed(columns_to_remove):
        st.session_state.schema_columns.pop(i)
        st.rerun()

    if st.button("＋ Add Column", use_container_width=True):
        st.session_state.schema_columns.append({"name": "", "description": ""})
        st.rerun()

    if len(st.session_state.schema_columns) > 20:
        st.warning("More than 20 columns defined. The LLM may produce incomplete results.")

    st.divider()
    target_section = st.text_input(
        "Target section / table (optional)",
        placeholder="e.g. Appendix A, Table 3",
        help="Leave blank to search the entire document.",
    )


# ── Main area ──────────────────────────────────────────────────────────────────

st.header("Watkins GeoExtraction Platform")

if not os.getenv("GOOGLE_API_KEY"):
    st.error("**GOOGLE_API_KEY** is not set. Create a `.env` file with your Gemini API key.")
    st.stop()

if not uploaded_files:
    st.info("Upload one or more PDFs in the sidebar to get started.")
    st.stop()

# Validate schema before running
valid_columns = [
    c for c in st.session_state.schema_columns
    if c["name"].strip() and c["description"].strip()
]
if not valid_columns:
    st.warning("Define at least one column with a name and description before running.")
    st.stop()

# ── Run pipeline ───────────────────────────────────────────────────────────────

if st.button("▶ Run Extraction", type="primary", use_container_width=True):
    schema = ExtractionSchema(
        columns=[ColumnDef(name=c["name"].strip(), description=c["description"].strip())
                 for c in valid_columns],
        target_section=target_section.strip() or None,
    )

    st.session_state.batch_results = []
    st.session_state.custom_rows = None
    st.session_state.schema_validation = None
    st.session_state.spatial_validation = None

    for uploaded_file in uploaded_files:
        fname = uploaded_file.name
        with st.status(f"Processing {fname}…", expanded=True) as status:
            result = {"filename": fname, "metadata": {}, "geocoded_locations": [], "custom_rows": []}

            st.write("📄 Parsing PDF…")
            try:
                parsed_doc = parse_pdf(uploaded_file)
                st.write(f"   ✓ {parsed_doc['num_pages']} pages, {len(parsed_doc['full_text']):,} characters")
            except Exception as e:
                st.error(f"PDF parsing failed: {e}")
                status.update(label=f"{fname} — failed", state="error", expanded=False)
                st.session_state.batch_results.append(result)
                continue

            st.write("🔍 Extracting project metadata…")
            try:
                metadata = extract_metadata(parsed_doc)
                result["metadata"] = metadata
                st.write(f"   ✓ {len(metadata)} metadata fields extracted")
            except Exception as e:
                st.warning(f"Metadata extraction failed: {e}")

            st.write("🌐 Geocoding project location…")
            try:
                location_raw = result["metadata"].get("location_raw", "")
                if location_raw and location_raw not in ("NOT FOUND", "EXTRACTION FAILED"):
                    geocoded = geocode_locations([{"raw_text": location_raw, "type": "address", "confidence": 1.0}])
                else:
                    geocoded = []
                result["geocoded_locations"] = geocoded
                success = sum(1 for g in geocoded if g.get("geocode_status") == "success")
                st.write(f"   ✓ {success}/{len(geocoded)} location(s) geocoded")
            except Exception as e:
                st.warning(f"Geocoding failed: {e}")

            st.write(f"📊 Extracting custom fields ({len(schema.columns)} columns)…")
            try:
                rows = extract_custom_fields(parsed_doc, schema)
                result["custom_rows"] = rows
                st.write(f"   ✓ {len(rows)} rows extracted")
            except Exception as e:
                st.error(f"Custom extraction failed: {e}")

            st.session_state.batch_results.append(result)
            status.update(label=f"{fname} — complete", state="complete", expanded=False)

    # Build combined rows with source_file prepended
    combined_rows = []
    for r in st.session_state.batch_results:
        for row in r["custom_rows"]:
            combined_rows.append({"source_file": r["filename"], **row})
    st.session_state.custom_rows = combined_rows

    # Validate combined results
    all_geocoded = [g for r in st.session_state.batch_results for g in r["geocoded_locations"]]
    if combined_rows:
        st.session_state.schema_validation = validate_schema(combined_rows, schema)
    if all_geocoded:
        st.session_state.spatial_validation = validate_spatial(all_geocoded)

    st.session_state["_schema_used"] = schema


# ── Results ────────────────────────────────────────────────────────────────────

if st.session_state.batch_results:
    st.divider()

    # Metadata summary — one expander per file
    for r in st.session_state.batch_results:
        meta = r["metadata"]
        label = meta.get("project_name") or r["filename"]
        with st.expander(f"📋 {label}", expanded=len(st.session_state.batch_results) == 1):
            if meta:
                cols = st.columns(3)
                for i, (key, val) in enumerate(meta.items()):
                    cols[i % 3].metric(key.replace("_", " ").title(), val)
            else:
                st.caption("No metadata extracted.")

    tab_data, tab_map, tab_issues = st.tabs(["📊 Extracted Data", "🗺️ Map", "⚠️ Validation"])

    # ── Data tab
    with tab_data:
        rows = st.session_state.custom_rows or []
        if rows:
            st.caption(f"{len(rows)} row(s) across {len(st.session_state.batch_results)} file(s)")
            edited = st.data_editor(
                rows,
                use_container_width=True,
                num_rows="dynamic",
                key="data_editor",
            )
            st.session_state.custom_rows = edited
        else:
            st.info("No custom data extracted yet.")

    # ── Map tab
    with tab_map:
        import pandas as pd
        all_geocoded = [g for r in st.session_state.batch_results for g in r["geocoded_locations"]]
        mappable = [
            {"lat": g["latitude"], "lon": g["longitude"], "label": g.get("raw_text", "")}
            for g in all_geocoded
            if g.get("latitude") is not None and g.get("longitude") is not None
        ]
        if mappable:
            st.map(pd.DataFrame(mappable), latitude="lat", longitude="lon")
            st.caption(f"{len(mappable)} geocoded location(s) shown")
        else:
            st.info("No geocoded locations to display.")

    # ── Validation tab
    with tab_issues:
        import pandas as pd
        schema_val = st.session_state.schema_validation
        spatial_val = st.session_state.spatial_validation

        if schema_val:
            if schema_val["valid"]:
                st.success(f"Schema: all {schema_val['total_records']} records are valid.")
            else:
                st.warning(f"Schema: {len(schema_val['issues'])} issue(s) found.")
                st.dataframe(pd.DataFrame(schema_val["issues"]), use_container_width=True)

        if spatial_val:
            if spatial_val["valid"]:
                st.success(f"Spatial: all {spatial_val['total_records']} locations are valid.")
            else:
                st.warning(f"Spatial: {len(spatial_val['issues'])} issue(s) found.")
                st.dataframe(pd.DataFrame(spatial_val["issues"]), use_container_width=True)

        if not schema_val and not spatial_val:
            st.info("Run the pipeline to see validation results.")

    # ── Export ─────────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Export")

    schema_used = st.session_state.get("_schema_used")
    all_edited_rows = st.session_state.custom_rows or []

    def _export_bytes(suffix, write_fn):
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        write_fn(tmp_path)
        with open(tmp_path, "rb") as f:
            data = f.read()
        os.unlink(tmp_path)
        return data

    for idx, r in enumerate(st.session_state.batch_results):
        # Derive filename stem from extracted project name, fall back to PDF filename
        _raw = r["metadata"].get("project_name", "")
        if _raw and _raw not in ("NOT FOUND", "EXTRACTION FAILED"):
            stem = re.sub(r'[^\w\-]', '_', _raw).strip('_')[:80]
        else:
            stem = os.path.splitext(r["filename"])[0]

        # Rows for this file (use edited combined rows filtered by source_file)
        file_rows = [row for row in all_edited_rows if row.get("source_file") == r["filename"]]

        spatial_records = [g for g in r["geocoded_locations"] if g.get("latitude") is not None]

        def _geo_features_for(rows, result):
            geo = spatial_records[0] if spatial_records else {}
            geo_fields = {
                "latitude":           geo.get("latitude"),
                "longitude":          geo.get("longitude"),
                "geocode_source":     geo.get("geocode_source", ""),
                "geocode_confidence": geo.get("geocode_confidence", 0),
                "geocode_status":     geo.get("geocode_status", ""),
            }
            flat_meta = {f"meta_{k}": v for k, v in result["metadata"].items()}
            if rows:
                return [{**row, **flat_meta, **geo_fields} for row in rows]
            return [{**flat_meta, **geo_fields}] if spatial_records else []

        export_meta = {
            "source_file":    r["filename"],
            "schema_columns": ", ".join(c.name for c in schema_used.columns) if schema_used else "",
            "total_records":  len(file_rows),
            "geocoded_count": sum(1 for g in r["geocoded_locations"] if g.get("geocode_status") == "success"),
        }

        st.caption(f"**{stem}**")
        col_xl, col_csv, col_gpkg, col_geojson = st.columns(4)

        with col_xl:
            st.download_button(
                "⬇ Excel",
                _export_bytes(".xlsx", lambda p, rows=file_rows, meta=r["metadata"], em=export_meta:
                    to_excel(rows, p, metadata=meta, export_metadata=em)),
                f"{stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"dl_xl_{idx}",
            )

        with col_csv:
            st.download_button(
                "⬇ CSV",
                _export_bytes(".csv", lambda p, rows=file_rows: to_csv(rows, p)),
                f"{stem}.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"dl_csv_{idx}",
            )

        with col_gpkg:
            if spatial_records:
                st.download_button(
                    "⬇ GeoPackage",
                    _export_bytes(".gpkg", lambda p, rows=file_rows, res=r:
                        to_geopackage(_geo_features_for(rows, res), p)),
                    f"{stem}.gpkg",
                    mime="application/octet-stream",
                    use_container_width=True,
                    key=f"dl_gpkg_{idx}",
                )
            else:
                st.button("⬇ GeoPackage", disabled=True, use_container_width=True,
                          key=f"dl_gpkg_dis_{idx}", help="No geocoded locations available.")

        with col_geojson:
            if spatial_records:
                st.download_button(
                    "⬇ GeoJSON",
                    _export_bytes(".geojson", lambda p, rows=file_rows, res=r:
                        to_geojson(_geo_features_for(rows, res), p)),
                    f"{stem}.geojson",
                    mime="application/geo+json",
                    use_container_width=True,
                    key=f"dl_geojson_{idx}",
                )
            else:
                st.button("⬇ GeoJSON", disabled=True, use_container_width=True,
                          key=f"dl_geojson_dis_{idx}", help="No geocoded locations available.")

        if idx < len(st.session_state.batch_results) - 1:
            st.divider()
