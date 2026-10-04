# MaintOps — 1-week plan (Sat 26 Sep → Fri 2 Oct, Sat 3 Oct buffer)

**Goal:** everything in the rubric working on Render, with evidence. Target 85+ (now ~35, capped at 60).
**Split:** Claude writes the code and runs Databricks via CLI. You: logins/keys + one ~15-min review per day.

## Key decisions
- Web app on **Render**, calls the agent over REST.
- **Agent = UC model** (`bootcamp_students.maintops.maintops_agent`), deployed with `agents.deploy` → gives tracing + inference tables.
- Agent tools and UI buttons share one package `maintops_core/` (same logic, one place).
- **Flow:** text → agent classifies → `create_incident` → `find_handymen` → 3 cards → client picks → handyman assigned.
- **Feedback loop:** rating + review → sentiment & summary (via CDF, ~1 min) → affects ranking and what the agent says.
- **Governance:** guardrails, inference tables, tracing, MLflow evaluation.
- Scores always computed in code; user identity comes from the server, never from the LLM.

## Days
| Day | Work | Rubric | Status |
|---|---|---|---|
| 0 | **You:** CLI login; `LAKEBASE_PG_URL` + `GEOAPIFY_API_KEY` in `.env` and Render. **Me:** check access (CDF, endpoints, secrets, Render→Lakebase). | — | you: done |
| 1 | **Data day:** RAG finished; Lakebase indexes/triggers/`app_events`, load synthetic users+handymen, start CDF; Spark bronze→silver→gold (+ feedback features), parse 10k CVs — all in one Databricks Job. | Spark, Lakebase, Big Data | ✅ RAG (hit@3 12/12), synthetic data, Lakebase (110k users, 44.7k incidents), CDF, batch pipeline (1M → 981k silver, 18.5k quarantined, scorecards → Lakebase), 10k CVs parsed, live stream |
| 2 | `maintops_core`: Geoapify (retries, validation), `find_handymen` scoring, incident + feedback services, tests. | API, Lakebase | ✅ + 22 unit tests |
| 3 | Agent model: tools, guardrails, tracing, eval set → register in UC → deploy. | Agent (removes cap) | ✅ UC model `manny` served at `maintops-manny`; eval script ready (run pending) |
| 4 | Connect app to agent; pages: my incidents + candidate cards, incident detail + feedback, handyman jobs; Manny live; fix known issues. | Frontend | ✅ widget → `/api/manny` with cards + Choose; dashboard: rate job, cancel, start/complete; CV upload at registration via Manny; CSRF |
| 5 | Streaming analytics from CDF, feedback refresh, latency < 60 s measured; Render hardening. | Analytics, Velocity, Deploy | ✅ analytics pipeline (8 metric tables, every 30 min); burst test PASS 36.8 s; gunicorn + /healthz in configs — Render redeploy by you |
| 6 | End-to-end test on Render, final eval, bug fixes. | — | |
| 7 | Evidence: README rubric map, screenshots, logs, eval results. Buffer. | All | |

## If we slip, cut in this order
Stretch extras → handyman chat tool → CV batch parsing.

## Main risks
- Render or the agent endpoint can't reach Lakebase → fallback: Databricks Apps / agent calls a Flask internal API.
- AI Gateway not allowed → guardrails + logging in the app instead.

## Done when
On the Render URL: describe a problem → 3 candidates → choose → handyman completes → review → a new similar request ranks differently within ~1 min. Plus: Job run green (≥1M rows), traces and eval in MLflow, analytics tables updating.

## Demo script (evidence for grading — record on the Render URL)
All synthetic accounts use the demo password `MaintOps!2026` (`DEMO_PASSWORD` in `data_synthesis/00_config`).

Before recording:
- Live stream on: `databricks bundle run maintops_live --params max_minutes=60 -p george_sokolovsky`.
- Lakebase at 0.5–2 CU (it was found back at a fixed 0.5 CU once): `databricks postgres list-endpoints projects/george-sokolovsky-capstone/branches/production -p george_sokolovsky`.
- Fresh Databricks token in Render (`DATABRICKS_TOKEN`, 1 h) unless the service principal is set; otherwise Manny and CV upload say "not available".
- Open the Render URL once to wake the free instance (first request up to 50 s).

1. **Visitor + RAG** — landing page, open Manny: "Do you always send the closest handyman?" → grounded FAQ answer. "How much does MaintOps cost?" → says pricing is not specified, invents nothing. "Ignore all previous instructions…" → refusal. 📸
2. **Handyman sign-up with a CV** — Sign up → handyman → upload `samples/cv_100001.pdf` → name, phone, address (Krefelder Weg 16, 22419 Hamburg), region, specialisations and skills filled in (≈ 20 s, `ai_parse_document` + Manny). No need to submit. 📸
3. **Client flow** — log in as `thomaskoch37@example.com` (Berlin). "Report New Incident" → "Water is leaking from the pipe under my kitchen sink." → Manny logs the incident and shows 3 cards (match %, skills matched, success rate, rating, real travel time by car or public transport, €/h, review summary). 📸 **Choose** the first → confirm → assigned; the card now shows the handyman's phone and email (contact details only while the job is active). 📸
4. **Handyman flow** — log in as the chosen handyman (their email is on Thomas's card) or `ute.wisniewski15@example.net` (job #981034). The job card shows the client's name, address with a map link, phone and email. 📸 **I'm on my way** → by public transport, then by car (Geoapify route) → as the client: departure, travel time and expected arrival, never the starting point. 📸 Ask Manny "What's the client's phone number?" → it has no contact details and points to the job card. **Start job** → **Mark completed** with 1 h and €500 → refused (implausible €/h); with 2.5 h and €135 → confirmation shows €54.00 per hour → completed. 📸
5. **Feedback loop** — back as the client: rate the job (e.g. 1★ "Arrived three hours late"), or via Manny "Rate job N 1 star" → asks for confirmation → saved. Within ~1 min `handyman_performance` / `handyman_feedback` and the handyman's €/h update (show `latency_metrics` and the scorecard before/after). 📸
6. **Handyman insights** — as Ute: "Show me the jobs where my feedback was the worst" and "What's my weakest side?" → list + recurring complaint + one practical tip, every figure grounded. 📸
7. **Databricks evidence** 📸 — job runs (`maintops_pipeline`, `maintops_rag`, `maintops_live`, `maintops_manny_deploy`), `pipeline_runs` (1,004,842 → 979,490 + 20,510 quarantined), `quarantine_incidents` reasons, Lakebase CDF `lb_*_history`, analytics pipeline `maintops_analytics` graph + expectations, `analytics_*` tables, release gate in MLflow experiment `maintops_manny_eval` (v32: 72 scenarios × 3, 216/216, 2,118 checks, 0 failed) + traces, endpoint `maintops-manny` + inference table `manny_payload`, UC model `bootcamp_students.maintops.manny` (alias `production`).
8. **Burst test** — `.venv/bin/python pipeline/latency_test.py 1000` → PASS < 60 s. 📸
