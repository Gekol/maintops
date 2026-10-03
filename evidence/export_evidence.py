"""Export the measurements behind the README into evidence/*.md, straight from the live systems.

Every number in the README's "Evidence" section comes from one of these files, and every file comes from a query
below, so anyone with access to the workspace can regenerate and compare them:

    DATABRICKS_CONFIG_PROFILE=<profile> python evidence/export_evidence.py

Reads Delta tables through the SQL warehouse (DATABRICKS_WAREHOUSE_ID), Lakebase through LAKEBASE_PG_URL (.env), and
the release-gate reports that maintops_manny_deploy writes next to the deployed bundle.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

HERE = Path(__file__).parent
ROOT = HERE.parent
SCHEMA = "bootcamp_students.maintops"
GATE_REPORTS = "/Workspace/Users/{user}/.bundle/maintops/dev/files/eval/results"


def _env(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit(f"{name} is not set (environment or .env)")


w = WorkspaceClient()
WAREHOUSE = _env("DATABRICKS_WAREHOUSE_ID")


def delta(sql: str) -> tuple[list[str], list[list]]:
    """Run SQL on the warehouse; returns (columns, rows)."""
    r = w.statement_execution.execute_statement(statement=sql, warehouse_id=WAREHOUSE, wait_timeout="50s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{r.status.state}: {r.status.error} — {sql[:200]}")
    cols = [c.name for c in r.manifest.schema.columns]
    return cols, (r.result.data_array or []) if r.result else []


def lakebase(sql: str) -> tuple[list[str], list[list]]:
    with psycopg.connect(_env("LAKEBASE_PG_URL"), connect_timeout=30) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [d.name for d in cur.description], [list(r) for r in cur.fetchall()]


def table(cols: list[str], rows: list[list]) -> str:
    def cell(v):
        if v is None:
            return "–"
        if isinstance(v, float):
            return f"{v:,.2f}"
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit() and len(v) > 3):
            return f"{int(v):,}"
        return str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def write(name: str, title: str, intro: str, sections: list[tuple[str, str, tuple]]) -> None:
    """sections: (heading, source description, (cols, rows))."""
    body = [f"# {title}", "", intro, "", f"_Exported {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by "
            f"`evidence/export_evidence.py`._", ""]
    for heading, source, (cols, rows) in sections:
        body += [f"## {heading}", "", f"Source: {source}", "", table(cols, rows), ""]
    (HERE / name).write_text("\n".join(body))
    print("wrote", name)


# ─────────────────────────────────────────────────────────────
# Volume and the batch pipeline
# ─────────────────────────────────────────────────────────────
write("pipeline.md", "Spark pipeline and data volume",
      "The 1M-incident history through the batch pipeline (job `maintops_pipeline`), its data-quality checks and "
      "the defects it quarantined.", [
          ("Row counts", "Delta tables in `bootcamp_students.maintops`", delta(f"""
              SELECT 'synth_incidents (source history)' AS `table`, count(*) AS `rows` FROM {SCHEMA}.synth_incidents
              UNION ALL SELECT 'bronze_incidents (raw export + injected defects)', count(*) FROM {SCHEMA}.bronze_incidents
              UNION ALL SELECT 'silver_incidents (validated, deduplicated)', count(*) FROM {SCHEMA}.silver_incidents
              UNION ALL SELECT 'quarantine_incidents (rejected, with reasons)', count(*) FROM {SCHEMA}.quarantine_incidents
              UNION ALL SELECT 'gold_handyman_performance (scorecards)', count(*) FROM {SCHEMA}.gold_handyman_performance
              UNION ALL SELECT 'gold_handyman_feedback (sentiment + summaries)', count(*) FROM {SCHEMA}.gold_handyman_feedback
              UNION ALL SELECT 'synth_users (clients + handymen)', count(*) FROM {SCHEMA}.synth_users
              UNION ALL SELECT 'cv_parsed (CV PDFs read by ai_parse_document)', count(*) FROM {SCHEMA}.cv_parsed""")),
          ("Latest run of every step", "`pipeline_runs` (each step logs its counts and checks; a failed check "
           "stops the job)", delta(f"""
              SELECT step, date_format(started_at, 'yyyy-MM-dd HH:mm') AS started_utc, round(duration_s) AS seconds,
                     rows_in, rows_out, rows_rejected, status, checks
              FROM (SELECT *, row_number() OVER (PARTITION BY step ORDER BY started_at DESC) AS rn
                    FROM {SCHEMA}.pipeline_runs WHERE step NOT LIKE '2%')
              WHERE rn = 1 ORDER BY step""")),
          ("Why records were quarantined", "`quarantine_incidents.reasons` (one record can have several)",
           delta(f"""SELECT reason, count(*) AS records FROM
                     (SELECT explode(reasons) AS reason FROM {SCHEMA}.quarantine_incidents)
                     GROUP BY reason ORDER BY records DESC""")),
          ("Hourly rate per job type (from billed jobs)", "`silver_incidents`: amount paid ÷ hours worked",
           delta(f"""SELECT incident_type, count(*) AS completed_jobs, round(sum(hours_worked), 0) AS hours,
                            round(sum(amount_paid_eur) / sum(hours_worked), 2) AS eur_per_hour
                     FROM {SCHEMA}.silver_incidents WHERE hours_worked IS NOT NULL
                     GROUP BY incident_type ORDER BY completed_jobs DESC""")),
      ])

# ─────────────────────────────────────────────────────────────
# Velocity: the live stream
# ─────────────────────────────────────────────────────────────
write("latency.md", "Live stream latency (Velocity)",
      "Time from a Postgres commit in Lakebase to the affected handymen's scorecards being back in Lakebase, "
      "measured by `pipeline/20_live_stream` for every micro-batch (CDF → stream → recompute → upsert).", [
          ("Burst tests (`pipeline/latency_test.py`: N reviews committed in one transaction)",
           "`latency_metrics`, batches of 900–1,100 changes (the first two were measured before the stream was "
           "tuned on 28 September; the third is the tuned stream).", delta(f"""
              SELECT date_format(processed_at, 'yyyy-MM-dd HH:mm') AS processed_utc, n_changes, n_handymen,
                     round(p50_s, 1) AS p50_s, round(p95_s, 1) AS p95_s, round(max_s, 1) AS max_s
              FROM {SCHEMA}.latency_metrics WHERE n_changes BETWEEN 900 AND 1100 ORDER BY processed_at""")),
          ("Normal operation (batches under 200 changes)", "`latency_metrics` (larger batches are catch-ups "
           "after the stream was stopped: their age is waiting time, not processing time)", delta(f"""
              SELECT count(*) AS batches, sum(n_changes) AS changes,
                     round(percentile_approx(p50_s, 0.5), 1) AS median_p50_s,
                     round(percentile_approx(p95_s, 0.5), 1) AS median_p95_s,
                     round(avg(CASE WHEN max_s < 60 THEN 1 ELSE 0 END) * 100, 1) AS pct_batches_all_under_60s
              FROM {SCHEMA}.latency_metrics WHERE n_changes < 200""")),
      ])

# ─────────────────────────────────────────────────────────────
# Variety: unstructured data
# ─────────────────────────────────────────────────────────────
write("variety.md", "Unstructured data (Variety)",
      "CV PDFs parsed with `ai_parse_document`, the FAQ PDF chunked and embedded for vector search, and review "
      "texts scored and summarised with AI functions.", [
          ("CV PDFs", "`cv_parsed`", delta(f"""
              SELECT count(*) AS pdfs, sum(CASE WHEN parsed_text IS NOT NULL AND length(parsed_text) > 200 THEN 1 ELSE 0 END)
                     AS parsed_ok, round(avg(length(parsed_text))) AS avg_chars FROM {SCHEMA}.cv_parsed""")),
          ("FAQ retrieval quality", "`rag_eval_runs` (hit@3 on a fixed question set, latest run)", delta(f"""
              SELECT date_format(run_at, 'yyyy-MM-dd') AS run_on, index_name, n_questions, k, hits, hit_rate
              FROM {SCHEMA}.rag_eval_runs ORDER BY run_at DESC LIMIT 1""")),
          ("Review sentiment", "`review_sentiment` (`ai_analyze_sentiment`, one row per distinct review text)",
           delta(f"""SELECT sentiment, count(*) AS distinct_texts FROM {SCHEMA}.review_sentiment
                     GROUP BY sentiment ORDER BY distinct_texts DESC""")),
          ("Review summaries", "`gold_handyman_feedback` (`ai_query`, at most 25 words each)", delta(f"""
              SELECT count(*) AS handymen, sum(CASE WHEN review_summary IS NOT NULL THEN 1 ELSE 0 END) AS with_summary
              FROM {SCHEMA}.gold_handyman_feedback""")),
      ])

# ─────────────────────────────────────────────────────────────
# Analytics (Declarative Pipeline over Lakebase CDF)
# ─────────────────────────────────────────────────────────────
write("analytics.md", "Analytics pipeline output",
      "Tables of the Lakeflow Declarative Pipeline `maintops_analytics` (refreshed every 30 minutes from Lakebase "
      "CDF). Most traffic comes from the release-gate evaluations, which run real conversations on test accounts.", [
          ("Tool usage and success", "`analytics_tool_usage`", delta(f"""
              SELECT name AS tool, calls, round(success_rate * 100, 1) AS success_pct, avg_latency_ms
              FROM {SCHEMA}.analytics_tool_usage ORDER BY calls DESC""")),
          ("Write actions", "`analytics_write_actions`", delta(f"""
              SELECT name AS action, channel, sum(actions) AS actions, sum(succeeded) AS succeeded,
                     count(DISTINCT user_id) AS users, min(day) AS first_day, max(day) AS last_day
              FROM {SCHEMA}.analytics_write_actions GROUP BY name, channel ORDER BY actions DESC""")),
          ("Third-party API (Geoapify)", "`analytics_api_usage_daily`", delta(f"""
              SELECT name AS api_call, sum(calls) AS calls, sum(failures) AS failures,
                     round(100.0 * sum(failures) / sum(calls), 2) AS failure_pct, max(p95_latency_ms) AS worst_daily_p95_ms
              FROM {SCHEMA}.analytics_api_usage_daily GROUP BY name ORDER BY calls DESC""")),
          ("Guardrail decisions", "`analytics_guardrails_daily`", delta(f"""
              SELECT name AS guardrail, sum(events) AS events FROM {SCHEMA}.analytics_guardrails_daily
              GROUP BY name ORDER BY events DESC""")),
          ("Agent requests by role", "`analytics_agent_requests_hourly` (summed over all hours)", delta(f"""
              SELECT role, sum(requests) AS requests, sum(input_tokens) AS input_tokens,
                     sum(output_tokens) AS output_tokens, round(sum(estimated_cost_usd), 2) AS estimated_cost_usd
              FROM {SCHEMA}.analytics_agent_requests_hourly GROUP BY role ORDER BY requests DESC""")),
          ("Which recommendation clients choose", "`analytics_recommendation_rank`", delta(f"""
              SELECT * FROM {SCHEMA}.analytics_recommendation_rank ORDER BY 1""")),
          ("Billing per month (latest)", "`analytics_billing_monthly`", delta(f"""
              SELECT * FROM {SCHEMA}.analytics_billing_monthly ORDER BY month DESC, jobs DESC LIMIT 10""")),
      ])

# ─────────────────────────────────────────────────────────────
# Lakebase: the operational model
# ─────────────────────────────────────────────────────────────
write("lakebase.md", "Lakebase data model",
      "The operational Postgres schema `maintops` as it is deployed: rows, keys, constraints, indexes and triggers.", [
          ("Tables", "`pg_catalog` (row counts are exact)", lakebase("""
              SELECT c.relname AS table_name,
                     (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM maintops.%I', c.relname),
                      false, true, '')))[1]::text::bigint AS rows,
                     (SELECT count(*) FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'p') AS pk,
                     (SELECT count(*) FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'f') AS fk,
                     (SELECT count(*) FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'c') AS checks,
                     (SELECT count(*) FROM pg_index i WHERE i.indrelid = c.oid) AS indexes,
                     (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal) AS triggers
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'maintops' AND c.relkind = 'r' ORDER BY c.relname""")),
          ("CHECK constraints", "`pg_constraint`", lakebase("""
              SELECT c.relname AS table_name, k.conname AS constraint_name,
                     left(pg_get_constraintdef(k.oid), 140) AS definition
              FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'maintops' AND k.contype = 'c' ORDER BY 1, 2""")),
          ("Incidents by status", "`maintops.incidents`", lakebase("""
              SELECT status, count(*) AS incidents, count(rating) AS rated, count(hours_worked) AS billed
              FROM maintops.incidents GROUP BY status ORDER BY incidents DESC""")),
      ])

# ─────────────────────────────────────────────────────────────
# The agent: release-gate history and live performance
# ─────────────────────────────────────────────────────────────
user = w.current_user.me().user_name
gate_rows = []
for f in sorted(w.workspace.list(GATE_REPORTS.format(user=user)), key=lambda o: o.path):
    try:
        rep = json.loads(w.workspace.download(f.path).read())
    except Exception:  # noqa: BLE001 — skip unreadable files
        continue
    if not str(rep.get("target", "")).startswith("endpoint:maintops-manny-staging"):
        continue                                      # gate runs only (local development runs are not evidence)
    gate_rows.append([rep["run_at"][:16].replace("T", " "), rep["scenarios"], f"{rep['passed_runs']}/{rep['runs']}",
                      rep["checks_total"], rep["checks_failed"], "PASS" if rep["passed"] else "FAIL",
                      "; ".join(f"{x['scenario']}: {x['check']}" for x in rep.get("failures", []))[:160] or "–",
                      rep.get("mlflow_run_id", "")])
gate = (["run (UTC)", "scenarios", "runs passed", "checks", "failed", "gate", "what failed", "MLflow run"], gate_rows)

write("agent.md", "Manny: release gate and live performance",
      "Every Manny version is evaluated on a temporary staging endpoint (63 multi-turn scenarios × 3 runs, every "
      "check in code) and promoted to production only if no check fails.", [
          ("Release-gate runs", "reports of job `maintops_manny_deploy` (`eval/results/` in the deployed bundle; "
           "details and transcripts in MLflow experiment `maintops_manny_eval`)", gate),
          ("Tool calls (last 7 days)", "`maintops.app_events` (every tool call is logged with arguments and timing)",
           lakebase("""
              SELECT name AS tool, count(*) AS calls, round(100.0 * avg(success::int), 1) AS success_pct,
                     percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)::int AS median_ms
              FROM maintops.app_events WHERE event_type = 'tool_call' AND created_at > now() - interval '7 days'
              GROUP BY name ORDER BY calls DESC""")),
          ("Why write tools were refused (last 7 days)", "`maintops.app_events`. Almost all of these come from the "
           "evaluation's test accounts deliberately asking for forbidden actions; ids are replaced by N",
           lakebase("""
              SELECT name AS tool, regexp_replace(error, '[0-9]+', 'N', 'g') AS rule_that_refused, count(*) AS calls,
                     count(*) FILTER (WHERE user_id IN (SELECT id FROM maintops.users
                                                         WHERE email LIKE '%@eval.maintops.test')) AS from_eval_accounts
              FROM maintops.app_events
              WHERE event_type = 'tool_call' AND NOT success AND created_at > now() - interval '7 days'
                AND name IN ('create_incident', 'assign_handyman', 'cancel_incident', 'submit_feedback',
                             'update_job_status')
              GROUP BY 1, 2 ORDER BY calls DESC LIMIT 20""")),
      ])
print("done")
