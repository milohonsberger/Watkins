"""
validator.py
────────────
Schema and spatial validation for extracted records.
Returns structured validation reports rather than raising exceptions,
so the UI can display issues without crashing the pipeline.
"""

import logging
from core.extractor import ExtractionSchema

logger = logging.getLogger(__name__)

LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)


def validate_schema(records: list[dict], schema: ExtractionSchema) -> dict:
    """
    Check that every record contains all columns defined in the schema.
    Missing or empty values are flagged as issues (not fatal).

    Returns:
        {
            "valid": bool,
            "total_records": int,
            "issues": list[dict]  # {row_index, column, issue_type, message}
        }
    """
    issues = []
    expected_columns = {col.name for col in schema.columns}

    for i, record in enumerate(records):
        for col_name in expected_columns:
            value = record.get(col_name)
            if value is None:
                issues.append({
                    "row_index": i,
                    "column": col_name,
                    "issue_type": "missing_column",
                    "message": f"Column '{col_name}' is missing from this record.",
                })
            elif str(value).strip() in ("", "N/A", "NOT FOUND", "EXTRACTION FAILED"):
                issues.append({
                    "row_index": i,
                    "column": col_name,
                    "issue_type": "empty_value",
                    "message": f"Column '{col_name}' has no extracted value.",
                })

    logger.info(f"Schema validation: {len(issues)} issues across {len(records)} records")
    return {
        "valid": len(issues) == 0,
        "total_records": len(records),
        "issues": issues,
    }


def validate_spatial(records: list[dict], bounds: dict | None = None) -> dict:
    """
    Check spatial data quality on geocoded location records.

    Args:
        records: List of geocoded location dicts (from geocoder.geocode_locations).
        bounds:  Optional bounding box {"min_lat", "max_lat", "min_lon", "max_lon"}
                 to flag points outside an expected area.

    Returns same structure as validate_schema.
    """
    issues = []

    for i, record in enumerate(records):
        lat = record.get("latitude")
        lon = record.get("longitude")

        # No geometry at all
        if lat is None or lon is None:
            status = record.get("geocode_status", "unknown")
            issues.append({
                "row_index": i,
                "column": "geometry",
                "issue_type": "no_geometry",
                "message": f"No coordinates — geocode_status: '{status}'.",
            })
            continue

        # Out-of-range coordinates
        if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
            issues.append({
                "row_index": i,
                "column": "latitude",
                "issue_type": "out_of_range",
                "message": f"Latitude {lat} is outside valid range {LAT_RANGE}.",
            })
        if not (LON_RANGE[0] <= lon <= LON_RANGE[1]):
            issues.append({
                "row_index": i,
                "column": "longitude",
                "issue_type": "out_of_range",
                "message": f"Longitude {lon} is outside valid range {LON_RANGE}.",
            })

        # Optional bounding box check
        if bounds:
            if not (bounds["min_lat"] <= lat <= bounds["max_lat"] and
                    bounds["min_lon"] <= lon <= bounds["max_lon"]):
                issues.append({
                    "row_index": i,
                    "column": "geometry",
                    "issue_type": "out_of_bounds",
                    "message": (
                        f"Point ({lat}, {lon}) is outside the expected area bounds."
                    ),
                })

    logger.info(f"Spatial validation: {len(issues)} issues across {len(records)} records")
    return {
        "valid": len(issues) == 0,
        "total_records": len(records),
        "issues": issues,
    }
