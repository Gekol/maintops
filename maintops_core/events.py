"""Write application events to maintops.app_events (agent requests, tool calls, API calls, UI actions,
guardrail decisions). The table reaches Delta through Lakebase CDF and feeds the analytics pipeline.

Logging must never break the request it describes, so failures are swallowed.
"""

import json
import logging

import psycopg

from maintops_core.db import get_connection

log = logging.getLogger(__name__)

EVENT_TYPES = {"agent_request", "tool_call", "api_call", "ui_action", "guardrail"}


def log_event(event_type: str, name: str, success: bool, *, user_id: int | None = None,
              incident_id: int | None = None, request_id: str | None = None, error: str | None = None,
              latency_ms: int | None = None, input_tokens: int | None = None,
              output_tokens: int | None = None, details: dict | None = None) -> None:
    """Insert one row into app_events; never raises."""
    if event_type not in EVENT_TYPES:
        log.warning("unknown event type %s", event_type)
        return
    def insert(uid, iid, extra):
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO maintops.app_events (event_type, name, request_id, user_id, incident_id, success, "
                "error, latency_ms, input_tokens, output_tokens, details) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (event_type, name[:64], request_id, uid, iid, success,
                 (error or None) and error[:2000], latency_ms, input_tokens, output_tokens,
                 json.dumps(extra, default=str) if extra else None),
            )
            conn.commit()

    try:
        try:
            insert(user_id, incident_id, details)
        except psycopg.errors.ForeignKeyViolation:
            # e.g. a failed action on an incident that does not exist: keep the event, move the ids to details
            insert(None, None, {**(details or {}), "user_id": user_id, "incident_id": incident_id})
    except Exception as exc:  # noqa: BLE001 — logging must not fail the caller
        log.warning("could not log event %s/%s: %s", event_type, name, exc)
