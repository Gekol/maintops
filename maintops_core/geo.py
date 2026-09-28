"""Geoapify integration: forward geocoding and the Route Matrix API.

- API key from GEOAPIFY_API_KEY (env var / Databricks secret), never in code.
- Retries with exponential backoff on 429 and 5xx (honours Retry-After), 10–20 s timeouts.
- Responses are validated (shape, coordinate ranges, geocoding confidence) before use.
- Every call is logged to app_events (api_call) with latency and outcome.
- If routing fails, callers get a straight-line estimate explicitly flagged estimated=True —
  never presented as real road data.
"""

import math
import os
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from maintops_core.events import log_event

GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
MATRIX_URL = "https://api.geoapify.com/v1/routematrix"
MIN_CONFIDENCE = 0.5          # below this a geocoding result is treated as "not found"
ROAD_FACTOR = 1.3             # straight-line → road distance, for the flagged fallback estimate
FALLBACK_SPEED_KMH = 30       # urban driving speed for the fallback estimate
MAX_MATRIX_SOURCES = 50

_session: requests.Session | None = None


class GeoError(Exception):
    """Geoapify could not produce a valid answer."""


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"), respect_retry_after_header=True)
        _session = requests.Session()
        _session.mount("https://", HTTPAdapter(max_retries=retry))
    return _session


def _api_key() -> str:
    key = os.environ.get("GEOAPIFY_API_KEY", "")
    if not key:
        raise GeoError("GEOAPIFY_API_KEY is not set")
    return key


def _valid_coords(lat, lon) -> bool:
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) \
        and -90 <= lat <= 90 and -180 <= lon <= 180


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def geocode(address: str, *, user_id: int | None = None) -> dict:
    """Address → {"lat", "lon", "formatted", "confidence"}. Raises GeoError if not found or invalid."""
    address = (address or "").strip()
    if not address:
        raise GeoError("empty address")
    started = time.time()
    error = None
    try:
        resp = _get_session().get(GEOCODE_URL, timeout=10, params={
            "text": address, "format": "json", "limit": 1, "apiKey": _api_key()})
        if resp.status_code != 200:
            raise GeoError(f"geocoding HTTP {resp.status_code}")
        results = (resp.json() or {}).get("results") or []
        if not results:
            raise GeoError("address not found")
        top = results[0]
        lat, lon = top.get("lat"), top.get("lon")
        confidence = float((top.get("rank") or {}).get("confidence") or 0)
        if not _valid_coords(lat, lon):
            raise GeoError("malformed coordinates in geocoding response")
        if confidence < MIN_CONFIDENCE:
            raise GeoError(f"address match too uncertain (confidence {confidence:.2f})")
        return {"lat": lat, "lon": lon, "formatted": top.get("formatted"), "confidence": confidence}
    except (requests.RequestException, ValueError) as exc:
        error = f"geocoding request failed: {exc}"
        raise GeoError(error) from exc
    except GeoError as exc:
        error = str(exc)
        raise
    finally:
        log_event("api_call", "geoapify_geocode", error is None, user_id=user_id, error=error,
                  latency_ms=int((time.time() - started) * 1000))


def route_matrix(sources: list[tuple[float, float]], target: tuple[float, float], *,
                 user_id: int | None = None, incident_id: int | None = None) -> list[dict]:
    """Road distance/time from each source (lat, lon) to the target (lat, lon), in one request.

    Returns [{"distance_km", "travel_minutes", "estimated"}] aligned with sources. If the API fails
    or omits a pair, that entry falls back to a straight-line estimate with estimated=True.
    """
    if not sources:
        return []
    if len(sources) > MAX_MATRIX_SOURCES:
        raise ValueError(f"at most {MAX_MATRIX_SOURCES} sources per request")

    def estimate(src):
        km = haversine_km(src[0], src[1], target[0], target[1]) * ROAD_FACTOR
        return {"distance_km": round(km, 2), "travel_minutes": round(km / FALLBACK_SPEED_KMH * 60, 1),
                "estimated": True}

    started = time.time()
    error = None
    results = [None] * len(sources)
    try:
        body = {"mode": "drive",
                "sources": [{"location": [lon, lat]} for lat, lon in sources],   # Geoapify wants [lon, lat]
                "targets": [{"location": [target[1], target[0]]}]}
        resp = _get_session().post(MATRIX_URL, params={"apiKey": _api_key()}, json=body, timeout=20)
        if resp.status_code != 200:
            raise GeoError(f"route matrix HTTP {resp.status_code}")
        rows = (resp.json() or {}).get("sources_to_targets")
        if not isinstance(rows, list) or len(rows) != len(sources):
            raise GeoError("malformed route matrix response")
        for row in rows:
            cell = row[0] if isinstance(row, list) and row else None
            if not isinstance(cell, dict):
                continue
            i, dist, secs = cell.get("source_index"), cell.get("distance"), cell.get("time")
            if isinstance(i, int) and 0 <= i < len(sources) and isinstance(dist, (int, float)) \
                    and isinstance(secs, (int, float)) and dist >= 0 and secs >= 0:
                results[i] = {"distance_km": round(dist / 1000, 2), "travel_minutes": round(secs / 60, 1),
                              "estimated": False}
    except (requests.RequestException, ValueError, GeoError) as exc:
        error = str(exc)
    finally:
        missing = sum(r is None for r in results)
        log_event("api_call", "geoapify_route_matrix", error is None and missing == 0, user_id=user_id,
                  incident_id=incident_id, error=error or (f"{missing} pairs missing" if missing else None),
                  latency_ms=int((time.time() - started) * 1000), details={"sources": len(sources)})
    return [r if r is not None else estimate(src) for r, src in zip(results, sources)]
