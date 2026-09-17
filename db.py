"""Lakebase connection helper.

Reads a single LAKEBASE_PG_URL env var (set via app.yaml secrets)
and provides a pooled get_connection() context manager for
request-scoped use.
"""

import os

from psycopg_pool import ConnectionPool

_PG_URL: str = os.environ.get("LAKEBASE_PG_URL", "")

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    """Lazily initialise the connection pool on first use."""
    global _pool
    if _pool is None:
        if not _PG_URL:
            raise RuntimeError(
                "LAKEBASE_PG_URL is not set. "
                "Configure it in the Databricks App secrets."
            )
        _pool = ConnectionPool(
            conninfo=_PG_URL,
            min_size=2,       # keep 2 warm connections ready
            max_size=10,      # burst up to 10 under load
            max_idle=600,     # close idle connections after 10 min
            check=ConnectionPool.check_connection,  # pre-ping on checkout
        )
    return _pool


def get_connection():
    """Return a pooled connection to Lakebase.

    Usage::

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
                rows = cur.fetchall()

    The connection is returned to the pool on exit.
    Commits on success, rolls back on exception.
    """
    return _get_pool().connection()
