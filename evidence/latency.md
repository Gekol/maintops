# Live stream latency (Velocity)

Time from a Postgres commit in Lakebase to the affected handymen's scorecards being back in Lakebase, measured by `pipeline/20_live_stream` for every micro-batch (CDF → stream → recompute → upsert).

_Exported 2026-10-03 17:55 UTC by `evidence/export_evidence.py`._

## Burst tests (`pipeline/latency_test.py`: N reviews committed in one transaction)

Source: `latency_metrics`, batches of 900–1,100 changes (the first two were measured before the stream was tuned on 28 September; the third is the tuned stream).

| processed_utc | n_changes | n_handymen | p50_s | p95_s | max_s |
|---|---|---|---|---|---|
| 2026-09-28 09:23 | 1,001 | 905 | 478.0 | 478.0 | 478.0 |
| 2026-09-28 09:31 | 1,000 | 911 | 72.0 | 72.0 | 72.0 |
| 2026-09-28 09:41 | 1,000 | 900 | 37.1 | 37.1 | 37.1 |

## Normal operation (batches under 200 changes)

Source: `latency_metrics` (larger batches are catch-ups after the stream was stopped: their age is waiting time, not processing time)

| batches | changes | median_p50_s | median_p95_s | pct_batches_all_under_60s |
|---|---|---|---|---|
| 350 | 4,890 | 32.3 | 44.4 | 94.0 |
