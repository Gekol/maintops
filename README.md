# MaintOps --- Capstone Project README

## 1. Project Overview

**MaintOps** is an AI-powered handyman matching and incident-management
application built as a Databricks capstone project.

The core workflow is:

1.  A client registers and creates an incident in natural language.
2.  An AI agent interprets the incident, determines its type, urgency,
    and required skills.
3.  The system finds suitable handymen using:
    -   specialisation and skill match,
    -   experience and historical performance,
    -   client ratings/feedback,
    -   current workload,
    -   geographic travel time.
4.  Geoapify is used for geocoding and routing.
5.  The system returns the best three candidates.
6.  The client makes the final choice.
7.  The selected handyman sees the assigned incident and handles it.
8.  The handyman marks the incident as completed.
9.  The client leaves a rating and textual feedback.
10. Historical outcomes and feedback influence future recommendations.

The application has three user experiences:

-   **Unregistered visitor** --- can view the main page, learn about
    MaintOps, ask questions through a RAG assistant, and register.
-   **Registered client** --- can manage their profile, create
    incidents, view active and historical incidents, select a handyman,
    and leave feedback.
-   **Handyman** --- can manage their profile, upload/update a CV, view
    assigned incidents, mark work as completed, and see
    feedback/statistics.

------------------------------------------------------------------------

## 2. Capstone Requirements Mapping

The project must demonstrate the following:

  -----------------------------------------------------------------------
  Requirement                         MaintOps implementation
  ----------------------------------- -----------------------------------
  Spark data pipeline                 Spark processes large historical
                                      incident data and derives
                                      handyman-performance features.

  Third-party API                     Geoapify Geocoding API and Route
                                      Matrix API.

  Lakebase relational/operational     Current users, handymen, and
  model                               operational/recent incidents.

  Action-taking AI agent              Agent creates incidents and invokes
                                      tools used to search/rank handymen
                                      and support application workflows.

  Analytics pipeline                  Lakebase CDC -\> Spark -\> Delta
                                      tables for historical/analytical
                                      data.

  Frontend                            Role-specific application UI.

  Deployment                          Databricks App.

  High Volume                         At least 1,000,000 synthetic
                                      historical incident records in
                                      Delta.

  High Variety                        Handyman CVs supplied as
                                      unstructured PDF/image documents.
  -----------------------------------------------------------------------

The two primary Big Data Vs are **Volume** and **Variety**.

------------------------------------------------------------------------

## 3. Architectural Principles

### 3.1 Lakebase is the operational store

Lakebase contains the latest relational state required by the
application.

It answers questions such as:

-   Who is this client?
-   What is the handyman's current profile?
-   What incidents are currently open?
-   Which handyman is assigned to an incident?
-   What is the handyman's current workload?
-   Has an incident just been completed?

The MVP intentionally keeps the operational model small:

-   `bootcamp_students.maintops_users`
-   `bootcamp_students.maintops_handymen`
-   `bootcamp_students.maintops_incidents`

### 3.2 Unity Catalog / Delta is the historical and analytical store

Delta tables are used for:

-   1M+ synthetic historical incidents,
-   long-term incident history,
-   historical handyman performance,
-   derived recommendation features,
-   application/agent analytics.

Do **not** insert 1M synthetic historical incidents into Lakebase merely
to demonstrate volume.

### 3.3 CDC connects operational and historical data

Changes to operational incidents flow from Lakebase through CDC into the
lakehouse.

Conceptually:

``` text
Lakebase maintops_incidents
          |
          | CDC
          v
        Spark
          |
          v
Delta / Unity Catalog incident history
```

Lakebase remains authoritative for current/recent operational state.
Delta is used for historical and analytical workloads.

Do not immediately delete a Lakebase incident the moment it becomes
`completed`. CDC is replication, not a transactional "move." A
production version can apply a retention policy, for example keeping
recently completed incidents in Lakebase for 30 days before
archival/purge.

For the capstone, physical purging is optional.

### 3.4 Current vs historical reads

Use Lakebase for current operational queries:

``` text
"My active incidents"
"My assigned jobs"
"What is the current status of incident X?"
```

Use Delta for historical/list/analytical queries:

``` text
"Show all jobs I completed last year"
"Show my historical incidents"
"What is this handyman's performance on plumbing incidents?"
```

The backend should own this distinction. The frontend should not
directly decide which storage system to query.

------------------------------------------------------------------------

## 4. Lakebase Data Model

Only three operational tables are required for the MVP.

### 4.1 Users

A user is a registered client.

Recommended schema:

``` sql
CREATE TABLE IF NOT EXISTS bootcamp_students.maintops_users (
    id BIGINT PRIMARY KEY,

    email VARCHAR(64) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,

    first_name VARCHAR(64) NOT NULL,
    last_name VARCHAR(64) NOT NULL,
    date_of_birth DATE,
    phone VARCHAR(32),

    house VARCHAR(128),
    postal_code VARCHAR(32),
    city VARCHAR(64),
    state VARCHAR(64),
    country VARCHAR(64),

    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION
);
```

An unregistered visitor does not need a `maintops_users` row.

### 4.2 Handymen

The handyman table contains the latest operational profile plus
information extracted from the current CV.

``` sql
CREATE TABLE IF NOT EXISTS bootcamp_students.maintops_handymen (
    id BIGINT PRIMARY KEY,

    email VARCHAR(64) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,

    first_name VARCHAR(64) NOT NULL,
    last_name VARCHAR(64) NOT NULL,
    phone VARCHAR(32),

    house VARCHAR(128),
    postal_code VARCHAR(32),
    city VARCHAR(64),
    state VARCHAR(64),
    country VARCHAR(64),

    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,

    specialisations TEXT[],
    skills TEXT[],
    experience_summary TEXT,

    cv_path TEXT,
    cv_raw_text TEXT,

    completed_cases INTEGER NOT NULL DEFAULT 0,
    rating_avg NUMERIC(3,2) NOT NULL DEFAULT 0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    avg_price NUMERIC(10,2),

    CONSTRAINT chk_handyman_rating
        CHECK (rating_avg >= 0 AND rating_avg <= 5),

    CONSTRAINT chk_handyman_completed_cases
        CHECK (completed_cases >= 0),

    CONSTRAINT chk_handyman_rating_count
        CHECK (rating_count >= 0)
);
```

#### Specialisations vs skills vs experience

Keep all three.

**Specialisations** are broad controlled categories used for coarse
filtering.

Examples:

``` text
plumbing
electrical
heating_hvac
carpentry
painting
roofing
flooring
appliance_repair
locksmith
general_maintenance
```

They are stored as `TEXT[]` rather than a separate table for the MVP.

**Skills** are granular capabilities, for example:

``` text
pipe repair
leak detection
toilet installation
radiator repair
boiler maintenance
```

They are also stored as `TEXT[]`.

**experience_summary** contains richer CV-derived information describing
the handyman's professional experience.

The intended hierarchy is:

``` text
specialisations
      |
      v
coarse filtering
      |
      v
skills
      |
      v
specific job compatibility
      |
      v
historical performance / experience
      |
      v
feedback + workload + travel time
      |
      v
top candidates
```

The CV extraction logic should select specialisations from a predefined
controlled taxonomy rather than inventing arbitrary category names.

### 4.3 Incidents

``` sql
CREATE TABLE IF NOT EXISTS bootcamp_students.maintops_incidents (
    id BIGINT PRIMARY KEY,

    user_id BIGINT NOT NULL,
    handyman_id BIGINT,

    description TEXT NOT NULL,

    incident_type VARCHAR(64),
    urgency VARCHAR(32),

    recommended_handyman_ids BIGINT[],
    agent_reasoning TEXT,

    distance_km NUMERIC(10,2),
    travel_time_minutes NUMERIC(10,2),

    status VARCHAR(32) NOT NULL DEFAULT 'open',

    rating INTEGER,
    feedback TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    assigned_at TIMESTAMP,
    completed_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_incident_user
        FOREIGN KEY (user_id)
        REFERENCES bootcamp_students.maintops_users(id),

    CONSTRAINT fk_incident_handyman
        FOREIGN KEY (handyman_id)
        REFERENCES bootcamp_students.maintops_handymen(id),

    CONSTRAINT chk_incident_urgency
        CHECK (
            urgency IS NULL
            OR urgency IN ('low', 'medium', 'high', 'critical')
        ),

    CONSTRAINT chk_incident_status
        CHECK (
            status IN (
                'open',
                'recommended',
                'assigned',
                'in_progress',
                'completed',
                'cancelled'
            )
        ),

    CONSTRAINT chk_incident_rating
        CHECK (
            rating IS NULL
            OR rating BETWEEN 1 AND 5
        )
);
```

`recommended_handyman_ids` may be retained if recommendation outcomes
will later be analyzed. If only the selected handyman matters
operationally, it can be removed later.

------------------------------------------------------------------------

## 5. Authentication and Password Handling

Never store plaintext passwords and do not store reversibly encrypted
passwords.

Store only a password hash:

``` sql
password_hash TEXT NOT NULL
```

Use **Argon2id** in the application backend.

Registration:

``` text
plaintext password
      |
      v
Argon2id hash
      |
      v
password_hash stored in Lakebase
```

Login:

1.  Find the account by email.
2.  Retrieve `password_hash`.
3.  Use the Argon2 library's verification method against the submitted
    password.
4.  Never compare a newly generated hash string manually.
5.  Never log plaintext passwords.

The Argon2 encoded hash contains the salt and algorithm parameters, so a
separate `salt` database column is unnecessary.

Python example:

``` python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ph = PasswordHasher()

# Registration
password_hash = ph.hash(password)

# Login
try:
    ph.verify(stored_password_hash, submitted_password)
    authenticated = True
except VerifyMismatchError:
    authenticated = False
```

------------------------------------------------------------------------

## 6. Address Geocoding with Geoapify

Both clients and handymen provide an address during registration.

Geoapify provides the required **forward geocoding** capability, so no
second geolocation provider is required.

Flow:

``` text
registration / address update
        |
        v
address fields
        |
        v
Geoapify Geocoding
        |
        v
latitude + longitude
        |
        v
store coordinates in Lakebase
```

Coordinates must be persisted so the application does not geocode the
same address during every handyman search.

Both `maintops_users` and `maintops_handymen` therefore contain:

``` sql
latitude DOUBLE PRECISION,
longitude DOUBLE PRECISION
```

When an address changes, geocode it again and update the stored
coordinates.

The existing `house` field can contain the street/house address for the
MVP. A future version may separate `street` and `house_number`.

------------------------------------------------------------------------

## 7. Handyman Availability

Do not store a simple `is_available` boolean if availability is defined
by current workload. It can become inconsistent with the incident table.

Derive availability from active incidents.

Example MVP rule:

``` text
0 active incidents -> highly available
1 active incident  -> available
2 active incidents -> available but busy
3+ active incidents -> unavailable
```

An active incident is one whose status is, for example:

``` text
assigned
in_progress
```

Example query:

``` sql
SELECT
    h.id,
    h.first_name,
    h.last_name,
    COUNT(i.id) AS active_incidents
FROM bootcamp_students.maintops_handymen h
LEFT JOIN bootcamp_students.maintops_incidents i
    ON h.id = i.handyman_id
   AND i.status IN ('assigned', 'in_progress')
GROUP BY
    h.id,
    h.first_name,
    h.last_name
HAVING COUNT(i.id) < 3;
```

Current workload should also be a ranking signal: all else being equal,
a handyman with zero active incidents should rank above one with two.

The threshold is an MVP business rule and should be configurable rather
than deeply hard-coded.

------------------------------------------------------------------------

## 8. Handyman Search and Recommendation

Do not ask the LLM to inspect every handyman and invent a ranking.

The LLM should handle semantic understanding. Deterministic
application/data logic should handle filtering, routing, scoring, and
ranking.

### 8.1 Agent responsibilities

For a client request such as:

> Water is leaking from a pipe under my kitchen sink.

the agent should derive structured information such as:

``` text
incident_type = plumbing
urgency = high
required_specialisation = plumbing
required_skills = [pipe repair, leak detection]
```

It then creates/updates the incident and invokes the handyman-search
tool.

### 8.2 Candidate filtering

First query Lakebase for plausible candidates based on:

-   specialisation,
-   skills,
-   workload/availability,
-   other operational constraints.

Do not call Geoapify for every handyman in the database.

### 8.3 Geographic pre-filter

For a large candidate set, use the stored latitude/longitude to
calculate approximate straight-line distance, for example using the
Haversine formula.

Use this only as a cheap pre-filter.

Example:

``` text
500 matching handymen
        |
        v
approximate geographic distance
        |
        v
nearest ~20 plausible candidates
        |
        v
Geoapify Route Matrix
```

### 8.4 Geoapify Route Matrix

Use Geoapify Route Matrix for the shortlisted candidates to calculate
real road:

-   distance,
-   travel time.

Send multiple handyman origins and the client destination in one matrix
request instead of making one request per handyman.

Be careful with coordinate ordering expected by the API.

### 8.5 Ranking

Travel time should not dominate the recommendation. The primary
objective is finding someone likely to solve the problem correctly.

A reasonable initial scoring concept is:

``` text
Skill / incident match       35%
Similar successful cases     30%
Historical feedback          20%
Current workload             10%
Travel time                   5%
```

These weights are initial product assumptions and should be easy to
configure.

Do not ask the LLM to invent numeric scores. Calculate them
deterministically.

The final flow is:

``` text
client incident
      |
      v
LLM classification
      |
      v
Lakebase candidate filtering
      |
      v
approximate geographic pre-filter
      |
      v
Geoapify Route Matrix
      |
      + historical performance from Delta
      |
      v
deterministic scoring
      |
      v
top 3 handymen
      |
      v
client makes final selection
```

A good agent-facing abstraction is a tool such as:

``` python
find_handymen(incident_id: int)
```

Internally, that tool can:

1.  Read the incident.
2.  Read client coordinates.
3.  Find handymen with matching specialisations/skills.
4.  Exclude overloaded handymen.
5.  Pre-filter geographically.
6.  Call Geoapify Route Matrix.
7.  Retrieve historical performance features.
8.  Calculate candidate scores.
9.  Return the top three.

The LLM should not need to orchestrate every SQL query and HTTP request
individually.

------------------------------------------------------------------------

## 9. Incident Lifecycle

The intended lifecycle is:

``` text
open
  |
  v
recommended
  |
  v
assigned
  |
  v
in_progress
  |
  v
completed
```

`cancelled` is also permitted where appropriate.

Detailed workflow:

1.  Registered client submits a free-text problem description.
2.  Agent determines incident type and urgency.
3.  A new incident is created in Lakebase.
4.  Agent invokes the handyman-search logic.
5.  Three candidates are returned.
6.  Client selects one.
7.  `handyman_id` is written to the incident.
8.  Incident becomes `assigned`.
9.  Handyman sees it in their active-jobs page.
10. Handyman may move it to `in_progress`.
11. Handyman marks it `completed`.
12. Client provides `rating` and `feedback`.
13. The new outcome flows through CDC to historical Delta data.
14. Future historical-performance features incorporate the new outcome.

------------------------------------------------------------------------

## 10. Historical Incident Data and Spark

Spark should **not** be forced into the synchronous CV-registration
path.

The strongest Spark use case is the large historical incident dataset.

Create at least 1,000,000 synthetic historical incidents in the
lakehouse and use Spark to:

-   clean and validate records,
-   transform fields,
-   derive resolution times,
-   aggregate incidents by handyman and incident type,
-   calculate completion/success statistics,
-   calculate average ratings,
-   derive features useful for ranking.

Conceptual pipeline:

``` text
1M+ historical incidents
          |
          v
        Spark
          |
          + clean
          + transform
          + enrich
          + aggregate
          |
          v
handyman performance Delta table
          |
          v
handyman recommendation logic
```

Do not query all one million historical rows every time a user creates
an incident.

Precompute features such as:

``` text
handyman_id
incident_type
jobs_completed
successful_jobs
average_rating
average_resolution_time
```

These derived features are then queried during recommendation.

------------------------------------------------------------------------

## 11. CDC / Analytics Pipeline

New application incidents originate in Lakebase.

Changes are captured through CDC and processed into Delta.

``` text
Lakebase incidents
       |
      CDC
       |
       v
     Spark
       |
       v
Delta incident history
```

The historical Delta dataset can then feed:

-   handyman performance aggregation,
-   incident analytics,
-   recommendation-quality analysis,
-   application usage analytics.

Agent/token-cost analytics should also be supported so that future
API/application pricing can be estimated.

Useful metrics include:

-   requests per user,
-   input tokens,
-   output tokens,
-   total tokens,
-   estimated model cost,
-   cost per request,
-   cost per user,
-   incident resolution time,
-   selected recommendation rank,
-   handyman rating,
-   recommendation success.

------------------------------------------------------------------------

## 12. CV Upload and Processing

CVs provide the **High Variety** component of the capstone.

Supported input is expected to include PDFs and images.

### 12.1 Do not launch a Spark job for every CV

A handyman expects their profile to become usable shortly after
registration. Starting a distributed Spark pipeline for every single
upload is unnecessary and can create excessive job-trigger overhead.

The CV processing path should therefore be separate from the Spark
historical-data pipeline.

### 12.2 Intended flow

``` text
handyman registration
        |
        + normal profile fields
        |
        + CV upload
              |
              v
        UC Volume / raw file
              |
              v
      document parsing capability
              |
              v
      structured CV content
              |
              v
      AI extraction/classification
              |
              + specialisations
              + skills
              + experience summary
              |
              v
       update latest profile
       in maintops_handymen
```

The original CV remains in Unity Catalog storage. Lakebase stores only
the latest operational profile and references such as `cv_path` plus any
parsed text needed by the application.

### 12.3 Databricks document parsing

The intended Databricks capability is `ai_parse_document`.

For application-style synchronous processing, prefer the supported
document-parsing API/interface rather than starting a Spark job merely
to parse one CV.

Document parsing and information extraction are conceptually separate:

``` text
CV
 |
 v
ai_parse_document
 |
 v
parsed/structured document
 |
 v
AI extraction/classification
 |
 v
specialisations + skills + experience_summary
```

The extraction step must map specialisations to the application's
controlled taxonomy.

Do not assume that document parsing itself has already produced the
final handyman profile.

------------------------------------------------------------------------

## 13. Raw vs Operational CV Data

Keep storage responsibilities separate.

### Unity Catalog / Volume

Store:

-   original CV file,
-   potentially parsed/intermediate document data,
-   historical/raw artifacts where useful.

### Lakebase

Store the latest operational handyman representation:

-   personal/profile information,
-   coordinates,
-   specialisations,
-   skills,
-   experience summary,
-   CV path/reference,
-   current performance summary fields.

This follows the broader architectural rule:

``` text
LAKEBASE                     UNITY CATALOG / DELTA
--------                     ---------------------
latest user profile          raw CV files
latest handyman profile      parsed/historical CV artifacts
active/recent incidents      1M+ incident history
                             completed incident history
                             handyman performance features
                             analytics
```

------------------------------------------------------------------------

## 14. RAG Assistant for Unregistered Visitors

Unregistered visitors can ask questions about MaintOps before creating
an account.

Implementation plan:

1.  Build the unregistered-user frontend/main page.
2.  Create one or more documents containing the information the
    assistant is allowed to use.
3.  Chunk the documents.
4.  Store/index the chunks for vector retrieval.
5.  Create an agent/RAG assistant that answers unregistered-user
    questions using those documents.

This assistant is informational. It does not require an operational user
record.

------------------------------------------------------------------------

## 15. Frontend

### Unregistered visitor

Main page should provide:

-   explanation of MaintOps,
-   registration options,
-   RAG question/answer interface.

### Registered client

At minimum:

**Profile page** - view/update personal information, - view/update
address.

**Incidents page** - create incident, - see active incidents, - see
status, - view recommended handymen, - select handyman, - see
completed/history records, - rate completed work, - leave textual
feedback.

### Handyman

At minimum:

**Profile page** - view/update personal data, - upload/update CV, - see
extracted specialisations/skills/experience information.

**Assigned incidents page** - see assigned work, - sort/prioritize by
urgency, - mark work in progress, - mark work completed, - view client
feedback/statistics.

------------------------------------------------------------------------

## 16. Feedback Loop

Feedback is part of the recommendation system, not merely UI data.

``` text
recommend handyman
       |
       v
client selects
       |
       v
work completed
       |
       v
rating + textual feedback
       |
       v
historical Delta data
       |
       v
Spark-derived performance
       |
       v
future recommendations
```

Historical feedback and outcomes should affect future candidate ranking.

For the MVP, quantitative rating can directly influence derived
performance features. Textual feedback can later be summarized or
evaluated for richer signals.

------------------------------------------------------------------------

## 17. Recommended Responsibility Boundaries

### LLM / AI agent

Use for:

-   understanding free-text incident descriptions,
-   classifying incident type,
-   estimating urgency from the provided description,
-   identifying required specialisation/skills,
-   deciding which application tool to invoke,
-   generating user-facing explanations.

Do not use it for:

-   inventing numeric ranking scores,
-   manually calculating distances,
-   scanning all historical incidents on every request,
-   directly comparing thousands of handyman profiles one by one.

### Deterministic application/tool logic

Use for:

-   SQL filtering,
-   workload calculation,
-   Haversine/geographic pre-filtering,
-   Geoapify calls,
-   ranking formulas,
-   database writes,
-   authentication,
-   authorization.

### Spark

Use for:

-   large-scale historical incident processing,
-   cleaning/transformation,
-   aggregations,
-   feature generation,
-   CDC processing into Delta,
-   analytics pipelines.

Do not use Spark simply because a single user uploaded one CV.

### Lakebase

Use for:

-   current users,
-   current handyman profiles,
-   current/recent incident state,
-   transactional writes.

### Delta / Unity Catalog

Use for:

-   high-volume historical incidents,
-   long-term incident history,
-   derived historical features,
-   analytics,
-   raw/unstructured storage where appropriate.

### Geoapify

Use for:

-   forward geocoding: address -\> latitude/longitude,
-   Route Matrix: shortlisted handyman coordinates -\> road
    distance/travel time to client.

------------------------------------------------------------------------

## 18. Implementation Rules for Code Generation

When generating code for MaintOps, follow these rules unless the project
requirements are explicitly changed:

1.  Keep the Lakebase operational schema centered on exactly three core
    tables: `maintops_users`, `maintops_handymen`, and
    `maintops_incidents`.
2.  Do not create extra normalized tables for specialisations or skills
    unless explicitly requested.
3.  Store `specialisations` and `skills` as arrays in
    `maintops_handymen`.
4.  Treat specialisations as a controlled vocabulary.
5.  Never store plaintext passwords. Use `password_hash` and Argon2id.
6.  Never log passwords or secrets.
7.  Keep Geoapify API keys and other credentials in environment
    variables/secrets, never source code.
8.  Geocode addresses when a user/handyman registers or changes address,
    then persist coordinates.
9.  Do not repeatedly geocode unchanged addresses.
10. Derive handyman availability from current assigned/in-progress
    incidents rather than maintaining a redundant `is_available` flag.
11. Use specialisation/skills and workload to filter candidates before
    external routing calls.
12. For large candidate sets, geographically pre-filter before calling
    Geoapify.
13. Use Geoapify Route Matrix in batches rather than one API request per
    handyman where possible.
14. Travel time is a ranking feature, not the dominant criterion.
15. Let deterministic code calculate ranking scores.
16. Let the AI agent understand natural language and invoke tools; do
    not let it invent operational facts.
17. The client makes the final choice among recommended handymen.
18. Keep active/current state in Lakebase.
19. Keep high-volume historical data and analytics in Delta/Unity
    Catalog.
20. Propagate operational incident changes to Delta through CDC/Spark.
21. Do not immediately delete completed incidents from Lakebase merely
    because CDC copied them.
22. Do not put the 1M+ synthetic historical dataset into Lakebase.
23. Use Spark for workloads that justify distributed processing,
    particularly historical incident transformation/aggregation and CDC
    analytics.
24. Do not start a Spark job for each CV upload.
25. Store raw CV files outside Lakebase, preferably in a Unity Catalog
    Volume.
26. Parse/extract CV information during registration/update and write
    the latest structured result to `maintops_handymen`.
27. Preserve clear separation between document parsing and semantic
    field extraction.
28. RAG for unregistered visitors should use the MaintOps informational
    knowledge base and should not require registration.
29. All database access should be parameterized; do not construct SQL
    from raw user input.
30. Validate authorization in the backend: a client can only access
    their own private incident/profile data, and a handyman can only
    access incidents assigned to them unless a workflow explicitly
    requires otherwise.

------------------------------------------------------------------------

## 19. End-to-End Architecture

``` text
                         MAINTOPS FRONTEND
                               |
          +--------------------+--------------------+
          |                    |                    |
   Unregistered            Registered            Handyman
     visitor                 client
          |                    |                    |
       RAG Q&A             Create incident       Upload CV
                               |                    |
                               |                    v
                               |                UC Volume
                               |                    |
                               |                    v
                               |            Document parsing
                               |                    |
                               |                    v
                               |             AI extraction
                               |                    |
                               |                    v
                               |          latest handyman profile
                               |                    |
                               +---------+----------+
                                         |
                                         v
                                      LAKEBASE
                           users / handymen / incidents
                                         |
                   +---------------------+---------------------+
                   |                                           |
                   v                                           v
             AI AGENT / TOOLS                                CDC
                   |                                           |
        understand incident                                    v
        determine urgency                                    SPARK
        required skills                                        |
                   |                                           v
                   v                                  DELTA / UNITY CATALOG
           Candidate filtering                       historical incidents
                   |                                  1M+ synthetic data
                   |                                  analytics
                   +----------+                       performance features
                              |
                              v
                           Geoapify
                       Route Matrix API
                              |
                              v
                  deterministic ranking
                              |
                              v
                         TOP 3 HANDYMEN
                              |
                              v
                       CLIENT SELECTS ONE
                              |
                              v
                     HANDYMAN COMPLETES JOB
                              |
                              v
                       RATING + FEEDBACK
                              |
                              v
                     CDC / historical data
                              |
                              v
                    future recommendations
```

------------------------------------------------------------------------

## 20. MVP Scope

Prioritize a complete working vertical slice over unnecessary
normalization or infrastructure.

The MVP is successful when it demonstrates:

``` text
registration
    ->
address geocoding
    ->
handyman CV processing
    ->
incident creation
    ->
AI classification
    ->
candidate search
    ->
Geoapify routing
    ->
top-3 recommendation
    ->
client selection
    ->
handyman workflow
    ->
completion
    ->
feedback
    ->
CDC / Delta history
    ->
Spark historical-performance pipeline
```

Keep future sophistication out of the critical path unless required for
the capstone.
