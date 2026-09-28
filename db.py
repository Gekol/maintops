"""Lakebase connection helper for the Flask app.

The pool lives in maintops_core.db so the app and the Manny agent share one implementation.
Usage: ``with get_connection() as conn: with conn.cursor() as cur: ...``
"""

from maintops_core.db import get_connection  # noqa: F401 — re-exported for app.py
