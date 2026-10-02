"""Shared logic for the MaintOps Spark pipeline (batch notebooks 10–16 and the live stream 20).

Keeping the cleaning rules, scorecard maths and Lakebase upserts in one module guarantees the batch
backfill and the real-time stream produce identical results.
"""

import json
import time
from datetime import datetime, timezone

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

# ─────────────────────────────────────────────────────────────
# Names
# ─────────────────────────────────────────────────────────────
CATALOG = "bootcamp_students"
SCHEMA = "maintops"


def fq(table: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{table}"


VOLUME_ROOT = "/".join(("/Volumes", CATALOG, SCHEMA, "maintops_docs"))
RAW_DIR = f"{VOLUME_ROOT}/raw/incidents"
CHECKPOINT_DIR = f"{VOLUME_ROOT}/_checkpoints"
CV_DIR = f"{VOLUME_ROOT}/handyman_cvs"

T_SOURCE = fq("synth_incidents")
T_BRONZE = fq("bronze_incidents")
T_SILVER = fq("silver_incidents")
T_QUARANTINE = fq("quarantine_incidents")
T_GOLD_PERFORMANCE = fq("gold_handyman_performance")
T_GOLD_FEEDBACK = fq("gold_handyman_feedback")
T_REVIEW_SENTIMENT = fq("review_sentiment")
T_CV_PARSED = fq("cv_parsed")
T_RUNS = fq("pipeline_runs")
T_LATENCY = fq("latency_metrics")
T_LB_INCIDENTS = fq("lb_incidents_history")
T_HANDYMAN_DETAILS = fq("synth_handyman_details")

SUMMARY_MODEL = "databricks-claude-haiku-4-5"
SUMMARY_MIN_REVIEWS = 3
SUMMARY_REFRESH_MINUTES = 10      # the live stream refreshes a handyman's summary at most this often
RECENT_REVIEWS = 20               # sentiment and summaries look at each handyman's latest reviews

SPECIALISATIONS = ["plumbing", "electrical", "heating_hvac", "carpentry", "painting",
                   "roofing", "flooring", "appliance_repair", "locksmith", "general_maintenance"]
URGENCIES = ["low", "medium", "high", "critical"]
STATUSES = ["open", "recommended", "assigned", "in_progress", "completed", "cancelled"]

# Incident columns in Lakebase order; silver adds _source and _processed_at
INCIDENT_COLUMNS = ["id", "reported_by_user_id", "handyman_user_id", "description", "incident_type", "urgency",
                    "recommended_handyman_ids", "agent_reasoning", "distance_km", "travel_time_minutes",
                    "status", "rating", "feedback", "created_at", "assigned_at", "completed_at", "updated_at"]
TIMESTAMP_COLUMNS = ["created_at", "assigned_at", "completed_at", "updated_at"]

# ─────────────────────────────────────────────────────────────
# Table setup
# ─────────────────────────────────────────────────────────────


def ensure_tables(spark: SparkSession) -> None:
    """Create the pipeline's Delta tables if they do not exist yet."""
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {T_SILVER} (
          id BIGINT, reported_by_user_id BIGINT, handyman_user_id BIGINT, description STRING,
          incident_type STRING, urgency STRING, recommended_handyman_ids ARRAY<BIGINT>, agent_reasoning STRING,
          distance_km DECIMAL(10,2), travel_time_minutes DECIMAL(10,2), status STRING, rating INT, feedback STRING,
          created_at TIMESTAMP, assigned_at TIMESTAMP, completed_at TIMESTAMP, updated_at TIMESTAMP,
          _source STRING COMMENT 'batch_export | lakebase_cdf',
          _processed_at TIMESTAMP
        ) CLUSTER BY (handyman_user_id, incident_type)
        COMMENT 'Validated, deduplicated incidents: historical export + live Lakebase changes'""")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {T_QUARANTINE} (
          id STRING, reasons ARRAY<STRING>, record STRING, _source STRING, _source_file STRING,
          _quarantined_at TIMESTAMP
        ) COMMENT 'Incident records rejected by the silver validation rules, with reasons'""")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {T_RUNS} (
          run_id STRING, step STRING, started_at TIMESTAMP, finished_at TIMESTAMP, duration_s DOUBLE,
          rows_in BIGINT, rows_out BIGINT, rows_rejected BIGINT, checks STRING, status STRING
        ) COMMENT 'One row per pipeline step run: volumes, duration and data-quality checks'""")


# ─────────────────────────────────────────────────────────────
# Run log
# ─────────────────────────────────────────────────────────────


def rows_processed(query) -> int:
    """Total input rows of a finished streaming query (progress entries can report None)."""
    total = 0
    for p in query.recentProgress:
        n = p["numInputRows"] if isinstance(p, dict) else getattr(p, "numInputRows", None)
        total += n or 0
    return total


def log_step(spark: SparkSession, run_id: str, step: str, started: float, rows_in: int = None,
             rows_out: int = None, rows_rejected: int = None, checks: dict = None) -> None:
    """Record a step in pipeline_runs; raise if any data-quality check failed (the row is logged first)."""
    checks = checks or {}
    failed = [name for name, ok in checks.items() if not ok]
    finished = time.time()
    spark.createDataFrame([{
        "run_id": run_id, "step": step,
        "started_at": datetime.fromtimestamp(started, timezone.utc),
        "finished_at": datetime.fromtimestamp(finished, timezone.utc),
        "duration_s": round(finished - started, 1),
        "rows_in": rows_in, "rows_out": rows_out, "rows_rejected": rows_rejected,
        "checks": json.dumps({k: bool(v) for k, v in checks.items()}),
        "status": "failed" if failed else "ok",
    }], schema=spark.table(T_RUNS).schema).write.mode("append").saveAsTable(T_RUNS)
    print(f"[{step}] in={rows_in} out={rows_out} rejected={rows_rejected} "
          f"{finished - started:.0f}s checks={checks}")
    if failed:
        raise AssertionError(f"{step}: data-quality checks failed: {failed}")


# ─────────────────────────────────────────────────────────────
# Cleaning (silver rules)
# ─────────────────────────────────────────────────────────────


def _parse_pg_array(col):
    """Parse Postgres array text into ARRAY<BIGINT>.

    Lakebase CDF delivers arrays as text, e.g. {1,2,3}.
    """
    inner = F.regexp_replace(col, r"[{}\s]", "")
    return F.when(col.isNull(), None).when(inner == "", F.array().cast("array<bigint>")) \
            .otherwise(F.split(inner, ",").cast("array<bigint>"))


def _try_cast(col: str, sql_type: str):
    """The column as text, trimmed, cast to sql_type; NULL when it does not parse."""
    return F.expr(f"try_cast(trim(cast({col} AS STRING))" f" AS {sql_type})")


def clean_incidents(df: DataFrame, source: str) -> tuple[DataFrame, DataFrame]:
    """Normalise raw incident rows and split them into (valid, rejected).

    Accepts bronze rows (all scalar fields as strings) or Lakebase CDF rows (typed, arrays as text).
    Fixable issues are repaired (whitespace, case); rule violations are rejected with reason codes.
    """
    s = lambda c: F.trim(F.col(c).cast("string"))
    ids = F.col("recommended_handyman_ids")
    rec_ids = _parse_pg_array(ids) if dict(df.dtypes).get("recommended_handyman_ids") == "string" \
        else ids.cast("array<bigint>")

    typed = df.select(
        _try_cast("id", "BIGINT").alias("id"),
        _try_cast("reported_by_user_id", "BIGINT").alias("reported_by_user_id"),
        _try_cast("handyman_user_id", "BIGINT").alias("handyman_user_id"),
        F.regexp_replace(s("description"), r"\s+", " ").alias("description"),
        F.lower(s("incident_type")).alias("incident_type"),
        F.lower(s("urgency")).alias("urgency"),
        rec_ids.alias("recommended_handyman_ids"),
        s("agent_reasoning").alias("agent_reasoning"),
        _try_cast("distance_km", "DECIMAL(10,2)").alias("distance_km"),
        _try_cast("travel_time_minutes", "DECIMAL(10,2)").alias("travel_time_minutes"),
        F.lower(s("status")).alias("status"),
        _try_cast("rating", "INT").alias("rating"),
        F.nullif(s("feedback"), F.lit("")).alias("feedback"),
        *[F.expr(f"try_to_timestamp(trim(cast({c} AS STRING)))").alias(c) for c in TIMESTAMP_COLUMNS],
        *[s(c).alias(f"_raw_{c}") for c in TIMESTAMP_COLUMNS],
        F.to_json(F.struct(*[F.col(c) for c in df.columns if not c.startswith("_")])).alias("_record"),
        (F.col("_source_file") if "_source_file" in df.columns else F.lit(None).cast("string")).alias("_source_file"),
    )

    c = F.col
    rules = [
        ("missing_id", c("id").isNull()),
        ("missing_reporter", c("reported_by_user_id").isNull()),
        ("empty_description", c("description").isNull() | (F.length("description") < 5)),
        ("description_too_long", F.length("description") > 2000),
        ("invalid_incident_type", c("incident_type").isNotNull() & ~c("incident_type").isin(SPECIALISATIONS)),
        ("invalid_urgency", c("urgency").isNotNull() & ~c("urgency").isin(URGENCIES)),
        ("invalid_status", c("status").isNull() | ~c("status").isin(STATUSES)),
        ("bad_timestamp", c("created_at").isNull() |
                          (c("_raw_assigned_at").isNotNull() & c("assigned_at").isNull()) |
                          (c("_raw_completed_at").isNotNull() & c("completed_at").isNull())),
        ("timestamp_order", (c("assigned_at") < c("created_at")) | (c("completed_at") < c("assigned_at")) |
                            (c("completed_at").isNotNull() & c("assigned_at").isNull())),
        ("negative_distance", (c("distance_km") < 0) | (c("travel_time_minutes") < 0)),
        ("rating_out_of_range", c("rating").isNotNull() & ~c("rating").between(1, 5)),
        ("feedback_on_unfinished_job", (c("rating").isNotNull() | c("feedback").isNotNull()) &
                                       (c("status") != "completed")),
        ("handyman_missing", c("status").isin("assigned", "in_progress", "completed") &
                             c("handyman_user_id").isNull()),
    ]
    reasons = F.filter(F.array(*[F.when(cond, F.lit(code)) for code, cond in rules]), lambda x: x.isNotNull())
    checked = typed.withColumn("_reasons", reasons)

    now = F.current_timestamp()
    valid = (checked.where(F.size("_reasons") == 0)
             .withColumn("updated_at", F.coalesce("updated_at", "created_at"))
             .select(*INCIDENT_COLUMNS, F.lit(source).alias("_source"), now.alias("_processed_at")))
    rejected = checked.where(F.size("_reasons") > 0).select(
        F.col("id").cast("string").alias("id"), F.col("_reasons").alias("reasons"), F.col("_record").alias("record"),
        F.lit(source).alias("_source"), "_source_file", now.alias("_quarantined_at"))
    return valid, rejected


def _run_merge(spark: SparkSession, statement: str, attempts: int = 4) -> None:
    """Run a MERGE, retrying on Delta write conflicts (the live stream and its summary sweep can overlap)."""
    for attempt in range(1, attempts + 1):
        try:
            spark.sql(statement)
            return
        except Exception as e:
            if "Concurrent" not in str(e) or attempt == attempts:
                raise
            time.sleep(2 * attempt)


def latest_per_id(df: DataFrame, order_cols: list[str]) -> DataFrame:
    """Keep one row per incident id: the one that sorts last by order_cols."""
    w = Window.partitionBy("id").orderBy(*[F.col(c).desc_nulls_last() for c in order_cols])
    return df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn")


def merge_into_silver(spark: SparkSession, valid: DataFrame) -> None:
    """Upsert validated incidents; an older version never overwrites a newer one."""
    latest_per_id(valid, ["updated_at"]).createOrReplaceTempView("_silver_updates")
    spark.sql(f"""
        MERGE INTO {T_SILVER} t USING _silver_updates s ON t.id = s.id
        WHEN MATCHED AND s.updated_at >= t.updated_at THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *""")


# ─────────────────────────────────────────────────────────────
# Gold: handyman scorecards
# ─────────────────────────────────────────────────────────────


def compute_performance(silver: DataFrame) -> DataFrame:
    """Scorecard per (handyman, incident_type) plus an overall row with incident_type = 'all'."""
    hours = (F.unix_timestamp("completed_at") - F.unix_timestamp("assigned_at")) / 3600
    base = silver.where(F.col("handyman_user_id").isNotNull() & F.col("incident_type").isNotNull())
    return (base.rollup("handyman_user_id", "incident_type")
            .agg(F.count_if(F.col("status") == "completed").alias("jobs_completed"),
                 F.count_if(F.col("status") == "cancelled").alias("jobs_cancelled"),
                 F.count_if(F.col("status").isin("assigned", "in_progress")).alias("jobs_active"),
                 F.count("rating").alias("rated_jobs"),
                 F.count_if(F.col("rating") >= 4).alias("successful_jobs"),
                 F.avg("rating").alias("_avg_rating"),
                 F.avg(F.when(F.col("status") == "completed", hours)).alias("_avg_hours"),
                 F.avg("travel_time_minutes").alias("_avg_travel"),
                 F.max("completed_at").alias("last_job_at"),
                 F.grouping("incident_type").alias("_is_total"))
            .where(F.col("handyman_user_id").isNotNull())
            .select("handyman_user_id",
                    F.when(F.col("_is_total") == 1, "all").otherwise(F.col("incident_type")).alias("incident_type"),
                    "jobs_completed", "jobs_cancelled", "jobs_active", "rated_jobs", "successful_jobs",
                    F.round(F.col("successful_jobs") / F.nullif(F.col("rated_jobs"), F.lit(0)), 4)
                     .cast("decimal(5,4)").alias("success_rate"),
                    F.round("_avg_rating", 2).cast("decimal(3,2)").alias("avg_rating"),
                    F.round("_avg_hours", 2).cast("decimal(10,2)").alias("avg_resolution_hours"),
                    F.round("_avg_travel", 2).cast("decimal(10,2)").alias("avg_travel_minutes"),
                    "last_job_at",
                    F.current_timestamp().alias("computed_at")))


def merge_performance(spark: SparkSession, perf: DataFrame) -> None:
    perf.createOrReplaceTempView("_perf_updates")
    _run_merge(spark, f"""
        MERGE INTO {T_GOLD_PERFORMANCE} t USING _perf_updates s
        ON t.handyman_user_id = s.handyman_user_id AND t.incident_type = s.incident_type
        WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")


# ─────────────────────────────────────────────────────────────
# Gold: review sentiment and summaries
# ─────────────────────────────────────────────────────────────


def update_sentiment_cache(spark: SparkSession, reviews: DataFrame) -> int:
    """Score each distinct review text once with ai_analyze_sentiment; returns how many were new."""
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {T_REVIEW_SENTIMENT}
                  (text_hash STRING, feedback STRING, sentiment STRING, scored_at TIMESTAMP)
                  COMMENT 'ai_analyze_sentiment result per distinct review text'""")
    new = (reviews.select(F.sha2("feedback", 256).alias("text_hash"), "feedback").dropDuplicates(["text_hash"])
           .join(spark.table(T_REVIEW_SENTIMENT).select("text_hash"), "text_hash", "left_anti"))
    n = new.count()
    if n:
        (new.withColumn("sentiment", F.expr("ai_analyze_sentiment(feedback)"))
            .withColumn("scored_at", F.current_timestamp())
            .write.mode("append").saveAsTable(T_REVIEW_SENTIMENT))
    return n


def compute_feedback(spark: SparkSession, silver: DataFrame) -> DataFrame:
    """Per handyman: review counts and sentiment over their latest reviews (no summaries yet)."""
    reviews = silver.where(F.col("handyman_user_id").isNotNull() & F.col("feedback").isNotNull())
    update_sentiment_cache(spark, reviews)
    scored = reviews.withColumn("text_hash", F.sha2("feedback", 256)) \
                    .join(spark.table(T_REVIEW_SENTIMENT).select("text_hash", "sentiment"), "text_hash", "left")
    w = Window.partitionBy("handyman_user_id").orderBy(F.col("completed_at").desc_nulls_last(), F.col("id").desc())
    recent = scored.withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") <= RECENT_REVIEWS)
    totals = reviews.groupBy("handyman_user_id").agg(F.count("*").alias("review_count"),
                                                    F.max("completed_at").alias("last_review_at"))
    recent_stats = recent.groupBy("handyman_user_id").agg(
        F.count("*").alias("recent_review_count"),
        F.avg(F.when(F.col("sentiment") == "positive", 1).otherwise(0)).alias("_pos"),
        F.avg(F.when(F.col("sentiment") == "negative", 1).otherwise(0)).alias("_neg"),
        F.concat_ws("\n", F.collect_list(F.concat(F.lit("- "), F.col("rating").cast("string"),
                                                  F.lit("★: "), F.col("feedback")))).alias("_recent_reviews"))
    return (totals.join(recent_stats, "handyman_user_id")
            .select("handyman_user_id", "review_count", "recent_review_count",
                    F.round("_pos", 4).cast("decimal(5,4)").alias("positive_share"),
                    F.round("_neg", 4).cast("decimal(5,4)").alias("negative_share"),
                    F.round(F.col("_pos") - F.col("_neg"), 4).cast("decimal(5,4)").alias("sentiment_score"),
                    "last_review_at", "_recent_reviews",
                    F.current_timestamp().alias("computed_at")))


SUMMARY_PROMPT = ("You summarise client reviews of a handyman for other clients. In at most 25 words of plain text "
                  "(no markdown, no headings, no lists), state the main strengths and any recurring complaint. "
                  "Use only what the reviews say; no preamble.\n"
                  "Reviews (rating: text):\n")


def plain_text(col):
    """Strip markdown symbols and collapse whitespace, in case the model formats anyway."""
    return F.trim(F.regexp_replace(F.regexp_replace(col, r"[*#_`]", ""), r"\s+", " "))


def refresh_summaries(spark: SparkSession, features: DataFrame, throttle: bool, generate: bool = True) -> DataFrame:
    """Add review_summary: regenerate it with ai_query for handymen with new reviews.

    throttle=True (live stream) skips handymen whose summary is younger than SUMMARY_REFRESH_MINUTES;
    they keep their previous summary and get refreshed later. generate=False never calls the LLM (the live
    fast path): previous summaries are kept and the stream's summary sweep regenerates them separately.
    """
    previous = spark.table(T_GOLD_FEEDBACK).select(
        "handyman_user_id", F.col("review_summary").alias("_prev_summary"),
        F.col("summary_updated_at").alias("_prev_summary_at"),
        F.col("summary_review_at").alias("_prev_review_at"))
    f = features.join(previous, "handyman_user_id", "left")
    needs = (F.col("recent_review_count") >= SUMMARY_MIN_REVIEWS) & (
        F.col("_prev_summary").isNull() | (F.col("last_review_at") > F.col("_prev_review_at")))
    if throttle:
        needs = needs & (F.col("_prev_summary_at").isNull() |
                         (F.col("_prev_summary_at") < F.current_timestamp() - F.expr(
                             f"INTERVAL {SUMMARY_REFRESH_MINUTES} MINUTES")))
    f = f.withColumn("_needs", needs if generate else F.lit(False))
    todo = f.where("_needs").withColumn("_ai", F.expr(f"""
        ai_query('{SUMMARY_MODEL}',
                 concat('{SUMMARY_PROMPT}', _recent_reviews),
                 failOnError => false)"""))
    done = todo.select("handyman_user_id", plain_text(F.col("_ai.result")).alias("_new_summary"))
    return (f.join(done, "handyman_user_id", "left")
            .withColumn("review_summary", F.coalesce("_new_summary", "_prev_summary"))
            .withColumn("summary_updated_at", F.when(F.col("_new_summary").isNotNull(), F.current_timestamp())
                        .otherwise(F.col("_prev_summary_at")))
            .withColumn("summary_review_at", F.when(F.col("_new_summary").isNotNull(), F.col("last_review_at"))
                        .otherwise(F.col("_prev_review_at")))
            .select("handyman_user_id", "review_count", "recent_review_count", "positive_share",
                    "negative_share", "sentiment_score", "review_summary", "summary_updated_at",
                    "summary_review_at", "last_review_at", "computed_at"))


def ensure_feedback_table(spark: SparkSession) -> None:
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {T_GOLD_FEEDBACK} (
          handyman_user_id BIGINT, review_count BIGINT, recent_review_count BIGINT,
          positive_share DECIMAL(5,4), negative_share DECIMAL(5,4), sentiment_score DECIMAL(5,4),
          review_summary STRING, summary_updated_at TIMESTAMP, summary_review_at TIMESTAMP,
          last_review_at TIMESTAMP, computed_at TIMESTAMP
        ) COMMENT 'Per-handyman review sentiment and LLM summary of their latest reviews'""")


def merge_feedback(spark: SparkSession, fb: DataFrame, stage_table: str = "_stage_feedback_batch") -> None:
    """Materialise first: ai_query is non-deterministic and MERGE may read its source twice.
    The batch job and the live stream use different stage tables so they never collide."""
    stage = fq(stage_table)
    fb.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(stage)
    _run_merge(spark, f"""
        MERGE INTO {T_GOLD_FEEDBACK} t USING {stage} s ON t.handyman_user_id = s.handyman_user_id
        WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")


# ─────────────────────────────────────────────────────────────
# Lakebase
# ─────────────────────────────────────────────────────────────
PERFORMANCE_COLUMNS = ["handyman_user_id", "incident_type", "jobs_completed", "jobs_cancelled", "jobs_active",
                       "rated_jobs", "successful_jobs", "success_rate", "avg_rating", "avg_resolution_hours",
                       "avg_travel_minutes", "last_job_at", "computed_at"]
FEEDBACK_COLUMNS = ["handyman_user_id", "review_count", "recent_review_count", "positive_share", "negative_share",
                    "sentiment_score", "review_summary", "summary_updated_at", "last_review_at", "computed_at"]


def upsert_lakebase(pg_url: str, data, table: str, keys: list[str], cols: list[str]) -> int:
    """Upsert rows into maintops.<table>.

    COPY into a temp table, then INSERT ... ON CONFLICT DO UPDATE.
    data is a DataFrame (streamed from the cluster) or a list of Rows already on the driver
    (the live stream's fast path, which pushes scorecards before writing anything to Delta).
    """
    import psycopg

    rows = data.select(*cols).toLocalIterator() if isinstance(data, DataFrame) \
        else (tuple(r[c] for c in cols) for r in data)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys)
    n = 0
    with psycopg.connect(pg_url, connect_timeout=30) as conn, conn.cursor() as cur:
        col_list, key_list = ", ".join(cols), ", ".join(keys)
        cur.execute(f"""
            CREATE TEMP TABLE _stage (LIKE maintops.{table} INCLUDING DEFAULTS)
            ON COMMIT DROP""")
        with cur.copy(f"""
            COPY _stage ({col_list}) FROM STDIN""") as cp:
            for row in rows:
                cp.write_row(tuple(row))
                n += 1
        cur.execute(f"""
            INSERT INTO maintops.{table} ({col_list})
            SELECT {col_list} FROM _stage
            ON CONFLICT ({key_list}) DO UPDATE SET {updates}""")
        if table == "handyman_performance":
            # Keep the headline numbers on handyman_details consistent with the scorecards
            cur.execute("""
                UPDATE maintops.handyman_details d
                SET completed_cases = s.jobs_completed,
                    rating_count    = s.rated_jobs,
                    rating_avg      = COALESCE(s.avg_rating, 0)
                FROM _stage s
                WHERE s.incident_type = 'all' AND s.handyman_user_id = d.user_id
                  AND (d.completed_cases, d.rating_count, d.rating_avg)
                      IS DISTINCT FROM (s.jobs_completed, s.rated_jobs, COALESCE(s.avg_rating, 0))""")
        conn.commit()
    return n
