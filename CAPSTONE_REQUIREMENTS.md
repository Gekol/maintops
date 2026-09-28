# Capstone grading rubric

Projects should be graded based on demonstrated functionality and submitted evidence. Award partial credit when a component is attempted but incomplete. Do not assume functionality that is not shown in the code, deployment, screenshots, demo, or documentation.

## 1. Spark Data Pipeline — 15 points

Evaluates whether the project uses Spark to prepare project data.

- 0–4 points: No Spark pipeline, or only superficial use of Spark without meaningful processing.
- 5–8 points: Spark is used for basic ingestion, cleaning, or transformation, but the pipeline is limited or incomplete.
- 9–12 points: Functional Spark pipeline that ingests and transforms relevant project data.
- 13–15 points: Robust, reusable, and re-runnable Spark pipeline with meaningful enrichment, validation, error handling, and appropriate output tables.

Consider:
- Does the pipeline use Spark rather than only pandas or local Python?
- Does it ingest, clean, transform, or enrich data?
- Is it re-runnable and reasonably idempotent?
- Are schema, null values, duplicates, and malformed records handled?
- Does the output support the application's workflow?

## 2. Third-Party API Integration — 10 points

Evaluates whether the application uses an external API relevant to the project.

- 0 points: No relevant third-party API integration.
- 1–3 points: API is mentioned or called, but the integration is incomplete, mocked, or disconnected from the application.
- 4–6 points: Working API integration with data stored or displayed by the application.
- 7–8 points: API data is reliably integrated into the pipeline or application workflow with basic error handling.
- 9–10 points: Strong integration with authentication or secrets management, rate-limit handling, retries, malformed-response handling, validation, and meaningful downstream use.

Reduce credit if the API appears to be simulated with hardcoded or fabricated data. Call this out explicitly.

## 3. Lakebase Data Model — 15 points

Evaluates how well the project stores relational and operational application data in Lakebase.

- 0–4 points: No Lakebase model, or data is stored only in files, memory, or unrelated tables.
- 5–8 points: Lakebase is used, but the schema is incomplete, poorly aligned with the application, or only partially functional.
- 9–12 points: Appropriate relational schema supporting core application entities and operations.
- 13–15 points: Well-designed, normalized, documented Lakebase model with appropriate keys, constraints, indexes, timestamps, and operational fields.

Consider:
- Are core entities represented clearly?
- Are primary keys and relationships defined?
- Does the model support the application's write actions?
- Are created/updated timestamps and audit fields included where appropriate?
- Are duplicate records and invalid writes prevented?
- Is the application actually reading from and writing to Lakebase?

## 4. Action-Taking AI Agent — 20 points

The agent must support both information retrieval and meaningful write actions.

### Retrieval and Read Tools — 6 points
- 0–1 points: No usable retrieval tools.
- 2–3 points: Basic retrieval exists but is limited, unreliable, or disconnected from the application data.
- 4–5 points: Agent can retrieve relevant structured or unstructured data using appropriate tools.
- 6 points: Retrieval is accurate, well-scoped, and combines relevant sources such as Lakebase, Delta tables, APIs, or vector search.

### Write and Action Tools — 8 points
- 0–2 points: No write capability, or writes are simulated.
- 3–4 points: Agent can perform a basic write, but validation, confirmation, or error handling is weak.
- 5–6 points: Agent performs meaningful actions that save, update, or delete application data in Lakebase.
- 7–8 points: Multiple reliable write actions with validation, authorization or confirmation safeguards, error handling, and clear user feedback.

Examples include:
- Saving a recommendation
- Adding an item to a watchlist
- Updating a profile or preference
- Logging an application
- Creating or updating a task
- Deleting a saved record

A read-only chatbot does not satisfy the write-action requirement.

### Agent Quality and Reasoning — 6 points
- 0–1 points: Agent is unreliable, hallucinates, or does not use tools appropriately.
- 2–3 points: Agent works for simple cases but provides limited explanations or inconsistent tool use.
- 4–5 points: Agent selects tools appropriately, grounds responses in retrieved data, and explains actions.
- 6 points: Agent handles ambiguity, validates inputs, avoids unsupported claims, communicates failures clearly, and provides accurate summaries of completed actions.

**Required-agent cap:** If the project has no action-taking agent, or the agent is strictly read-only, the overall score should be capped at 60/100.

## 5. Analytics Pipeline — 10 points

Evaluates whether application changes or events are transformed into analytics-ready Delta data.

- 0 points: No analytics pipeline.
- 1–3 points: Analytics table is manually created, static, or not connected to application activity.
- 4–6 points: Change Data Feed from Lakebase or Delta Live Tables is used to populate a Delta table.
- 7–8 points: Pipeline reliably captures and transforms application usage, agent activity, or data changes.
- 9–10 points: Incremental, re-runnable analytics pipeline with useful metrics, appropriate aggregation, monitoring, and documented logic.

Possible analytics include:
- Agent requests by time period
- Tool usage and success rates
- Write actions by user or session
- Record creation, update, and deletion trends
- API usage and failure rates
- Most-used application features

Credit should be based on demonstrated use of Lakebase Change Data Feed or Delta Live Tables, not merely the presence of a dashboard or summary table.

## 6. Frontend and Core Workflow — 10 points

Evaluates whether users can interact with the project through a usable interface.

- 0–2 points: No frontend, or only a notebook/API with no user-facing workflow.
- 3–4 points: Basic interface exists but has limited functionality or broken flows.
- 5–7 points: Usable frontend supports the project's primary workflow and displays relevant results.
- 8–9 points: Clear, responsive interface with agent interaction, data views, action controls, and useful error states.
- 10 points: Polished and intuitive frontend that supports the complete core workflow end-to-end.

Consider:
- Can users submit requests or queries?
- Can users view retrieved results?
- Can users trigger agent actions?
- Are successful writes reflected in the interface?
- Are loading, empty, and error states handled?
- Does the UI provide confirmation for consequential actions?

## 7. Deployed Application — 5 points

Evaluates whether the frontend is actually deployed and accessible.

- 0 points: No deployed application or deployment cannot be verified.
- 1–2 points: Deployment exists but is broken, inaccessible, or incomplete.
- 3 points: Working deployment on Databricks Apps or Render with limited verification.
- 4 points: Stable deployment that supports the demonstrated core workflow.
- 5 points: Fully working deployment with documented setup, environment configuration, secrets handling, and reliable access.

A local-only application should not receive full credit.

## 8. Big Data: At Least Two of the Three Vs — 15 points

The project must demonstrate at least two of the following:
1. High volume: More than 1 million rows
2. High velocity: Data or events processed with less than one-minute latency
3. High variety: Processing unstructured data such as text, images, audio, or video

### High Volume — up to 5 points
- 0 points: No evidence of volume beyond small demonstration data.
- 1–2 points: Large dataset is claimed but not demonstrated or meaningfully processed.
- 3–4 points: More than 1 million rows are ingested or processed using an appropriate distributed workflow.
- 5 points: More than 1 million rows are processed reliably with documented scale, partitioning, performance considerations, and meaningful downstream use.

### High Velocity — up to 5 points
- 0 points: No low-latency or streaming behavior.
- 1–2 points: Near-real-time behavior is claimed but not measured or demonstrated.
- 3–4 points: Data or events are processed in under one minute with a working incremental or streaming pipeline.
- 5 points: Reliable sub-minute processing with monitoring, checkpointing, recovery, and demonstrated latency measurements.

### High Variety — up to 5 points
- 0 points: Only structured data is processed.
- 1–2 points: Unstructured data is present but not meaningfully processed.
- 3–4 points: The project processes relevant unstructured data, such as documents, descriptions, transcripts, images, audio, or video.
- 5 points: Unstructured data is meaningfully transformed, indexed, embedded, searched, or surfaced in the application workflow.

### Big Data scoring rule
The project must demonstrate at least two Vs to receive full credit for this category.
- 0–5 points: Fewer than two Vs are demonstrated.
- 6–10 points: Two Vs are demonstrated at a basic or partially complete level.
- 11–15 points: At least two Vs are convincingly demonstrated with measurable evidence and meaningful use in the application.

If a project demonstrates only one V, award no more than 5/15 for this category.

## Required Grading Response

### Score Summary Table
Provide one row for each rubric category with points earned, points possible, and a concise justification.

### Total Score
State the final score out of 100. Apply the 60/100 cap if the action-taking agent is missing or read-only.

### Strengths
List 2–4 specific strengths, citing relevant files, functions, workflows, or observed behavior.

### Gaps & Deductions
Explain each deduction by linking it to:
- The rubric category
- The missing or incomplete requirement
- The evidence supporting the deduction
- The points withheld

### Evidence Gaps
List anything that could not be verified because it was not provided, such as:
- Deployment URL
- Spark execution logs
- Lakebase schema or connection code
- CDF configuration
- Dataset size
- Measured processing latency
- API request and error-handling code
- Agent tool definitions
- Screenshots or demo transcripts

Evidence gaps should be described as unverified, not automatically treated as proof that the feature is absent.

### Suggestions for Improvement
Give concrete next steps, such as:
- Add idempotent Spark writes and schema validation.
- Replace mocked API data with a live integration and retry handling.
- Add Lakebase constraints and indexes.
- Implement a real write tool that updates Lakebase.
- Enable and demonstrate Change Data Feed.
- Add analytics metrics for agent activity and tool success rates.
- Measure whether processing meets the one-minute velocity requirement.
- Add a second Big Data V, such as embeddings over unstructured documents or processing more than one million rows.
