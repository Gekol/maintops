# MaintOps

Databricks capstone: AI handyman matching + incident management. Flask app on Lakebase (Postgres), analytics on Unity Catalog/Delta, AI via Databricks serving endpoints. **README.md is the full spec** (setup, design, 19 implementation rules) — read its Design section before building a new feature.

## Capstone grading rubric

The project is graded against the rubric below; weigh design and implementation choices by the points they earn. Key consequences:
- Only demonstrated functionality counts (code, deployment, screenshots, demo, docs) — plan evidence for every feature.
- The agent must perform real Lakebase **write** actions, or the whole score is capped at 60/100.
- Analytics credit requires demonstrated Lakebase Change Data Feed or Delta Live Tables.
- Big Data needs two Vs with measurable evidence (planned: Volume + Variety).
- Mocked or fabricated API data is penalised.

@CAPSTONE_REQUIREMENTS.md

## Stack and layout
- `app.py` — Flask app (routes, Flask-Login auth, CV parsing, `/api/manny` proxy to the agent endpoint, `/api/incidents/<id>/assign`). Business logic lives in `maintops_core/`.
- `maintops_core/db.py` — `get_connection` (one shared `psycopg_pool` pool from `LAKEBASE_PG_URL`; pre-ping, `max_idle` 60 s, `max_lifetime` 300 s because Lakebase drops idle connections with "SSL connection has been closed unexpectedly").
- `templates/` (Jinja, `base.html` holds the navbar + Manny floating chat widget) and `static/css/styles.css` (single stylesheet).
- `sqls/` — Lakebase DDL (base tables) + `sqls/migrations/*.sql` applied on top by `sqls/migrate.py` (connects as the table owner via a CLI OAuth token; `--dry-run` rolls back). Base DDL + migrations = the schema.
- `maintops_core/` — services shared by Flask and the Manny agent: `incidents` (lifecycle with ownership/transition checks, `ServiceError` = user-safe message), `matching.find_handymen` (Lakebase filter → Haversine top 20 → `rank_candidates`: score without travel, then Geoapify travel times by `has_car` (car: Route Matrix; no car: Routing API `approximated_transit`) fetched in batches of 6 only for candidates whose best possible score can still reach the top 3 (exactly the same top 3, see the 200 randomised tests) → top 3), `geo` (Geoapify geocode + route matrix + transit routing, retries, validation, flagged fallback), `rag.search_faq`, `events.log_event` (→ `app_events`), `grounding` (Manny's runtime check that figures stated for a handyman are that handyman's own; the eval keeps its own independent implementation in `eval/eval_checks.py`), `db`.
- `agent/manny.py` — Manny: MLflow `ResponsesAgent`, the single agent for all AI (roles visitor/client/handyman via server-set `custom_inputs`, tasks chat/extract_cv), role-gated tools, guardrails, tracing. `agent/deploy_manny.ipynb` logs it → UC model `bootcamp_students.maintops.manny` → temporary endpoint `maintops-manny-staging` → eval gate → `agents.deploy` endpoint `maintops-manny` (only on PASS; old versions pruned, alias `production`) (inference table `bootcamp_students.maintops.manny_payload`; AI Gateway usage tracking and rate limits are NOT supported for agent endpoints in this workspace → rate limit lives in `/api/manny`, usage/cost in `app_events`). Redeploy takes ~12 min; new version gets 100 % traffic. Local dev env: `.venv-agent` (MLflow etc., never in requirements.txt).
- `pipeline/` — Spark pipeline, all logic in `pipeline_lib.py`: batch `10_export_raw` → `11_bronze` (Auto Loader) → `12_silver` (+ `quarantine_incidents`) → `13_gold_performance` (incl. `hourly_rate` from billed jobs) / `14_gold_feedback` (ai_analyze_sentiment + ai_query summaries) → `15_sync_lakebase`; `16_parse_cvs` (ai_parse_document, 10k PDFs); `20_live_stream` (Lakebase CDF `lb_incidents_history` → silver → affected handymen's scorecards → Lakebase, < 1 min; summaries via a sweep); `latency_test.py` (burst test). Run log: `pipeline_runs`; latency: `latency_metrics`.
- `eval/` — the agent's release gate: `manny_eval.py` runs 63 multi-turn scenarios (`eval_scenarios.py`) ×3 on dedicated Lakebase test accounts (`eval_fixtures.py`, `@eval.maintops.test`, reset per scenario and deleted at the end) and checks every turn in code (`eval_checks.py`: DB state, tool calls + args from `app_events`, grounded figures, skill claims, confirmation before writes). Any failed check = FAIL. Backends: endpoint (default), `--local` (agent/manny.py in-process), `--model-uri`. Locally: `DATABRICKS_CONFIG_PROFILE=george_sokolovsky .venv-agent/bin/python eval/manny_eval.py [--only regex] [--repeats N]`. Scenarios that assign real handymen run one at a time (`exclusive`). The endpoint backend posts each turn exactly once with plain HTTP (the SDK client resends after some errors, which made a turn act twice); the deploy notebook waits until the staging endpoint answers before the eval starts. Stop `maintops_live` before a gate run: the stream recomputes the fixture handymen's scorecards from their test jobs and can overwrite the scorecards a scenario seeded (figures seeded by setup still count as grounded, but which job type is weakest could change mid-scenario).
- `databricks.yml` — Asset Bundle (`databricks bundle deploy|run <job> -p george_sokolovsky`): jobs `maintops_rag` (01→02→03→05), `maintops_synth` (data_synthesis 01→08, 07 = load into Lakebase, 08 = billing backfill; run one task with `--only <task_key>`), `maintops_pipeline` (batch 10–16; `--params force=true` rebuilds raw → silver from scratch), `maintops_live` (stream 20; run with `--params max_minutes=N`, 0 = until cancelled), `maintops_manny_deploy`, `maintops_vs_keepalive` (every 4 h).
- `rag/` — FAQ retrieval (RAG) pipeline: `01_setup_schema` → `02_parse_faq` → `03_chunk_faq` (index `faq_index` on our own endpoint `maintops_vs`) → `05_eval_retrieval` (hit@3 → `rag_eval_runs`); `06_keep_vs_alive` (the workspace deletes personal VS endpoints after 6 idle hours; it queries the index and rebuilds via `maintops_rag` if gone).
- `data_synthesis/` — synthetic data (100k clients, 10k handymen + PDF CVs, 1M incidents) as `synth_*` Delta tables; all config in `00_config`.
- Deploy: `render.yaml` (Render, auto-deploys on push to `main`). No Databricks Apps deployment (workspace owner's request, cost).
- Two requirement files on purpose: `requirements.txt` = deployed web app only; `requirements-notebooks.txt` = local notebook runs via Databricks Connect. Don't merge them or add notebook deps to the app file.

## Schema (Postgres schema `maintops`)
- `users` — all accounts; `is_handyman` distinguishes clients from handymen; `is_active`; address + `latitude`/`longitude`.
- `handyman_details` — PK/FK `user_id` → `users.id`; `specialisations TEXT[]`, `skills TEXT[]`, `experience_summary`, `cv_path`, `cv_raw_text`, `has_car`, `completed_cases`, `rating_avg`, `rating_count`, `avg_price` (average HOURLY rate in EUR, never a job price; shown as €/h; = `handyman_performance.hourly_rate` of the 'all' row, i.e. amount paid / hours worked over the latest 50 billed jobs, written by the batch sync and the live stream; unchanged for a handyman with no billed jobs).
- `incidents` — `reported_by_user_id`, `handyman_user_id` (both → `users.id`), `urgency` ∈ low/medium/high/critical, `status` ∈ open/recommended/assigned/in_progress/completed/cancelled, `rating` 1–5, `required_skills text[]`, `hours_worked` + `amount_paid_eur` (migration 005: set when the handyman completes the job, via the dashboard form or Manny's `update_job_status`; validated in `incidents.validate_billing`: 0.25–24 h, ≤ €10,000, €10–300/h); `agent_reasoning` holds find_handymen's JSON (weights, top-3 with travel data).
- `app_events` — agent_request / tool_call / api_call / ui_action / guardrail log (→ CDF → analytics).
- `incident_trips` (migration 004) — "I'm on my way": origin (home / last_job / custom), travel_mode (car or public transport; the choice exists only if `has_car`), Geoapify route, departed_at. The client sees departure time, travel time and expected arrival (Berlin time via the `local_time` filter), never the origin. Service `maintops_core/trips.py`, route `POST /jobs/<id>/trip`.
- `handyman_performance` (per handyman × type + 'all'; migration 006 added `billed_jobs`, `hours_billed`, `amount_billed`, `hourly_rate`) and `handyman_feedback` (sentiment, review summary) — written by the pipeline, read by `find_handymen`.
- Migrations 001–006 applied (constraints, triggers, indexes, REPLICA IDENTITY FULL, scorecards, required_skills, trips, billing). A schema change makes CDF re-snapshot the history table; the live stream uses `skipChangeCommits` for that.
- Lakebase CDF config `maintops_cdf` (schema maintops → `bootcamp_students.maintops.lb_*_history`, ~15 s). Arrays arrive as Postgres text `{a,b}`.
- Synthetic data in Lakebase: 110k users (ids ≤ 110000, demo password in `00_config`), 44.7k recent incidents (ids ≤ 1,000,000); app rows get ids above those ranges.
- Unity Catalog: catalog `bootcamp_students`, schema `maintops`, volume `maintops_docs` (CVs under `handyman_cvs/`).
- Specialisations are a fixed vocabulary of 10, defined in `app.py` (`SPECIALISATIONS`) and `data_synthesis/00_config` — change both together.

## Conventions
- Raw parameterised SQL, never string-built from user input:
  `with get_connection() as conn: with conn.cursor() as cur:` → tuples mapped to dicts by hand → explicit `conn.commit()` on writes.
- Errors to the user via `flash(msg, "error"|"success")` + `redirect`.
- Passwords: Argon2id through the module-level `password_hasher`; verify only with `verify_password()`.
- Section banners `# ─────`; private helpers prefixed `_`.
- LLM does semantic understanding only; filtering, scoring, ranking, distances are deterministic code (README rules 8–11).
- Secrets only from env vars (`FLASK_SECRET_KEY`, `LAKEBASE_PG_URL`, `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `MANNY_ENDPOINT`, `DATABRICKS_WAREHOUSE_ID`, `GEOAPIFY_API_KEY`); on Databricks from secret scope `maintops` (`lakebase_pg_url`, `geoapify_api_key`). Locally in git-ignored `.env` (template: `.env.example`).
- Notebooks: Databricks-format `.ipynb` (JSON, indent 1, non-ASCII kept). Edit them by loading/dumping JSON with `indent=1, ensure_ascii=False` so diffs stay minimal. `data_synthesis/00_config.py` is a generated copy of `00_config.ipynb` — edit both.

## Running
- App: Python 3.12, `.venv` has the app deps; `set -a; . ./.env; set +a` plus `DATABRICKS_HOST`/`DATABRICKS_TOKEN` (`databricks auth token -p george_sokolovsky`), then `flask run --debug`.
- Schema changes: add `sqls/migrations/00N_*.sql` (idempotent), `python sqls/migrate.py --dry-run`, then without the flag.
- Notebooks locally: `pip install -r requirements-notebooks.txt` . Never install `pyspark` next to `databricks-connect`.
- Tests: `.venv/bin/python -m pytest -q` (275 unit tests incl. 200 randomised ranking cases, no DB/network). Lint: `uvx ruff check .` (config in `pyproject.toml`: line length 120; notebooks and the generated `00_config.py` excluded; Manny's prompt strings are exempt from E501 because rewrapping them changes the gated prompt). Keep it passing.

## Status
See `PLAN.md` for the schedule. Done: RAG (own endpoint `maintops_vs`, hit@3 12/12), synthetic data + Lakebase load, migrations, CDF, batch Spark pipeline, live stream, core services, Manny agent + UC deployment, Manny widget wired to `/api/manny` with candidate cards and "Choose".
Also done: dashboard actions (rate, cancel, start/complete job), analytics Declarative Pipeline `maintops_analytics` (refreshed every 30 min), live-stream burst test PASS (1,000 reviews → 900 scorecards in 36.8 s). Also done: end-to-end eval + staging release gate (eval/), confirmation for submit_feedback, `skills_matched` field, per-candidate figure guard, hourly rates, routing by car or public transport, "I'm on my way" trips. Manny v21 passed the gate 189/189 (63 scenarios ×3, 1,776 checks) on 2026-10-03 and serves production (alias `production`). Also done: billing (hours worked + amount paid on completed jobs → hourly rate). Also done (2026-10-03): CV sign-up fills the address (+ region/country from geocoding), live dashboard updates after Manny/Choose (`data-live` sections, `refreshDashboard()` in base.html), speed-ups A (visitor FAQ searched before the first LLM call), B (`find_handymen` runs automatically after `create_incident`), C (travel only where it can change the top 3). Evidence: `evidence/*.md` from `evidence/export_evidence.py`; README is the instructor-facing document (rubric map, walkthrough); sample CV in `samples/`.

## Known issues (confirm with the user before fixing)
Fixed 2026-09-28: open redirect on login, CV upload at registration (`/parse-cv` no longer needs login; file → Volume `cv_uploads/` → ai_parse_document on `DATABRICKS_WAREHOUSE_ID` → Manny `extract_cv`), register saves all fields + CV path + geocodes, CSRF (`CSRFProtect`, token in forms and `X-CSRFToken` header), `client_required`/`handyman_required` in use, render.yaml has LAKEBASE_PG_URL / GEOAPIFY_API_KEY / MANNY_ENDPOINT.
1. Render (https://maintops-h3bv.onrender.com) is the only live deployment; Databricks Apps is not used (workspace owner's request, cost). Manny there needs a 1-hour `DATABRICKS_TOKEN` (`databricks auth token --force-refresh`): student accounts cannot create PATs.
2. The workspace LLM limit (input tokens per minute on `databricks-claude-sonnet-4-6`) is shared by Manny, the eval and its judges: keep eval workers ≤ 4; Manny retries 429s with backoff and then answers "busy".
3. Lakebase = Autoscaling project `george-sokolovsky-capstone`, branch `production`, endpoint `primary` (`ep-round-union-d11k2roa`), autoscaling 0.5–2 CU (raised from a fixed 0.5 CU on 2026-10-02: at 0.5 CU the CDF `wal2delta` worker ran out of memory and Postgres restarted every ~15 s, dropping every connection). Check with `databricks postgres list-endpoints projects/george-sokolovsky-capstone/branches/production`; uptime via `SELECT now() - pg_postmaster_start_time()`.

## Working with the user
- Propose changes and wait for approval before applying non-trivial edits; the user reviews diffs first.
- Don't commit unless asked.
