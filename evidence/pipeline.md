# Spark pipeline and data volume

The 1M-incident history through the batch pipeline (job `maintops_pipeline`), its data-quality checks and the defects it quarantined.

_Exported 2026-10-03 15:09 UTC by `evidence/export_evidence.py`._

## Row counts

Source: Delta tables in `bootcamp_students.maintops`

| table | rows |
|---|---|
| synth_incidents (source history) | 1,000,000 |
| bronze_incidents (raw export + injected defects) | 1,004,842 |
| silver_incidents (validated, deduplicated) | 980,519 |
| quarantine_incidents (rejected, with reasons) | 20,510 |
| gold_handyman_performance (scorecards) | 23,232 |
| gold_handyman_feedback (sentiment + summaries) | 9,295 |
| synth_users (clients + handymen) | 110,000 |
| cv_parsed (CV PDFs read by ai_parse_document) | 10,000 |

## Latest run of every step

Source: `pipeline_runs` (each step logs its counts and checks; a failed check stops the job)

| step | started_utc | seconds | rows_in | rows_out | rows_rejected | status | checks |
|---|---|---|---|---|---|---|---|
| 10_export_raw | 2026-10-02 20:13 | 34.0 | 1,000,000 | 1,004,842 | – | ok | {"source_has_1m_rows": true, "export_includes_duplicates": true} |
| 11_bronze | 2026-10-02 20:13 | 18.0 | 0 | 1,004,842 | – | ok | {"bronze_has_1m_rows": true} |
| 12_silver | 2026-10-02 20:14 | 52.0 | 1,004,842 | 979,490 | 20,510 | ok | {"one_row_per_incident": true, "silver_has_most_rows": true, "defects_caught": true} |
| 13_gold_performance | 2026-10-02 20:15 | 10.0 | – | 23,164 | – | ok | {"every_handyman_has_overall_row": true, "rates_in_range": true, "billing_consistent": true, "most_handymen_have_hourly_rate": true} |
| 14_gold_feedback | 2026-10-02 20:15 | 21.0 | – | 9,289 | 0 | ok | {"summaries_for_95pct": true, "sentiment_in_range": true} |
| 15_sync_lakebase | 2026-10-02 20:16 | 6.0 | 32,453 | 32,460 | – | ok | {"performance_in_lakebase": true, "feedback_in_lakebase": true, "avg_price_follows_hourly_rate": true} |
| 16_parse_cvs | 2026-09-28 08:04 | 2192.0 | 10,000 | 10,000 | 0 | ok | {"all_pdfs_parsed": true, "text_extracted": true} |

## Why records were quarantined

Source: `quarantine_incidents.reasons` (one record can have several)

| reason | records |
|---|---|
| invalid_urgency | 5,085 |
| empty_description | 4,849 |
| bad_timestamp | 3,012 |
| negative_distance | 2,936 |
| rating_out_of_range | 2,652 |
| invalid_hours | 1,819 |
| billing_incomplete | 157 |
| billing_on_unfinished_job | 157 |

## Hourly rate per job type (from billed jobs)

Source: `silver_incidents`: amount paid ÷ hours worked

| incident_type | completed_jobs | hours | eur_per_hour |
|---|---|---|---|
| electrical | 238,753 | 716,844 | 67.34 |
| heating_hvac | 213,689 | 748,285 | 72.61 |
| plumbing | 152,906 | 382,119 | 70.35 |
| carpentry | 151,113 | 604,042 | 64.08 |
| painting | 88,425 | 619,308 | 49.86 |
| roofing | 37,270 | 224,169 | 63.13 |
| flooring | 14,005 | 98,323 | 55.92 |
| appliance_repair | 4,455 | 6,670 | 65.38 |
| locksmith | 754 | 758 | 79.18 |
| general_maintenance | 78 | 146 | 53.09 |
