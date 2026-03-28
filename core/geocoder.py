"""
geocoder.py
───────────
Converts extracted location references into coordinates.

Priority order:
  1. Coordinate strings  → parsed directly
  2. Addresses           → Nominatim (OpenStreetMap, free, no API key)
  3. City/state          → Nominatim (lower confidence)
  4. APN                 → Not implemented (stub)
  5. PLSS                → Not implemented (stub)

Rate limiting: Nominatim requires ≥ 1 second between requests.
"""

import logging
import re
import time

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

_geolocator = Nominatim(user_agent="geo_platform/1.0", timeout=10)
_NOMINATIM_DELAY = 1.1  # seconds — Nominatim usage policy requires 1 req/sec


def geocode_locations(locations: list[dict]) -> list[dict]:
    """
    Add latitude, longitude, and geocoding metadata to each location dict.
    Returns the input list with added fields:
        latitude, longitude, geocode_source, geocode_confidence, geocode_status
    """
    results = []
    for loc in locations:
        enriched = dict(loc)
        loc_type = loc.get("type", "unknown")

        if loc_type == "coordinates":
            enriched.update(_geocode_coordinates(loc["raw_text"]))
        elif loc_type == "address":
            enriched.update(_geocode_nominatim(loc["raw_text"], confidence_base=0.85))
        elif loc_type == "city_state":
            enriched.update(_geocode_nominatim(loc["raw_text"], confidence_base=0.5))
        elif loc_type == "apn":
            enriched.update(_stub_not_implemented("APN county parcel lookup"))
        elif loc_type == "plss":
            enriched.update(_stub_not_implemented("BLM PLSS API"))
        else:
            enriched.update(_failed("unknown location type"))

        results.append(enriched)

    success = sum(1 for r in results if r.get("geocode_status") == "success")
    logger.info(f"Geocoding complete: {success}/{len(results)} succeeded")
    return results


# ── Geocoding strategies ───────────────────────────────────────────────────────

def _geocode_coordinates(raw_text: str) -> dict:
    """Parse coordinate strings directly into lat/lon."""
    result = _parse_decimal_degrees(raw_text) or _parse_dms(raw_text)
    if result:
        lat, lon = result
        return {
            "latitude": lat,
            "longitude": lon,
            "geocode_source": "coordinate_parser",
            "geocode_confidence": 1.0,
            "geocode_status": "success",
        }
    logger.warning(f"Could not parse coordinate string: '{raw_text}'")
    return _failed("coordinate parsing failed")


def _geocode_nominatim(address: str, confidence_base: float) -> dict:
    """Geocode an address string using the Nominatim (OSM) geocoder."""
    try:
        time.sleep(_NOMINATIM_DELAY)
        location = _geolocator.geocode(address)
        if location:
            return {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "geocode_source": "nominatim",
                "geocode_confidence": confidence_base,
                "geocode_status": "success",
            }
        return _failed("no result from Nominatim")
    except GeocoderTimedOut:
        logger.warning(f"Nominatim timed out for: '{address}'")
        return _failed("geocoder timeout")
    except GeocoderServiceError as e:
        logger.warning(f"Nominatim service error for '{address}': {e}")
        return _failed("geocoder service error")


def _stub_not_implemented(method_name: str) -> dict:
    """Placeholder for geocoding methods not yet implemented."""
    logger.info(f"TODO: {method_name} is not yet implemented.")
    return {
        "latitude": None,
        "longitude": None,
        "geocode_source": method_name,
        "geocode_confidence": 0.0,
        "geocode_status": "not_implemented",
    }


def _failed(reason: str) -> dict:
    return {
        "latitude": None,
        "longitude": None,
        "geocode_source": "none",
        "geocode_confidence": 0.0,
        "geocode_status": "failed",
    }


# ── Coordinate parsers ─────────────────────────────────────────────────────────

# Decimal degrees: "32.715736, -117.161087" or "32.715736° N, 117.161087° W"
_DECIMAL_RE = re.compile(
    r'(-?\d{1,3}\.\d+)\s*°?\s*([NS])?\s*[,/\s]\s*(-?\d{1,3}\.\d+)\s*°?\s*([EW])?',
    re.IGNORECASE,
)

# Degrees-minutes-seconds: 32°42'57.4"N 117°9'39.9"W
_DMS_RE = re.compile(
    r'(\d{1,3})°\s*(\d{1,2})[\'′]\s*([\d.]+)[\"″]?\s*([NS])'
    r'\s*[,\s]\s*'
    r'(\d{1,3})°\s*(\d{1,2})[\'′]\s*([\d.]+)[\"″]?\s*([EW])',
    re.IGNORECASE,
)


def _parse_decimal_degrees(text: str) -> tuple[float, float] | None:
    m = _DECIMAL_RE.search(text)
    if not m:
        return None
    lat = float(m.group(1))
    lat_dir = (m.group(2) or "").upper()
    lon = float(m.group(3))
    lon_dir = (m.group(4) or "").upper()

    if lat_dir == "S":
        lat = -abs(lat)
    if lon_dir == "W":
        lon = -abs(lon)

    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _parse_dms(text: str) -> tuple[float, float] | None:
    m = _DMS_RE.search(text)
    if not m:
        return None

    lat = _dms_to_dd(float(m.group(1)), float(m.group(2)), float(m.group(3)))
    if m.group(4).upper() == "S":
        lat = -lat

    lon = _dms_to_dd(float(m.group(5)), float(m.group(6)), float(m.group(7)))
    if m.group(8).upper() == "W":
        lon = -lon

    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _dms_to_dd(degrees: float, minutes: float, seconds: float) -> float:
    return degrees + minutes / 60 + seconds / 3600
