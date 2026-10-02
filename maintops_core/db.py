"""Lakebase connection pool shared by the Flask app and the Manny agent.

Reads LAKEBASE_PG_URL (env var; a Databricks secret on the serving endpoint and on Render).
"""

import os

from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    """Lazily initialise the connection pool on first use."""
    global _pool
    if _pool is None:
        url = os.environ.get("LAKEBASE_PG_URL", "")
        if not url:
            raise RuntimeError("LAKEBASE_PG_URL is not set.")
        _pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=10,
            max_idle=60,                            # Lakebase drops idle connections ("SSL connection has been
            max_lifetime=300,                       # closed unexpectedly"): recycle them before it does
            check=ConnectionPool.check_connection,  # pre-ping on checkout
            open=True,
        )
    return _pool


def get_connection():
    """Return a pooled connection to Lakebase.

    Usage::

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")

    The connection is returned to the pool on exit; commits on success, rolls back on exception.
    """
    return _get_pool().connection()
