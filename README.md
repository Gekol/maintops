# MaintOps

AI-powered handyman matching and incident management, built as a Databricks capstone project.
A client describes a problem in plain language; an agent classifies it and the system recommends the three best-suited handymen (skills, experience, ratings, workload, travel time). The client picks one, the handyman completes the job, and the client's feedback improves future rankings.

> **Status:** core workflow implemented end to end — Manny (agent served from Unity Catalog) creates incidents, ranks handymen with real Geoapify travel times and assigns the client's choice; handymen complete jobs; client reviews flow back through Lakebase CDF and a streaming pipeline into the rankings within a minute. See [Rubric evidence](#rubric-evidence) for measured results.

## Architecture at a glance

| Layer | Technology | Role |
|---|---|---|
| Web app | Flask, Flask-Login, Flask-WTF (CSRF), gunicorn | UI, auth, dashboards, Manny chat widget |
| Agent | **Manny**: MLflow `ResponsesAgent` registered in UC (`bootcamp_students.maintops.manny`), served by `agents.deploy` (endpoint `maintops-manny`), LLM `databricks-claude-sonnet-4-6` | all AI: FAQ answers (RAG), incident classification + creation, ranking, assignment, feedback, CV profile extraction |
| Operational store | Lakebase (Postgres) | users, handymen, incidents, app events, handyman scorecards |
| CDC | Lakebase Change Data Feed → `lb_*_history` Delta tables (~15 s) | feeds the live stream and analytics |
| Batch pipeline | Spark (Auto Loader, Delta MERGE), `ai_parse_document`, `ai_analyze_sentiment`, `ai_query` | 1M-incident history → validated silver → handyman scorecards; 10k CV PDFs → text |
| Live pipeline | Spark Structured Streaming on CDF | new reviews → affected handymen's scorecards in Lakebase in < 1 min |
| Analytics | Lakeflow Declarative Pipeline on CDF | agent / tool / API / guardrail / write-action / incident metrics |
| RAG | Vector Search (`faq_index` on endpoint `maintops_vs`, `databricks-gte-large-en`) | FAQ retrieval for Manny |
| Geo | Geoapify Geocoding + Route Matrix + Routing (public transport) | address coordinates, real travel times: by car for handymen with `has_car`, by public transport otherwise |

## Repository layout

```
app.py                  Flask app: routes, auth, /api/manny proxy, dashboard actions, CV upload
db.py                   re-exports maintops_core.db (shared Lakebase pool)
maintops_core/          services shared by the app and the agent
  incidents.py          incident lifecycle with ownership + transition checks
  matching.py           find_handymen: filter → pre-filter → Geoapify → deterministic score → top 3
  geo.py                Geoapify geocoding, route matrix (car) and transit routing (no car); retries, validation, flagged fallback
  rag.py                FAQ vector search
  events.py             app_events logging (→ CDF → analytics)
agent/
  manny.py              the Manny agent (tools, guardrails, tracing)
  deploy_manny.ipynb    log → register in UC → staging endpoint → eval gate → agents.deploy (production)
eval/                   end-to-end agent eval and release gate: manny_eval.py (runner), eval_scenarios.py,
                        eval_checks.py (deterministic checks), eval_fixtures.py (test accounts in Lakebase)
pipeline/               Spark pipeline (logic in pipeline_lib.py)
  10–15                 raw export → bronze → silver (+quarantine) → gold → Lakebase
  16_parse_cvs          ai_parse_document over the CV PDFs
  20_live_stream        CDF → scorecards in Lakebase (< 1 min)
  latency_test.py       burst latency test
  analytics/            Lakeflow Declarative Pipeline (metrics)
notebooks/              RAG: 01 schema → 02 parse FAQ → 03 chunk + index → 05 retrieval eval; 06 keep-alive
data_synthesis/         synthetic data (01–06) and Lakebase load (07)
sqls/                   base DDL + migrations/ (applied by migrate.py)
tests/                  unit tests (pytest)
databricks.yml          Asset Bundle: all jobs and the analytics pipeline
app.yaml, render.yaml   deployment configs
requirements.txt        App dependencies
requirements-notebooks.txt  Local notebook dependencies (Databricks Connect)
```

## Running the app locally

Requires Python 3.12 and a reachable Lakebase (Postgres) instance.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export FLASK_APP=app.py
export FLASK_SECRET_KEY=<random string>
export LAKEBASE_PG_URL="postgresql://user:password@host:5432/dbname?sslmode=require"
# Needed for CV parsing:
export DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
export DATABRICKS_TOKEN=<personal access token>

flask run --debug
```

The app is served at http://127.0.0.1:5000.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `FLASK_SECRET_KEY` | yes (prod) | Session/CSRF signing key. Defaults to an insecure dev value. |
| `LAKEBASE_PG_URL` | yes | Postgres connection string for Lakebase (see `db.py`). |
| `DATABRICKS_HOST` | yes | Workspace URL (Manny endpoint, CV parsing). |
| `DATABRICKS_TOKEN` | yes | PAT or OAuth token (only the backend holds it). |
| `MANNY_ENDPOINT` | no | Manny serving endpoint; defaults to `maintops-manny`. |
| `DATABRICKS_WAREHOUSE_ID` | for CV upload | SQL warehouse that runs `ai_parse_document`. |
| `GEOAPIFY_API_KEY` | yes | Geoapify key (geocoding on registration / address change). |

On Databricks the agent endpoint and jobs read `LAKEBASE_PG_URL` and `GEOAPIFY_API_KEY` from the secret scope `maintops`.

## Deployment

- **Render (graded):** live at **https://maintops-h3bv.onrender.com** (free instance: the first request after a quiet period can take about 50 s). `render.yaml` runs gunicorn with a `/healthz` health check; every push to `main` deploys automatically. Secrets (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `LAKEBASE_PG_URL`, `GEOAPIFY_API_KEY`) are set in the Render dashboard (`sync: false`).
- **Databricks Apps:** `app.yaml` runs the same gunicorn command on port 8000 with secrets from the app's resources.
- **Databricks side:** `databricks bundle deploy -p <profile>` deploys every job and the analytics pipeline (`databricks.yml`); `databricks bundle run maintops_manny_deploy` (re)deploys Manny; `python sqls/migrate.py` applies schema migrations.

### Databricks token for Manny (for instructors)

The web app calls Manny (Model Serving endpoint `maintops-manny`) with `DATABRICKS_HOST` + `DATABRICKS_TOKEN`. Student
accounts in the bootcamp workspace cannot create personal access tokens, and a student's CLI OAuth token expires after
1 hour, so on the Render deployment Manny only answers while a fresh token is set. Everything else (login, dashboards,
recommendations shown on cards, ratings, job status, "I'm on my way" trips, Geoapify routing) works without it; CV upload
at registration also needs the token.

To assess the app with Manny, a workspace admin creates a token for the session (8 hours here) and sets it:

```bash
# 1. Create an 8-hour token (prints token_value once; the token's owner needs CAN QUERY on maintops-manny)
databricks tokens create --lifetime-seconds 28800 --comment "MaintOps assessment" -p <your-profile>
#    Without the CLI: workspace Settings → Developer → Access tokens → Generate new token (lifetime: 1 day)

# 2a. Use it on Render: dashboard → service "maintops" → Environment → DATABRICKS_TOKEN = <token_value> → Save
#     (Render redeploys in about a minute)
# 2b. Or run the app locally: put DATABRICKS_HOST, DATABRICKS_TOKEN, LAKEBASE_PG_URL, GEOAPIFY_API_KEY and
#     FLASK_SECRET_KEY in .env (see "Running the app locally"), then: flask run
```

Without a personal access token, a 1-hour OAuth token works the same way:
`databricks auth token -p <your-profile>` (field `access_token`). Demo accounts: see "Synthetic data" below
(all share the demo password `MaintOps!2026`), e.g. client `thomaskoch37@example.com`, handyman
`ute.wisniewski15@example.net`.

## Rubric evidence

Measured on 2026-09-28 (tables in `bootcamp_students.maintops` unless noted).

| Area | Evidence |
|---|---|
| Spark pipeline | Job `maintops_pipeline`: 1,000,000 incidents → raw JSON with injected defects (1,004,842 rows) → Auto Loader bronze → silver **981,466** clean incidents + **18,534** quarantined with reasons (invalid urgency 5,085 · empty description 4,849 · bad timestamp 3,012 · negative distance 2,936 · rating out of range 2,652) → 23,170 scorecard rows + 9,288 review summaries → Lakebase. Incremental (checkpoints, MERGE); every step logged with checks in `pipeline_runs`. |
| Third-party API | Geoapify geocoding at registration / address change one batched Route Matrix call per recommendation for handymen with a car and a Routing API call (`approximated_transit`) for each one without a car; retries on 429/5xx with Retry-After, response validation, fallback estimates flagged `estimated`; every call in `app_events` → `analytics_api_usage_daily`. Unit-tested with mocked HTTP (`tests/`). |
| Lakebase model | 3 core tables + `app_events` + `handyman_performance` / `handyman_feedback`; PK/FK, 15+ CHECK constraints, handyman-role triggers, duplicate-open-incident guard, `updated_at` triggers, indexes; migrations in `sqls/migrations/`. |
| Agent | Manny (UC model `manny`, endpoint `maintops-manny`): read tools `search_faq`, `get_my_incidents`, `get_incident`, `find_handymen`, `get_my_jobs`, `get_my_reviews`, `search_my_reviews`, `get_my_performance`; write tools `create_incident`, `assign_handyman`, `cancel_incident`, `submit_feedback`, `update_job_status` — explicit confirmation required before assign, cancel, rating and status changes, identity from the server only, every tool call logged with its arguments. Guardrails (injection refusal, emergency advice, argument validation, grounded-number check, per-candidate figure check with one correction round and a data-only fallback (`maintops_core/grounding.py`), confirmation-mismatch guard for ratings, no phone numbers except 112, per-session rate limit), MLflow tracing, inference table `manny_payload` (AI Gateway; usage tracking / gateway rate limits are not available for agent endpoints in this workspace, so usage and cost are tracked via `app_events` → analytics). |
| Agent evaluation (release gate) | `eval/manny_eval.py`: 58 scenarios (77 turns) covering every tool, CV extraction, guardrails, authorization and prompt-injection attempts, each run 3× on dedicated test accounts. Checks are code, not LLM judgement: Lakebase state after every turn (right row, right values, nothing written before an explicit yes), tool calls and arguments (from `app_events`), every figure in a reply grounded in data the user may see (and, in a sentence about one candidate, in that candidate's own data), skill-coverage claims, no phone numbers except 112, emergency advice. One failed check fails the gate. `maintops_manny_deploy` deploys each new version to a temporary staging endpoint, runs the gate there and promotes to production only on a pass (UC alias `production`). Results: MLflow experiment `maintops_manny_eval` + `eval/results/`. LLM judges (no promises, clear failure messages, safety) are reported, not gating. |
| Analytics | Lakebase CDF → Declarative Pipeline `maintops_analytics` (refreshed every 30 min, expectations): `analytics_agent_requests_hourly` (incl. tokens and estimated cost), `analytics_tool_usage`, `analytics_api_usage_daily`, `analytics_guardrails_daily`, `analytics_write_actions`, `analytics_feature_usage`, `analytics_incident_activity_daily`, `analytics_recommendation_rank`. |
| Volume | 1M-incident history processed by the Spark pipeline (above). |
| Velocity | Lakebase write → CDF → stream → scorecards back in Lakebase: burst of **1,000 reviews for 900 handymen fully reflected in 36.8 s** (`pipeline/latency_test.py`); per-batch latency in `latency_metrics`; checkpointed stream (restart-safe). |
| Variety | 10,000 CV PDFs parsed with `ai_parse_document` (100 % success, `cv_parsed`, text stored in Lakebase); FAQ PDF parsed, chunked and embedded into `faq_index` (retrieval hit@3 = 12/12, `rag_eval_runs`); reviews scored with `ai_analyze_sentiment` and summarised with `ai_query`. |

## Synthetic data (`data_synthesis/`)

Generates realistic data at scale for testing the app and the Spark pipeline: **100,000 client users, 10,000 handymen (users with `is_handyman = true` plus `handyman_details` and PDF CVs) and 1,000,000 incidents**, following the table definitions in `sqls/`. Volumes, table/volume names and the skill taxonomy live in `00_config`, which every other notebook loads via `%run ./00_config`.

Run the notebooks in order on Databricks:

| # | Notebook | Output |
|---|---|---|
| 00 | `00_config` | Shared config (not run on its own) |
| 01 | `01_real_addresses` | Unique real German addresses from OpenStreetMap → `synth_address_pool` |
| 02 | `02_generate_users` | `synth_users` client rows (same columns as `maintops.users`, `is_handyman = false`) |
| 03 | `03_generate_handymen` | Handyman rows appended to `synth_users` (`is_handyman = true`), `synth_handyman_details` (same columns as `maintops.handyman_details`), plus `synth_handymen_truth` (hidden quality, structured CV) |
| 04 | `04_generate_cv_pdfs` | One PDF CV per handyman in the Unity Catalog Volume |
| 05 | `05_generate_incidents` | `synth_incidents` Delta table (columns of `maintops.incidents`) with Change Data Feed; handyman stats come from the Spark pipeline |
| 06 | `06_validate` | Read-only PASS/FAIL sanity checks |

Notes:
- Emails use reserved `example.*` domains, and all synthetic users share the demo password defined in `00_config` (Argon2-hashed). Do not reuse it anywhere real.
- `01` needs outbound internet access to `download.geofabrik.de`. Address data © OpenStreetMap contributors (ODbL).
- `00_config.py` is generated from `00_config.ipynb` so `%run ./00_config` also works locally; re-export it whenever the notebook changes.

### Running notebooks locally

`requirements-notebooks.txt` lets you run the notebooks from your IDE through Databricks Connect (serverless). It needs Python 3.12 and a configured Databricks profile. Do not install `pyspark` alongside `databricks-connect`; they conflict.

```bash
pip install -r requirements-notebooks.txt
pip install --no-deps langgraph-prebuilt==1.0.1   # must be installed after the file above
```

---

# Design

## Workflow and users

1. A client registers and describes an incident in natural language.
2. An AI agent determines its type, urgency and required skills.
3. The system finds suitable handymen by specialisation and skill match, experience and historical performance, client ratings, current workload and travel time (Geoapify).
4. The best three candidates are returned; the client makes the final choice.
5. The handyman sees the assigned incident, handles it and marks it completed.
6. The client leaves a rating and textual feedback, which influences future recommendations.

There are three user experiences:

- **Unregistered visitor:** landing page, information about MaintOps, questions to the RAG assistant (Manny), registration.
- **Registered client:** profile and address; create incidents, see active and past incidents and their status, view recommended handymen and select one, rate completed work and leave feedback.
- **Handyman:** profile, upload/update CV and see the extracted specialisations/skills/experience; assigned incidents sorted by urgency, mark them in progress / completed, see feedback and statistics.

## Capstone requirements

| Requirement | MaintOps implementation |
|---|---|
| Spark data pipeline | Spark processes the large historical incident dataset and derives handyman-performance features |
| Third-party API | Geoapify Geocoding and Route Matrix APIs |
| Lakebase operational model | Current users, handymen and recent incidents |
| Action-taking AI agent | Agent creates incidents and invokes tools that search/rank handymen |
| Analytics pipeline | Lakebase CDC → Spark → Delta for historical/analytical data |
| Frontend | Role-specific application UI |
| Deployment | Databricks App (also deployed on Render) |
| High Volume | At least 1,000,000 synthetic historical incidents in Delta |
| High Variety | Handyman CVs supplied as unstructured PDF/image documents |

## Storage principles

**Lakebase is the operational store.** It holds the latest relational state: who a client is, a handyman's current profile, open incidents, assignments, current workload.

**Unity Catalog / Delta is the historical and analytical store:** the 1M+ synthetic incidents, long-term incident history, historical handyman performance, derived recommendation features and application/agent analytics. Do not load the synthetic history into Lakebase just to demonstrate volume.

**CDC connects them:** Lakebase `incidents` → CDC → Spark → Delta incident history. Lakebase stays authoritative for current state. CDC is replication, not a transactional move, so completed incidents are not deleted from Lakebase immediately; a production version could purge them after e.g. 30 days (optional for the capstone).

**Current vs historical reads:** Lakebase answers "my active incidents", "my assigned jobs", "current status of incident X"; Delta answers "jobs I completed last year" or "this handyman's performance on plumbing". The backend owns this distinction, not the frontend.

| Lakebase | Unity Catalog / Delta |
|---|---|
| latest user profile | raw CV files (Volume) |
| latest handyman profile | parsed/historical CV artifacts |
| active/recent incidents | 1M+ incident history, completed incidents |
| | handyman performance features, analytics |

## Lakebase data model

The operational schema is the Postgres schema `maintops` with three tables; the DDL in [`sqls/`](sqls/) is the source of truth.

- **`users`**: every account, clients and handymen alike. Identity `id`, unique `email`, `password_hash`, name, `date_of_birth`, `phone`, address (`house`, `postal_code`, `city`, `state`, `country`), `latitude`/`longitude`, `is_handyman`, `is_active`, `created_at`. Unregistered visitors have no row.
- **`handyman_details`**: one row per handyman, `user_id` is both primary key and foreign key to `users.id`. Holds `specialisations TEXT[]`, `skills TEXT[]`, `experience_summary`, `cv_path`, `cv_raw_text`, `has_car`, and the performance summary `completed_cases`, `rating_avg` (0–5), `rating_count`, `avg_price` (the handyman's average hourly rate in EUR, shown to clients as €/h; realistic German rates, e.g. plumbing ~€68/h, locksmith ~€75/h, general maintenance ~€42/h).
- **`incidents`**: `reported_by_user_id` (client) and `handyman_user_id` (assigned handyman), both foreign keys to `users.id`; `description`, `incident_type`, `urgency` (`low` / `medium` / `high` / `critical`), `recommended_handyman_ids BIGINT[]`, `agent_reasoning`, `distance_km`, `travel_time_minutes`, `status`, `rating` (1–5), `feedback`, and `created_at` / `assigned_at` / `completed_at` / `updated_at`.
- **`incident_trips`** (migration 004): the handyman's trip to the client, one row per incident (PK/FK `incident_id`). "I'm on my way" on an assigned job records where they set off from (`origin_kind` home / last_job / custom, geocoded address and coordinates), how they travel (`travel_mode`: a handyman with a car chooses car or public transport; without a car it is always public transport and no choice is offered), and the Geoapify route (`distance_km`, `travel_minutes`, `estimated`, `departed_at`). The client's dashboard shows departure time, travel time and expected arrival as an estimate, never the starting address (it may be another client's home). Service: `maintops_core/trips.py`.

### Specialisations, skills and experience

All three are kept:

- **Specialisations** are a controlled vocabulary used for coarse filtering: `plumbing`, `electrical`, `heating_hvac`, `carpentry`, `painting`, `roofing`, `flooring`, `appliance_repair`, `locksmith`, `general_maintenance`. CV extraction must choose from this list, never invent categories.
- **Skills** are granular capabilities (pipe repair, leak detection, boiler maintenance, …) used for specific job compatibility.
- **`experience_summary`** is richer CV-derived text about the handyman's professional experience.

Filtering goes specialisations → skills → historical performance/experience → feedback, workload and travel time → top candidates. Both lists are stored as `TEXT[]`, not separate tables.

## Authentication

Store only an Argon2id `password_hash` (never plaintext or reversibly encrypted passwords). On login, look the account up by email and verify with the Argon2 library's `verify`; never compare hash strings manually and never log passwords. The encoded hash already contains salt and parameters, so no salt column is needed.

## Geocoding

Clients and handymen give an address at registration. Geoapify forward geocoding turns it into `latitude`/`longitude`, which are stored in `users` so searches never re-geocode. Geocode again only when the address changes. `house` holds street and house number for the MVP.

## Handyman availability

Availability is derived from active incidents (`assigned`, `in_progress`), not stored as an `is_available` flag that could drift out of sync. MVP rule: 0 active incidents = highly available, 1 = available, 2 = busy but available, 3+ = unavailable. The threshold should be configurable, and fewer active incidents should also rank higher.

```sql
SELECT u.id, u.first_name, u.last_name, COUNT(i.id) AS active_incidents
FROM maintops.users u
JOIN maintops.handyman_details h ON h.user_id = u.id
LEFT JOIN maintops.incidents i
       ON i.handyman_user_id = u.id
      AND i.status IN ('assigned', 'in_progress')
GROUP BY u.id, u.first_name, u.last_name
HAVING COUNT(i.id) < 3;
```

## Handyman search and recommendation

The LLM handles semantic understanding; deterministic code handles filtering, routing, scoring and ranking. The LLM never invents scores or scans every handyman.

1. **Classification (agent).** "Water is leaking from a pipe under my kitchen sink" becomes `incident_type = plumbing`, `urgency = high`, `required_skills = [pipe repair, leak detection]`. The agent creates/updates the incident and calls the search tool.
2. **Candidate filtering (Lakebase)** by specialisation, skills, workload and other operational constraints.
3. **Geographic pre-filter.** For large candidate sets, compute straight-line (Haversine) distance from stored coordinates and keep the nearest ~20. Never call Geoapify for every handyman.
4. **Geoapify travel times** for the shortlist, by the handyman's `has_car` flag: handymen with a car get real road distance and driving time from one batched Route Matrix request (mind the API's coordinate order); handymen without a car get a public transport route each from the Routing API (`approximated_transit`, the Route Matrix has no transit mode), up to 4 in parallel. Cards and Manny say "7 min by car" or "35 min by public transport".
5. **Deterministic scoring** with configurable initial weights:

   | Signal | Weight |
   |---|---|
   | Skill / incident match | 35% |
   | Similar successful cases (from Delta) | 30% |
   | Historical feedback | 20% |
   | Current workload | 10% |
   | Travel time | 5% |

6. **Top 3** are shown; the client selects one.

The agent-facing abstraction is a single tool, `find_handymen(incident_id: int)`, which reads the incident and client coordinates, finds matching non-overloaded handymen, pre-filters geographically, calls the Route Matrix, fetches historical performance features, scores and returns the top three. The LLM does not orchestrate individual SQL queries and HTTP calls.

## Incident lifecycle

`open` → `recommended` → `assigned` → `in_progress` → `completed` (`cancelled` is allowed where appropriate).

1. Client submits a free-text description; the agent determines type and urgency and the incident is created in Lakebase.
2. The search tool returns three candidates; the client selects one, `handyman_user_id` is set and the status becomes `assigned`.
3. The handyman sees it among their active jobs. When they set off they tap "I'm on my way" (from home, their last job or another address; by car or public transport if they have a car) and the client sees the expected arrival. They move it to `in_progress` on arrival, then mark it `completed`.
4. The client provides `rating` and `feedback`.
5. The outcome flows through CDC into Delta and into future performance features.

## Historical data and Spark

Spark is used for the historical incident dataset, not for the synchronous CV-registration path. Over the 1M+ incidents it cleans and validates records, derives resolution times, aggregates by handyman and incident type, and computes completion/success rates and average ratings. The result is a precomputed handyman performance table (e.g. `handyman_id`, `incident_type`, `jobs_completed`, `successful_jobs`, `average_rating`, `average_resolution_time`) that recommendation queries read; the raw history is never scanned per request.

The Delta history also feeds incident analytics, recommendation-quality analysis and agent cost analytics: requests per user, input/output/total tokens, estimated model cost per request and per user, resolution time, rank of the selected recommendation, handyman rating and recommendation success.

## CV upload and processing

CVs (PDFs and images) are the High Variety component. Processing is synchronous and separate from Spark, since a handyman expects a usable profile right after registering:

1. The original file is stored in a Unity Catalog Volume.
2. `ai_parse_document` turns it into structured document content.
3. A separate AI extraction step maps it to specialisations (from the controlled vocabulary), skills and an experience summary.
4. The latest structured result, `cv_path` and any parsed text the app needs are written to `handyman_details`.

Parsing and extraction are separate steps: parsing does not produce the final profile.

## RAG assistant for visitors

Unregistered visitors can ask Manny questions without an account. The informational documents are parsed, chunked and indexed for vector retrieval (`notebooks/02`–`03`), and a LangGraph agent answers from them (`notebooks/04`). It is informational only and needs no user record.

## Feedback loop

Recommendation → client selection → completed work → rating and feedback → historical Delta data → Spark-derived performance → future recommendations. For the MVP the numeric rating feeds performance features directly; textual feedback can later be summarised for richer signals.

## Responsibility boundaries

| Component | Use for | Not for |
|---|---|---|
| LLM / agent | understanding descriptions, incident type, urgency, required skills, choosing tools, user-facing explanations | numeric scores, distances, scanning history, comparing thousands of profiles |
| Deterministic code | SQL filtering, workload, Haversine pre-filter, Geoapify calls, ranking, DB writes, authentication, authorization | |
| Spark | historical processing, cleaning, aggregation, feature generation, CDC into Delta, analytics | a single CV upload |
| Lakebase | current users, handyman profiles, current/recent incidents, transactional writes | the 1M+ history |
| Delta / Unity Catalog | historical incidents, derived features, analytics, raw/unstructured files | |
| Geoapify | forward geocoding, Route Matrix for shortlisted handymen | |

## Implementation rules

Unless the requirements change explicitly:

1. Keep the Lakebase operational schema to the three tables `users`, `handyman_details` and `incidents`.
2. Do not create separate tables for specialisations or skills; store them as arrays in `handyman_details`.
3. Treat specialisations as a controlled vocabulary.
4. Never store plaintext passwords; use `password_hash` with Argon2id.
5. Never log passwords or secrets. Keep API keys and credentials in environment variables/secrets, never in source code.
6. Geocode addresses on registration or address change, persist the coordinates, and never re-geocode unchanged addresses.
7. Derive handyman availability from assigned/in-progress incidents instead of an `is_available` flag.
8. Filter candidates by specialisation, skills and workload before any routing call; for large sets pre-filter geographically before calling Geoapify.
9. Batch Geoapify Route Matrix requests rather than one per handyman.
10. Travel time is a ranking feature, not the dominant criterion.
11. Ranking scores are calculated by deterministic code; the agent understands language and invokes tools but never invents operational facts.
12. The client makes the final choice among recommended handymen.
13. Keep active/current state in Lakebase and high-volume history and analytics in Delta/Unity Catalog; never put the 1M+ synthetic dataset into Lakebase.
14. Propagate incident changes to Delta through CDC/Spark, and do not delete completed incidents from Lakebase just because CDC copied them.
15. Use Spark only for workloads that justify it (historical transformation/aggregation, CDC analytics), never per CV upload.
16. Store raw CV files in a Unity Catalog Volume, parse/extract during registration or update, write the latest result to `handyman_details`, and keep parsing separate from field extraction.
17. The visitor RAG assistant uses the MaintOps knowledge base and does not require registration.
18. Parameterise all database access; never build SQL from raw user input.
19. Enforce authorization in the backend: clients only see their own profile and incidents, handymen only incidents assigned to them unless a workflow explicitly requires otherwise.

## MVP scope

Prioritise a complete working vertical slice over extra normalisation or infrastructure. The MVP is done when it demonstrates:

registration → address geocoding → handyman CV processing → incident creation → AI classification → candidate search → Geoapify routing → top-3 recommendation → client selection → handyman workflow → completion → feedback → CDC / Delta history → Spark historical-performance pipeline.

Keep further sophistication out of the critical path unless the capstone requires it.
