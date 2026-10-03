"""find_handymen: deterministic ranking of handymen for an incident (README "Handyman search and recommendation").

  Lakebase filter (specialisation, active, < MAX_ACTIVE_JOBS active jobs, has coordinates)
  → straight-line pre-filter: nearest PREFILTER_SIZE
  → Geoapify Route Matrix for real road distance / travel time (one batched request)
  → weighted score from skills, per-type track record, ratings + review sentiment, workload, travel time
  → top 3, stored on the incident (recommended_handyman_ids, agent_reasoning) and status 'recommended'.

The LLM never scores: it only supplies the classification (type, urgency, required skills).
The track record and review features come from the Spark pipeline (handyman_performance, handyman_feedback).
"""

import json
import math
from datetime import UTC, datetime

from maintops_core import geo
from maintops_core.db import get_connection
from maintops_core.incidents import MAX_ACTIVE_JOBS, ServiceError

PREFILTER_SIZE = 20
PREFILTER_RADIUS_KM = 60        # first try handymen within this radius (index-friendly bounding box)
TOP_N = 3

# Initial product weights (README); urgent incidents give travel time more weight
WEIGHTS = {"skill_match": 0.35, "similar_success": 0.30, "feedback": 0.20, "workload": 0.10, "travel": 0.05}
URGENT_WEIGHTS = {"skill_match": 0.30, "similar_success": 0.25, "feedback": 0.20, "workload": 0.10, "travel": 0.15}

SUCCESS_PRIOR, SUCCESS_PRIOR_WEIGHT = 0.80, 10     # Bayesian shrinkage for few rated jobs
RATING_PRIOR, RATING_PRIOR_WEIGHT = 4.0, 5
EXPERIENCE_FULL_AT = 30                           # jobs of this type for full experience credit
TRAVEL_ZERO_AT_MIN = 60                           # travel score reaches 0 at one hour

_HAVERSINE_SQL = """6371 * 2 * asin(sqrt(
    power(sin(radians(u.latitude - %(lat)s) / 2), 2) +
    cos(radians(%(lat)s)) * cos(radians(u.latitude)) * power(sin(radians(u.longitude - %(lon)s) / 2), 2)))"""

_CANDIDATES_SQL = f"""
SELECT u.id, u.first_name, u.last_name, u.latitude, u.longitude,
       d.skills, d.avg_price, d.has_car, active.cnt,
       pt.jobs_completed, pt.rated_jobs, pt.successful_jobs, pt.success_rate, pt.avg_resolution_hours,
       pa.jobs_completed, pa.rated_jobs, pa.avg_rating,
       f.sentiment_score, f.review_summary,
       {_HAVERSINE_SQL} AS straight_km
FROM maintops.users u
JOIN maintops.handyman_details d ON d.user_id = u.id
CROSS JOIN LATERAL (
    SELECT count(*) AS cnt FROM maintops.incidents i
    WHERE i.handyman_user_id = u.id AND i.status IN ('assigned', 'in_progress')) active
LEFT JOIN maintops.handyman_performance pt ON pt.handyman_user_id = u.id AND pt.incident_type = %(type)s
LEFT JOIN maintops.handyman_performance pa ON pa.handyman_user_id = u.id AND pa.incident_type = 'all'
LEFT JOIN maintops.handyman_feedback f ON f.handyman_user_id = u.id
WHERE u.is_handyman AND u.is_active AND u.id <> %(client)s
  AND u.latitude IS NOT NULL AND u.longitude IS NOT NULL
  AND d.specialisations @> ARRAY[%(type)s]::text[]
  AND active.cnt < {MAX_ACTIVE_JOBS}
  AND (%(box)s IS NULL OR (u.latitude BETWEEN %(lat)s - %(box)s AND %(lat)s + %(box)s
                           AND u.longitude BETWEEN %(lon)s - %(box_lon)s AND %(lon)s + %(box_lon)s))
ORDER BY straight_km
LIMIT {PREFILTER_SIZE}"""


def _skill_score(required: list[str], skills: list[str]) -> tuple[float, list[str]]:
    """Specialisation already matches (0.5); the rest is the share of required skills the handyman has."""
    if not required:
        return 0.75, []
    have = [s.lower() for s in (skills or [])]
    matched = []
    for req in required:
        words = {w for w in req.lower().split() if len(w) > 3}
        if any(req.lower() in h or h in req.lower() or (words and words & set(h.split())) for h in have):
            matched.append(req)
    return 0.5 + 0.5 * len(matched) / len(required), matched


def _client_location(cur, client_id: int) -> tuple[float, float]:
    cur.execute("SELECT latitude, longitude, house, postal_code, city, country "
                "FROM maintops.users WHERE id = %s", (client_id,))
    lat, lon, house, postal, city, country = cur.fetchone()
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    # Address was never geocoded (e.g. registered before geocoding existed): geocode once and store it
    address = ", ".join(p for p in (house, f"{postal or ''} {city or ''}".strip(), country) if p)
    try:
        loc = geo.geocode(address, user_id=client_id)
    except geo.GeoError as exc:
        raise ServiceError("We could not locate your address. Please check it on your profile page.") from exc
    cur.execute("UPDATE maintops.users SET latitude = %s, longitude = %s, state = COALESCE(state, %s), "
                "country = COALESCE(country, %s) WHERE id = %s",
                (loc["lat"], loc["lon"], loc.get("state"), loc.get("country"), client_id))
    return loc["lat"], loc["lon"]


def find_handymen(client_id: int, incident_id: int) -> dict:
    """Rank handymen for the client's incident; returns {"incident_id", "candidates": [...]}."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
                       SELECT status, incident_type, urgency, required_skills FROM maintops.incidents
                       WHERE id = %s AND reported_by_user_id = %s""", (incident_id, client_id))
        row = cur.fetchone()
        if row is None:
            raise ServiceError(f"Incident {incident_id} not found.")
        status, incident_type, urgency, required = row
        if status not in ("open", "recommended"):
            raise ServiceError(f"Incident {incident_id} is '{status}'; recommendations are only for open incidents.")
        if not incident_type:
            raise ServiceError("The incident has no type yet, so no handymen can be matched.")

        lat, lon = _client_location(cur, client_id)
        params = {"lat": lat, "lon": lon, "type": incident_type, "client": client_id,
                  "box": PREFILTER_RADIUS_KM / 111.0,
                  "box_lon": PREFILTER_RADIUS_KM / (111.0 * max(0.2, abs(math.cos(math.radians(lat)))))}
        cur.execute(_CANDIDATES_SQL, params)
        rows = cur.fetchall()
        if len(rows) < TOP_N:                      # few specialists nearby: search everywhere
            cur.execute(_CANDIDATES_SQL, {**params, "box": None, "box_lon": None})
            rows = cur.fetchall()
        if not rows:
            trade = incident_type.replace("_", " ")
            raise ServiceError(f"No available {trade} specialists found right now.")

        # by car if the handyman has one, otherwise by public transport
        travel = geo.travel_times([(float(r[3]), float(r[4]), bool(r[7])) for r in rows], (lat, lon),
                                  user_id=client_id, incident_id=incident_id)
        weights = URGENT_WEIGHTS if urgency in ("high", "critical") else WEIGHTS

        candidates = []
        for r, t in zip(rows, travel, strict=True):
            (hid, first, last, _, _, skills, avg_price, has_car, active,
             t_jobs, t_rated, t_success, t_rate, t_hours, a_jobs, a_rated, a_rating,
             sentiment, summary, _) = r
            skill, matched = _skill_score(required or [], skills)
            t_jobs, t_rated, t_success = t_jobs or 0, t_rated or 0, t_success or 0
            bayes_success = (t_success + SUCCESS_PRIOR * SUCCESS_PRIOR_WEIGHT) / (t_rated + SUCCESS_PRIOR_WEIGHT)
            similar = bayes_success * (0.7 + 0.3 * min(t_jobs / EXPERIENCE_FULL_AT, 1))
            a_rated = a_rated or 0
            rating = ((float(a_rating or 0) * a_rated + RATING_PRIOR * RATING_PRIOR_WEIGHT)
                      / (a_rated + RATING_PRIOR_WEIGHT))
            feedback = 0.7 * (rating - 1) / 4 + 0.3 * ((float(sentiment) + 1) / 2 if sentiment is not None else 0.5)
            workload = 1 - active / MAX_ACTIVE_JOBS
            travel_score = 1 - min(t["travel_minutes"] / TRAVEL_ZERO_AT_MIN, 1)
            factors = {"skill_match": skill, "similar_success": similar, "feedback": feedback,
                       "workload": workload, "travel": travel_score}
            score = sum(weights[k] * v for k, v in factors.items())
            candidates.append({
                "handyman_id": hid,
                "name": f"{first} {last}",
                "match_percent": round(score * 100),
                "success_rate_percent": round(float(t_rate) * 100) if t_rate is not None else None,
                "jobs_of_this_type": t_jobs,
                "avg_resolution_hours": float(t_hours) if t_hours is not None else None,
                "avg_rating": float(a_rating) if a_rating is not None else None,
                "rating_count": a_rated,
                "review_summary": summary,
                "distance_km": t["distance_km"],
                "travel_minutes": t["travel_minutes"],
                "travel_estimated": t["estimated"],
                "travel_mode": t["mode"],                    # drive | transit (no car)
                "avg_hourly_rate_eur": float(avg_price) if avg_price is not None else None,   # per hour
                "active_jobs": active,
                "has_car": has_car,
                "matched_skills": matched,
                "skills_matched": f"{len(matched)} of {len(required)}" if required else None,
                "factors": {k: round(v, 3) for k, v in factors.items()},
            })

        candidates.sort(key=lambda c: c["match_percent"], reverse=True)
        top = candidates[:TOP_N]
        reasoning = {
            "computed_at": datetime.now(UTC).isoformat(),
            "weights": weights,
            "considered": len(candidates),
            "candidates": [{k: c[k] for k in ("handyman_id", "match_percent", "distance_km", "travel_minutes",
                                               "travel_estimated", "travel_mode", "factors")} for c in top],
        }
        cur.execute("""UPDATE maintops.incidents
                       SET recommended_handyman_ids = %s, agent_reasoning = %s, status = 'recommended'
                       WHERE id = %s""", ([c["handyman_id"] for c in top], json.dumps(reasoning), incident_id))
        conn.commit()
    return {"incident_id": incident_id, "incident_type": incident_type, "urgency": urgency,
            "considered": len(candidates), "candidates": top}
