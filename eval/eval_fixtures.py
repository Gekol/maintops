"""Eval fixtures: dedicated test accounts in Lakebase and the database state each scenario starts from.

A fixture set is four accounts (client, other_client, handyman, other_handyman) under the reserved domain
@eval.maintops.test. They have an unusable password hash, so nobody can log in as them, and the handymen have no
specialisations, so they are never recommended to real clients. Scenarios run in parallel, each on its own set
from a pool; a set is reset (all its incidents deleted) before every scenario and once more at the end. The live
stream turns those deletes into fresh scorecards, so nothing the eval writes outlives the run.
"""

import functools
import json
import queue
from contextlib import contextmanager
from datetime import datetime, timedelta

import psycopg

from maintops_core.db import get_connection


def _retry_on_drop(fn):
    """Lakebase occasionally drops a pooled connection mid-query: a read is simply tried once more."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except psycopg.OperationalError:
            return fn(*args, **kwargs)
    return wrapper

DOMAIN = "eval.maintops.test"
ROLES = ("client", "other_client", "handyman", "other_handyman")
NO_LOGIN_HASH = "!eval-account-no-login"


@_retry_on_drop
def ensure_pool(size: int) -> list[dict]:
    """Create the fixture accounts that do not exist yet; returns one {role: user_id} dict per set."""
    with get_connection() as conn, conn.cursor() as cur:
        # A real Berlin address, so find_handymen has real handymen and real routes around the client
        cur.execute("""SELECT house, postal_code, city, state, country, latitude, longitude FROM maintops.users
                       WHERE NOT is_handyman AND city = 'Berlin' AND latitude IS NOT NULL ORDER BY id LIMIT 1""")
        address = cur.fetchone()
        sets = []
        for n in range(size):
            ids = {}
            for role in ROLES:
                email = f"{role.replace('_', '-')}-{n}@{DOMAIN}"
                cur.execute("SELECT id FROM maintops.users WHERE email = %s", (email,))
                row = cur.fetchone()
                if row is None:
                    handyman = role.endswith("handyman")
                    cur.execute("""INSERT INTO maintops.users (email, password_hash, first_name, last_name, house,
                                       postal_code, city, state, country, latitude, longitude, is_handyman)
                                   VALUES (%s, %s, 'Eval', %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                                (email, NO_LOGIN_HASH, f"{role.replace('_', ' ').title()} {n}", *address, handyman))
                    row = cur.fetchone()
                    if handyman:
                        cur.execute("""
                            INSERT INTO maintops.handyman_details (user_id, specialisations, skills, has_car)
                            VALUES (%s, '{}', '{}', true)""", (row[0],))
                ids[role] = row[0]
            sets.append(ids)
        conn.commit()
    return sets


@_retry_on_drop
def reset(fx: dict) -> None:
    """Remove everything a scenario may have written for this fixture set."""
    users = list(fx.values())
    handymen = [fx["handyman"], fx["other_handyman"]]
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""DELETE FROM maintops.incidents
                       WHERE reported_by_user_id = ANY(%s) OR handyman_user_id = ANY(%s)""", (users, users))
        cur.execute("""
            UPDATE maintops.handyman_details SET rating_avg = 0, rating_count = 0, completed_cases = 0
            WHERE user_id = ANY(%s)""", (handymen,))
        for table in ("handyman_performance", "handyman_feedback"):
            cur.execute(f"""
                DELETE FROM maintops.{table}
                WHERE handyman_user_id = ANY(%s)""", (handymen,))
        conn.commit()


class Pool:
    """Thread-safe checkout of fixture sets for parallel scenarios. A set whose request timed out is retired for
    the rest of the run: the abandoned request may still write into it."""

    def __init__(self, sets: list[dict]):
        self.sets = sets
        self._free = queue.Queue()
        self._retired = set()
        for fx in sets:
            self._free.put(fx)

    def retire(self, fx: dict) -> None:
        self._retired.add(id(fx))

    @contextmanager
    def checkout(self):
        fx = self._free.get()
        try:
            reset(fx)
            yield fx
        finally:
            if id(fx) not in self._retired:
                self._free.put(fx)

    def reset_all(self) -> None:
        for fx in self.sets:
            reset(fx)


# ─────────────────────────────────────────────────────────────
# Seeding helpers (each returns the new incident id)
# ─────────────────────────────────────────────────────────────


def add_incident(client: int, description: str, *, status: str = "open", incident_type: str = "plumbing",
                 urgency: str = "medium", handyman: int | None = None, rating: int | None = None,
                 feedback: str | None = None, days_ago: int = 3, required_skills: list[str] | None = None) -> int:
    created = datetime.now() - timedelta(days=days_ago)
    assigned = created + timedelta(hours=2) if status in ("assigned", "in_progress", "completed") else None
    completed = created + timedelta(hours=26) if status == "completed" else None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO maintops.incidents
                         (reported_by_user_id, handyman_user_id, description, incident_type, urgency, status,
                          required_skills, rating, feedback, created_at, assigned_at, completed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (client, handyman, description, incident_type, urgency, status, required_skills, rating,
                     feedback, created, assigned, completed))
        incident_id = cur.fetchone()[0]
        conn.commit()
    return incident_id


def add_performance(handyman: int, rows: list[dict], review_summary: str | None = None) -> list[dict]:
    """Seed the scorecard tables the pipeline normally writes: rows = [{incident_type, rated_jobs,
    successful_jobs, avg_rating}], plus an 'all' row computed from them. Returns the seeded rows (with
    success_rate): if the live stream is running it recomputes this handyman's scorecard from the fixture
    jobs, so after a turn the database may no longer hold the figures Manny was given."""
    total_rated = sum(r["rated_jobs"] for r in rows)
    total_ok = sum(r["successful_jobs"] for r in rows)
    all_row = {"incident_type": "all", "rated_jobs": total_rated, "successful_jobs": total_ok,
               "avg_rating": round(sum(r["avg_rating"] * r["rated_jobs"] for r in rows) / total_rated, 2)}
    with get_connection() as conn, conn.cursor() as cur:
        for r in [*rows, all_row]:
            cur.execute("""INSERT INTO maintops.handyman_performance
                             (handyman_user_id, incident_type, jobs_completed, rated_jobs, successful_jobs,
                              success_rate, avg_rating, avg_resolution_hours, computed_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, 24, now())""",
                        (handyman, r["incident_type"], r["rated_jobs"], r["rated_jobs"], r["successful_jobs"],
                         round(r["successful_jobs"] / r["rated_jobs"], 4), r["avg_rating"]))
        if review_summary:
            cur.execute("""
                           INSERT INTO maintops.handyman_feedback
                             (handyman_user_id, review_count, recent_review_count, positive_share, negative_share,
                              sentiment_score, review_summary, summary_updated_at, computed_at)
                           VALUES (%s, %s, %s, 0.7, 0.2, 0.5, %s, now(), now())""",
                        (handyman, total_rated, min(total_rated, 20), review_summary))
        conn.commit()
    return [{**r, "success_rate": round(r["successful_jobs"] / r["rated_jobs"], 4)} for r in [*rows, all_row]]


def recommend(client: int, description: str, incident_type: str, urgency: str, skills: list[str]) -> dict:
    """An incident with real recommendations, made through the same services Manny uses."""
    from maintops_core import incidents as inc
    from maintops_core import matching
    created = inc.create_incident(client, description, incident_type, urgency, skills)
    out = matching.find_handymen(client, created["id"])
    ids = [c["handyman_id"] for c in out["candidates"]]
    return {"incident": created["id"], "ids": ids, "names": [c["name"] for c in out["candidates"]]}


# ─────────────────────────────────────────────────────────────
# Reading state back
# ─────────────────────────────────────────────────────────────


_INCIDENT_SQL = """SELECT id, reported_by_user_id, handyman_user_id, status, incident_type, urgency, rating, feedback,
                          recommended_handyman_ids, completed_at, description, required_skills,
                          hours_worked::float, amount_paid_eur::float
                   FROM maintops.incidents"""
_INCIDENT_KEYS = ("id", "reported_by", "handyman", "status", "incident_type", "urgency", "rating", "feedback",
                  "recommended", "completed_at", "description", "required_skills", "hours_worked", "amount_paid_eur")


@_retry_on_drop
def incident(incident_id: int) -> dict | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(_INCIDENT_SQL + " WHERE id = %s", (incident_id,))
        row = cur.fetchone()
    return dict(zip(_INCIDENT_KEYS, row, strict=True)) if row else None


@_retry_on_drop
def address_of(user_id: int) -> dict:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT house, postal_code FROM maintops.users WHERE id = %s", (user_id,))
        house, postal_code = cur.fetchone()
    return {"house": house, "postal_code": postal_code}


@_retry_on_drop
def incidents_of(user_id: int) -> list[dict]:
    """All incidents a fixture user reported or works on, oldest first (one query, one connection)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(_INCIDENT_SQL + " WHERE reported_by_user_id = %s OR handyman_user_id = %s ORDER BY id",
                    (user_id, user_id))
        return [dict(zip(_INCIDENT_KEYS, r, strict=True)) for r in cur.fetchall()]


@_retry_on_drop
def recommendation_reasoning(client: int) -> list:
    """find_handymen's stored reasoning (weights, travel data) for the client's incidents."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT agent_reasoning FROM maintops.incidents
                       WHERE reported_by_user_id = %s AND agent_reasoning IS NOT NULL""", (client,))
        return [json.loads(r[0]) for r in cur.fetchall()]


@_retry_on_drop
def tool_calls(request_id: str) -> list[dict]:
    """Manny's tool calls for one request, from app_events (written before the response returns)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT name, success, error, details FROM maintops.app_events
                       WHERE request_id = %s AND event_type = 'tool_call' ORDER BY id""", (request_id,))
        return [{"name": r[0], "success": r[1], "error": r[2], "args": (r[3] or {}).get("args") or {},
                 "needs_confirmation": bool((r[3] or {}).get("needs_confirmation"))} for r in cur.fetchall()]


@_retry_on_drop
def handyman_rating(handyman: int) -> tuple:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT rating_avg, rating_count
            FROM maintops.handyman_details WHERE user_id = %s""", (handyman,))
        return cur.fetchone()


@_retry_on_drop
def cv_truths(n: int) -> list[dict]:
    """Synthetic handymen whose CV text and true profile are both known (the CV was generated from the profile)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
                       SELECT u.first_name, u.last_name, u.email, u.phone, d.specialisations, d.cv_raw_text,
                              u.house, u.postal_code, u.city, u.state, u.country
                       FROM maintops.users u JOIN maintops.handyman_details d ON d.user_id = u.id
                       WHERE d.cv_raw_text IS NOT NULL AND length(d.cv_raw_text) > 200 AND u.email NOT LIKE %s
                       ORDER BY u.id LIMIT %s""", (f"%@{DOMAIN}", n))
        return [{"first_name": r[0], "last_name": r[1], "email": r[2], "phone": r[3],
                 "specialisations": sorted(r[4] or []), "cv_text": r[5],
                 "house": r[6], "postal_code": r[7], "city": r[8], "state": r[9], "country": r[10]}
                for r in cur.fetchall()]
