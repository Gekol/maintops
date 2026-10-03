# Lakebase data model

The operational Postgres schema `maintops` as it is deployed: rows, keys, constraints, indexes and triggers.

_Exported 2026-10-03 13:47 UTC by `evidence/export_evidence.py`._

## Tables

Source: `pg_catalog` (row counts are exact)

| table_name | rows | pk | fk | checks | indexes | triggers |
|---|---|---|---|---|---|---|
| app_events | 12,843 | 1 | 2 | 2 | 5 | 0 |
| handyman_details | 10,025 | 1 | 1 | 5 | 3 | 2 |
| handyman_feedback | 9,289 | 1 | 1 | 1 | 1 | 1 |
| handyman_performance | 23,172 | 1 | 1 | 4 | 1 | 1 |
| incident_trips | 1 | 1 | 2 | 4 | 2 | 2 |
| incidents | 44,777 | 1 | 2 | 13 | 5 | 2 |
| schema_migrations | 6 | 1 | 0 | 0 | 1 | 0 |
| users | 110,049 | 1 | 0 | 1 | 4 | 1 |

## CHECK constraints

Source: `pg_constraint`

| table_name | constraint_name | definition |
|---|---|---|
| app_events | chk_event_numbers | CHECK ((((latency_ms IS NULL) OR (latency_ms >= 0)) AND ((input_tokens IS NULL) OR (input_tokens >= 0)) AND ((output_tokens IS NULL) OR (out |
| app_events | chk_event_type | CHECK (((event_type)::text = ANY ((ARRAY['agent_request'::character varying, 'tool_call'::character varying, 'api_call'::character varying,  |
| handyman_details | chk_handyman_avg_price | CHECK (((avg_price IS NULL) OR (avg_price >= (0)::numeric))) |
| handyman_details | chk_handyman_completed_cases | CHECK ((completed_cases >= 0)) |
| handyman_details | chk_handyman_rating | CHECK (((rating_avg >= (0)::numeric) AND (rating_avg <= (5)::numeric))) |
| handyman_details | chk_handyman_rating_count | CHECK ((rating_count >= 0)) |
| handyman_details | chk_handyman_specialisations | CHECK (((specialisations IS NULL) OR (specialisations <@ ARRAY['plumbing'::text, 'electrical'::text, 'heating_hvac'::text, 'carpentry'::text |
| handyman_feedback | chk_feedback_shares | CHECK ((((positive_share IS NULL) OR ((positive_share >= (0)::numeric) AND (positive_share <= (1)::numeric))) AND ((negative_share IS NULL)  |
| handyman_performance | chk_performance_billing | CHECK (((billed_jobs >= 0) AND (hours_billed >= (0)::numeric) AND (amount_billed >= (0)::numeric) AND ((hourly_rate IS NULL) OR (hourly_rate |
| handyman_performance | chk_performance_counts | CHECK (((jobs_completed >= 0) AND (jobs_cancelled >= 0) AND (jobs_active >= 0) AND (rated_jobs >= 0) AND ((successful_jobs >= 0) AND (succes |
| handyman_performance | chk_performance_rates | CHECK ((((success_rate IS NULL) OR ((success_rate >= (0)::numeric) AND (success_rate <= (1)::numeric))) AND ((avg_rating IS NULL) OR ((avg_r |
| handyman_performance | chk_performance_type | CHECK (((incident_type)::text = ANY ((ARRAY['all'::character varying, 'plumbing'::character varying, 'electrical'::character varying, 'heati |
| incident_trips | chk_trip_coordinates | CHECK ((((origin_latitude >= ('-90'::integer)::double precision) AND (origin_latitude <= (90)::double precision)) AND ((origin_longitude >=  |
| incident_trips | chk_trip_numbers | CHECK (((distance_km >= (0)::numeric) AND (travel_minutes >= (0)::numeric))) |
| incident_trips | chk_trip_origin_kind | CHECK (((origin_kind)::text = ANY ((ARRAY['home'::character varying, 'last_job'::character varying, 'custom'::character varying])::text[]))) |
| incident_trips | chk_trip_travel_mode | CHECK (((travel_mode)::text = ANY ((ARRAY['drive'::character varying, 'transit'::character varying])::text[]))) |
| incidents | chk_incident_billing | CHECK ((((hours_worked IS NULL) AND (amount_paid_eur IS NULL)) OR (((status)::text = 'completed'::text) AND (hours_worked > (0)::numeric) AN |
| incidents | chk_incident_completed_at | CHECK ((((status)::text <> 'completed'::text) OR (completed_at IS NOT NULL))) |
| incidents | chk_incident_description | CHECK (((length(btrim(description)) >= 5) AND (length(btrim(description)) <= 2000))) |
| incidents | chk_incident_feedback_completed | CHECK ((((rating IS NULL) AND (feedback IS NULL)) OR ((status)::text = 'completed'::text))) |
| incidents | chk_incident_feedback_length | CHECK (((feedback IS NULL) OR (length(feedback) <= 2000))) |
| incidents | chk_incident_handyman_required | CHECK ((((status)::text = ANY ((ARRAY['open'::character varying, 'recommended'::character varying, 'cancelled'::character varying])::text[]) |
| incidents | chk_incident_not_self_assigned | CHECK ((handyman_user_id <> reported_by_user_id)) |
| incidents | chk_incident_rating | CHECK (((rating IS NULL) OR ((rating >= 1) AND (rating <= 5)))) |
| incidents | chk_incident_required_skills | CHECK (((required_skills IS NULL) OR (cardinality(required_skills) <= 10))) |
| incidents | chk_incident_status | CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'recommended'::character varying, 'assigned'::character varying, 'in_progres |
| incidents | chk_incident_timestamps | CHECK ((((assigned_at IS NULL) OR (assigned_at >= created_at)) AND ((completed_at IS NULL) OR ((assigned_at IS NOT NULL) AND (completed_at > |
| incidents | chk_incident_travel | CHECK ((((distance_km IS NULL) OR (distance_km >= (0)::numeric)) AND ((travel_time_minutes IS NULL) OR (travel_time_minutes >= (0)::numeric) |
| incidents | chk_incident_urgency | CHECK (((urgency IS NULL) OR ((urgency)::text = ANY ((ARRAY['low'::character varying, 'medium'::character varying, 'high'::character varying |
| users | chk_user_coordinates | CHECK ((((latitude IS NULL) AND (longitude IS NULL)) OR (((latitude >= ('-90'::integer)::double precision) AND (latitude <= (90)::double pre |

## Incidents by status

Source: `maintops.incidents`

| status | incidents | rated | billed |
|---|---|---|---|
| completed | 39,519 | 37,809 | 39,519 |
| cancelled | 3,658 | 0 | 0 |
| in_progress | 1,576 | 0 | 0 |
| assigned | 24 | 0 | 0 |
