"""Incident lifecycle services (used by Manny's tools and by the Flask routes).

Every function takes the acting user's id from the server session — never from the LLM — and checks
ownership and the allowed status transition before writing. Violations raise ServiceError with a
message that can be shown to the user. Database CHECK constraints are the last line of defence.

Lifecycle: open → recommended → assigned → in_progress → completed   (cancelled from open/recommended/assigned)
"""

import json

import psycopg

from maintops_core.db import get_connection

SPECIALISATIONS = ["plumbing", "electrical", "heating_hvac", "carpentry", "painting",
                   "roofing", "flooring", "appliance_repair", "locksmith", "general_maintenance"]
URGENCIES = ["low", "medium", "high", "critical"]
ACTIVE_STATUSES = ("assigned", "in_progress")
MAX_ACTIVE_JOBS = 3        # a handyman with this many active jobs is not recommended or assigned


class ServiceError(Exception):
    """A rule prevented the action; the message is safe to show to the user."""


# ─────────────────────────────────────────────────────────────
# Reads
# ─────────────────────────────────────────────────────────────
_INCIDENT_COLS = ("id, description, incident_type, urgency, status, required_skills, handyman_user_id, "
                  "recommended_handyman_ids, distance_km, travel_time_minutes, rating, feedback, "
                  "created_at, assigned_at, completed_at")


def _incident_dict(row) -> dict:
    keys = [c.strip() for c in _INCIDENT_COLS.split(",")]
    d = dict(zip(keys, row))
    for k in ("distance_km", "travel_time_minutes"):
        d[k] = float(d[k]) if d[k] is not None else None
    return d


def get_incident(user_id: int, incident_id: int) -> dict:
    """An incident the user reported or is assigned to (anything else looks like 'not found')."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_INCIDENT_COLS}, reported_by_user_id FROM maintops.incidents "
                    "WHERE id = %s AND (reported_by_user_id = %s OR handyman_user_id = %s)",
                    (incident_id, user_id, user_id))
        row = cur.fetchone()
    if row is None:
        raise ServiceError(f"Incident {incident_id} not found.")
    d = _incident_dict(row[:-1])
    d["is_mine"] = row[-1] == user_id
    return d


def list_client_incidents(client_id: int, limit: int = 10) -> list[dict]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_INCIDENT_COLS} FROM maintops.incidents WHERE reported_by_user_id = %s "
                    "ORDER BY created_at DESC LIMIT %s", (client_id, limit))
        return [_incident_dict(r) for r in cur.fetchall()]


def list_handyman_jobs(handyman_id: int, limit: int = 20) -> list[dict]:
    """Active jobs first (most urgent first), then recent finished ones."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT {_INCIDENT_COLS} FROM maintops.incidents WHERE handyman_user_id = %s
            ORDER BY (status IN ('assigned', 'in_progress')) DESC,
                     array_position(ARRAY['critical', 'high', 'medium', 'low']::varchar[], urgency),
                     created_at DESC
            LIMIT %s""", (handyman_id, limit))
        return [_incident_dict(r) for r in cur.fetchall()]


def get_handyman_reviews(handyman_id: int, limit: int = 10) -> dict:
    """Recent ratings/feedback plus the pipeline's review summary for a handyman."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT rating, feedback, completed_at FROM maintops.incidents
                       WHERE handyman_user_id = %s AND rating IS NOT NULL
                       ORDER BY completed_at DESC LIMIT %s""", (handyman_id, limit))
        reviews = [{"rating": r[0], "feedback": r[1], "completed_at": r[2]} for r in cur.fetchall()]
        cur.execute("""SELECT d.rating_avg, d.rating_count, f.review_summary, f.sentiment_score
                       FROM maintops.handyman_details d
                       LEFT JOIN maintops.handyman_feedback f ON f.handyman_user_id = d.user_id
                       WHERE d.user_id = %s""", (handyman_id,))
        row = cur.fetchone()
    summary = {}
    if row:
        summary = {"rating_avg": float(row[0]), "rating_count": row[1], "review_summary": row[2],
                   "sentiment_score": float(row[3]) if row[3] is not None else None}
    return {"summary": summary, "recent_reviews": reviews}


def user_names(user_ids) -> dict:
    """{user_id: "First Last"} for the given ids."""
    ids = [int(i) for i in user_ids or []]
    if not ids:
        return {}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, first_name || ' ' || last_name FROM maintops.users WHERE id = ANY(%s)", (ids,))
        return dict(cur.fetchall())


# ─────────────────────────────────────────────────────────────
# Writes
# ─────────────────────────────────────────────────────────────


def create_incident(client_id: int, description: str, incident_type: str, urgency: str,
                    required_skills: list[str] | None = None) -> dict:
    """Create an open incident for a client. Returns {"id", "status", "duplicate"}."""
    description = " ".join((description or "").split())
    incident_type = (incident_type or "").strip().lower()
    urgency = (urgency or "").strip().lower()
    skills = [s.strip().lower() for s in (required_skills or []) if s and s.strip()][:10]
    if not 5 <= len(description) <= 2000:
        raise ServiceError("The problem description must be between 5 and 2000 characters.")
    if incident_type not in SPECIALISATIONS:
        raise ServiceError(f"Unknown incident type '{incident_type}'. Use one of: {', '.join(SPECIALISATIONS)}.")
    if urgency not in URGENCIES:
        raise ServiceError(f"Unknown urgency '{urgency}'. Use one of: {', '.join(URGENCIES)}.")

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT is_handyman, is_active FROM maintops.users WHERE id = %s", (client_id,))
        row = cur.fetchone()
        if row is None or not row[1]:
            raise ServiceError("Your account is not active.")
        if row[0]:
            raise ServiceError("Handyman accounts cannot report incidents.")
        try:
            cur.execute("""INSERT INTO maintops.incidents
                             (reported_by_user_id, description, incident_type, urgency, required_skills, status)
                           VALUES (%s, %s, %s, %s, %s, 'open') RETURNING id""",
                        (client_id, description, incident_type, urgency, skills or None))
            incident_id = cur.fetchone()[0]
            conn.commit()
            return {"id": incident_id, "status": "open", "duplicate": False}
        except psycopg.errors.UniqueViolation:
            # incidents_no_duplicate_open_idx: the same problem is already open — reuse it
            conn.rollback()
            cur.execute("""SELECT id, status FROM maintops.incidents
                           WHERE reported_by_user_id = %s AND status IN ('open', 'recommended')
                             AND md5(lower(btrim(description))) = md5(lower(btrim(%s)))""",
                        (client_id, description))
            existing = cur.fetchone()
            return {"id": existing[0], "status": existing[1], "duplicate": True}


def assign_handyman(client_id: int, incident_id: int, handyman_id: int) -> dict:
    """Client picks one of the recommended handymen. Returns the assignment details."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT status, recommended_handyman_ids, agent_reasoning FROM maintops.incidents
                       WHERE id = %s AND reported_by_user_id = %s FOR UPDATE""", (incident_id, client_id))
        row = cur.fetchone()
        if row is None:
            raise ServiceError(f"Incident {incident_id} not found.")
        status, recommended, reasoning = row
        if status != "recommended":
            raise ServiceError(f"Incident {incident_id} is '{status}'; only incidents with recommendations "
                               "can be assigned.")
        if handyman_id not in (recommended or []):
            raise ServiceError("You can only choose one of the handymen recommended for this incident.")

        cur.execute("""SELECT count(*) FROM maintops.incidents
                       WHERE handyman_user_id = %s AND status IN ('assigned', 'in_progress')""", (handyman_id,))
        if cur.fetchone()[0] >= MAX_ACTIVE_JOBS:
            raise ServiceError("This handyman has just become fully booked. Please choose another candidate.")

        travel = next((c for c in (json.loads(reasoning or "{}").get("candidates") or [])
                       if c.get("handyman_id") == handyman_id), {})
        cur.execute("""UPDATE maintops.incidents
                       SET handyman_user_id = %s, status = 'assigned', assigned_at = CURRENT_TIMESTAMP,
                           distance_km = %s, travel_time_minutes = %s
                       WHERE id = %s
                       RETURNING (SELECT first_name || ' ' || last_name FROM maintops.users WHERE id = %s)""",
                    (handyman_id, travel.get("distance_km"), travel.get("travel_minutes"), incident_id, handyman_id))
        name = cur.fetchone()[0]
        conn.commit()
    rank = (recommended.index(handyman_id) + 1) if handyman_id in recommended else None
    return {"incident_id": incident_id, "status": "assigned", "handyman_id": handyman_id,
            "handyman_name": name, "recommendation_rank": rank}


def cancel_incident(client_id: int, incident_id: int) -> dict:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE maintops.incidents SET status = 'cancelled'
                       WHERE id = %s AND reported_by_user_id = %s
                         AND status IN ('open', 'recommended', 'assigned')
                       RETURNING id""", (incident_id, client_id))
        if cur.fetchone() is None:
            cur.execute("SELECT status FROM maintops.incidents WHERE id = %s AND reported_by_user_id = %s",
                        (incident_id, client_id))
            row = cur.fetchone()
            if row is None:
                raise ServiceError(f"Incident {incident_id} not found.")
            raise ServiceError(f"Incident {incident_id} is '{row[0]}' and can no longer be cancelled.")
        conn.commit()
    return {"incident_id": incident_id, "status": "cancelled"}


def submit_feedback(client_id: int, incident_id: int, rating: int, feedback: str | None) -> dict:
    """Rate a completed job once. Updates the handyman's headline rating in the same transaction;
    the live stream then refreshes the full scorecard, sentiment and review summary."""
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ServiceError("The rating must be a whole number from 1 to 5.")
    feedback = " ".join((feedback or "").split()) or None
    if feedback and len(feedback) > 2000:
        raise ServiceError("Feedback can be at most 2000 characters.")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT status, rating, handyman_user_id FROM maintops.incidents
                       WHERE id = %s AND reported_by_user_id = %s FOR UPDATE""", (incident_id, client_id))
        row = cur.fetchone()
        if row is None:
            raise ServiceError(f"Incident {incident_id} not found.")
        status, existing, handyman_id = row
        if status != "completed":
            raise ServiceError("Feedback can only be given once the job is completed.")
        if existing is not None:
            raise ServiceError("You have already rated this job.")
        cur.execute("UPDATE maintops.incidents SET rating = %s, feedback = %s WHERE id = %s",
                    (rating, feedback, incident_id))
        cur.execute("""UPDATE maintops.handyman_details
                       SET rating_avg = round((rating_avg * rating_count + %s) / (rating_count + 1), 2),
                           rating_count = rating_count + 1
                       WHERE user_id = %s""", (rating, handyman_id))
        conn.commit()
    return {"incident_id": incident_id, "rating": rating, "handyman_id": handyman_id}


def update_job_status(handyman_id: int, incident_id: int, new_status: str) -> dict:
    """Handyman moves their job assigned → in_progress → completed."""
    allowed = {"in_progress": ("assigned",), "completed": ("assigned", "in_progress")}
    if new_status not in allowed:
        raise ServiceError("Status can only be changed to 'in_progress' or 'completed'.")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT status FROM maintops.incidents
                       WHERE id = %s AND handyman_user_id = %s FOR UPDATE""", (incident_id, handyman_id))
        row = cur.fetchone()
        if row is None:
            raise ServiceError(f"Job {incident_id} not found among your assigned jobs.")
        if row[0] not in allowed[new_status]:
            raise ServiceError(f"Job {incident_id} is '{row[0]}' and cannot move to '{new_status}'.")
        if new_status == "completed":
            cur.execute("""UPDATE maintops.incidents SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                           WHERE id = %s""", (incident_id,))
            cur.execute("UPDATE maintops.handyman_details SET completed_cases = completed_cases + 1 "
                        "WHERE user_id = %s", (handyman_id,))
        else:
            cur.execute("UPDATE maintops.incidents SET status = 'in_progress' WHERE id = %s", (incident_id,))
        conn.commit()
    return {"incident_id": incident_id, "status": new_status}
