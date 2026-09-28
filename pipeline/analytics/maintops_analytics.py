"""MaintOps analytics — Lakeflow Declarative Pipeline over Lakebase Change Data Feed.

Sources (written by Lakebase CDF, ~15 s behind the app):
  lb_app_events_history   every agent request, tool call, Geoapify call, UI action and guardrail decision
  lb_incidents_history    every insert / update / delete of an incident

Streaming tables read the CDF history incrementally (each change processed once; skipChangeCommits ignores
the history rewrites Lakebase does after a schema migration). Materialized views aggregate them into
analytics-ready tables. Expectations enforce data quality and are visible in the pipeline's event log.
"""

import dlt
from pyspark.sql import functions as F

SOURCE = "bootcamp_students.maintops"
WRITE_ACTIONS = ["create_incident", "assign_handyman", "cancel_incident", "submit_feedback", "update_job_status"]
# Assumed list price of the agent's LLM (USD per token); the cost columns are estimates for pricing decisions
PRICE_IN, PRICE_OUT = 3.0 / 1_000_000, 15.0 / 1_000_000


def _changes(table: str):
    """Row images after each change (inserts and update post-images), plus deletes."""
    return (spark.readStream.option("skipChangeCommits", "true").table(f"{SOURCE}.{table}")
            .where("_pg_change_type IN ('insert', 'update_postimage', 'delete')"))


# ─────────────────────────────────────────────────────────────
# Silver: cleaned event streams
# ─────────────────────────────────────────────────────────────


@dlt.table(name="analytics_events", comment="App events from Lakebase CDF, validated and deduplicated")
@dlt.expect_or_drop("known_event_type",
                    "event_type IN ('agent_request', 'tool_call', 'api_call', 'ui_action', 'guardrail')")
@dlt.expect_or_drop("has_timestamp", "created_at IS NOT NULL")
@dlt.expect("latency_not_negative", "latency_ms IS NULL OR latency_ms >= 0")
def analytics_events():
    return (_changes("lb_app_events_history")
            .where("_pg_change_type = 'insert'")                  # events are append-only in Lakebase
            .select("id", F.col("created_at").cast("timestamp").alias("created_at"),   # CDF delivers TIMESTAMP_NTZ
                    "event_type", "name", "request_id", "user_id", "incident_id", "success",
                    "error", "latency_ms", "input_tokens", "output_tokens", "details",
                    F.col("_timestamp").cast("timestamp").alias("captured_at"),
                    F.get_json_object("details", "$.role").alias("role"))
            .withWatermark("created_at", "1 day")
            .dropDuplicates(["id"]))


@dlt.table(name="analytics_incident_changes", comment="Every incident change captured by Lakebase CDF")
@dlt.expect_or_drop("has_id", "id IS NOT NULL")
@dlt.expect("valid_status", "change_type = 'delete' OR status IN "
                            "('open', 'recommended', 'assigned', 'in_progress', 'completed', 'cancelled')")
def analytics_incident_changes():
    return _changes("lb_incidents_history").select(
        "id", F.col("_pg_change_type").alias("change_type"), F.col("_timestamp").cast("timestamp").alias("changed_at"),
        "status", "incident_type", "urgency", "handyman_user_id", "recommended_handyman_ids", "rating")


# ─────────────────────────────────────────────────────────────
# Gold: metrics (materialized views)
# ─────────────────────────────────────────────────────────────


@dlt.table(name="analytics_agent_requests_hourly", comment="Manny requests per hour and role: volume, latency, tokens, estimated cost")
def analytics_agent_requests_hourly():
    return (dlt.read("analytics_events").where("event_type = 'agent_request'")
            .groupBy(F.date_trunc("hour", "created_at").alias("hour"), F.coalesce("role", F.lit("unknown")).alias("role"))
            .agg(F.count("*").alias("requests"),
                 F.countDistinct("user_id").alias("distinct_users"),
                 F.round(F.avg("latency_ms")).alias("avg_latency_ms"),
                 F.percentile_approx("latency_ms", 0.95).alias("p95_latency_ms"),
                 F.sum("input_tokens").alias("input_tokens"),
                 F.sum("output_tokens").alias("output_tokens"),
                 F.round(F.sum(F.col("input_tokens") * PRICE_IN + F.col("output_tokens") * PRICE_OUT), 4)
                  .alias("estimated_cost_usd")))


@dlt.table(name="analytics_tool_usage", comment="Agent tool calls: volume, success rate, latency per tool")
def analytics_tool_usage():
    return (dlt.read("analytics_events").where("event_type = 'tool_call'")
            .groupBy("name")
            .agg(F.count("*").alias("calls"),
                 F.round(F.avg(F.col("success").cast("int")), 3).alias("success_rate"),
                 F.round(F.avg("latency_ms")).alias("avg_latency_ms"),
                 F.max("created_at").alias("last_call_at")))


@dlt.table(name="analytics_api_usage_daily", comment="Third-party (Geoapify) API calls per day: volume, failure rate, latency")
def analytics_api_usage_daily():
    return (dlt.read("analytics_events").where("event_type = 'api_call'")
            .groupBy(F.to_date("created_at").alias("day"), "name")
            .agg(F.count("*").alias("calls"),
                 F.sum((~F.col("success")).cast("int")).alias("failures"),
                 F.round(F.avg((~F.col("success")).cast("int")), 3).alias("failure_rate"),
                 F.percentile_approx("latency_ms", 0.95).alias("p95_latency_ms")))


@dlt.table(name="analytics_guardrails_daily", comment="Guardrail decisions per day (injection refusals, emergencies, ungrounded numbers)")
def analytics_guardrails_daily():
    return (dlt.read("analytics_events").where("event_type = 'guardrail'")
            .groupBy(F.to_date("created_at").alias("day"), "name").agg(F.count("*").alias("events")))


@dlt.table(name="analytics_write_actions", comment="Write actions per user and day, split by channel (agent vs UI buttons)")
def analytics_write_actions():
    return (dlt.read("analytics_events")
            .where(F.col("event_type").isin("tool_call", "ui_action") & F.col("name").isin(WRITE_ACTIONS))
            .withColumn("channel", F.when(F.col("event_type") == "tool_call", "agent").otherwise("ui"))
            .groupBy(F.to_date("created_at").alias("day"), "user_id", "channel", "name")
            .agg(F.count("*").alias("actions"), F.sum(F.col("success").cast("int")).alias("succeeded")))


@dlt.table(name="analytics_feature_usage", comment="Most-used features: every event type + name, all time")
def analytics_feature_usage():
    return (dlt.read("analytics_events").groupBy("event_type", "name")
            .agg(F.count("*").alias("uses"), F.countDistinct("user_id").alias("distinct_users"))
            .orderBy(F.desc("uses")))


@dlt.table(name="analytics_incident_activity_daily", comment="Incident record creation / update / deletion trends per day and status")
def analytics_incident_activity_daily():
    return (dlt.read("analytics_incident_changes")
            .groupBy(F.to_date("changed_at").alias("day"), "change_type", "status")
            .agg(F.count("*").alias("changes"), F.countDistinct("id").alias("incidents")))


@dlt.table(name="analytics_recommendation_rank", comment="Which recommended candidate clients choose (rank 1–3)")
def analytics_recommendation_rank():
    ids = F.split(F.regexp_replace("recommended_handyman_ids", r"[{}\s]", ""), ",")
    return (dlt.read("analytics_incident_changes")
            .where("change_type = 'update_postimage' AND status = 'assigned' AND recommended_handyman_ids IS NOT NULL")
            .dropDuplicates(["id"])
            .withColumn("chosen_rank", F.array_position(ids, F.col("handyman_user_id").cast("string")))
            .groupBy("chosen_rank").agg(F.count("*").alias("assignments")))
