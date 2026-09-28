"""Apply Lakebase schema migrations in sqls/migrations/ (in file-name order, each at most once).

Migrations change table definitions, which only the table owner (your Databricks identity) may do,
so this connects as you with a short-lived OAuth token from the Databricks CLI. Afterwards the app
role from LAKEBASE_PG_URL is granted access to all tables and sequences in the schema.

Usage:
    python sqls/migrate.py            # apply pending migrations
    python sqls/migrate.py --dry-run  # apply inside a transaction and roll back
Reads LAKEBASE_PG_URL from the environment or .env; needs `databricks auth login -p george_sokolovsky`.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg import sql

PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "george_sokolovsky")
ENDPOINT = "projects/george-sokolovsky-capstone/branches/production/endpoints/primary"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
ROOT = Path(__file__).parent.parent


def _env(name: str) -> str:
    """Read a variable from the environment, falling back to the repo's .env file."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit(f"{name} is not set (environment or .env)")


def _run_cli(*args: str) -> dict:
    out = subprocess.run(
        ["databricks", "-p", PROFILE, *args, "-o", "json"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def _owner_conninfo(app_url: str) -> str:
    """Connection string for the same database, authenticated as the current Databricks user."""
    user = _run_cli("current-user", "me")["userName"]
    token = _run_cli("postgres", "generate-database-credential", ENDPOINT)["token"]
    app = urlparse(app_url)
    return psycopg.conninfo.make_conninfo(
        host=app.hostname, port=app.port or 5432, dbname=app.path.lstrip("/"),
        user=user, password=token, sslmode="require",
    )


def main(dry_run: bool) -> None:
    app_url = _env("LAKEBASE_PG_URL")
    app_role = urlparse(app_url).username

    with psycopg.connect(_owner_conninfo(app_url), connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS maintops.schema_migrations ("
                " name text PRIMARY KEY,"
                " applied_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
            cur.execute("SELECT name FROM maintops.schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

            pending = [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in applied]
            for path in pending:
                print(f"applying {path.name}")
                cur.execute(path.read_text())
                cur.execute("INSERT INTO maintops.schema_migrations (name) VALUES (%s)", (path.name,))

            # The app role reads and writes rows but never changes the schema
            role = sql.Identifier(app_role)
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA maintops TO {}").format(role))
            cur.execute(sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA maintops TO {}"
            ).format(role))
            # UPDATE lets the synthetic-data loader move sequences past the loaded ids (setval)
            cur.execute(sql.SQL(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA maintops TO {}"
            ).format(role))

        if dry_run:
            conn.rollback()
            print(f"dry run: {len(pending)} migration(s) applied and rolled back")
        else:
            conn.commit()
            print(f"done: {len(pending)} migration(s) applied, grants refreshed for role {app_role!r}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
