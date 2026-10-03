# Analytics pipeline output

Tables of the Lakeflow Declarative Pipeline `maintops_analytics` (refreshed every 30 minutes from Lakebase CDF). Most traffic comes from the release-gate evaluations, which run real conversations on test accounts.

_Exported 2026-10-03 17:55 UTC by `evidence/export_evidence.py`._

## Tool usage and success

Source: `analytics_tool_usage`

| tool | calls | success_pct | avg_latency_ms |
|---|---|---|---|
| find_handymen | 1,133 | 98.7 | 4606.0 |
| create_incident | 1,087 | 98.6 | 970.0 |
| search_faq | 503 | 100.0 | 625.0 |
| get_incident | 462 | 65.8 | 715.0 |
| update_job_status | 353 | 54.1 | 548.0 |
| get_my_performance | 225 | 99.6 | 1086.0 |
| cancel_incident | 212 | 66.0 | 294.0 |
| search_my_reviews | 176 | 100.0 | 862.0 |
| get_my_incidents | 166 | 99.4 | 1358.0 |
| assign_handyman | 165 | 97.6 | 560.0 |
| submit_feedback | 154 | 48.7 | 459.0 |
| get_my_jobs | 82 | 100.0 | 730.0 |

## Write actions

Source: `analytics_write_actions`

| action | channel | actions | succeeded | users | first_day | last_day |
|---|---|---|---|---|---|---|
| create_incident | agent | 1,087 | 1,072 | 18 | 2026-09-28 | 2026-10-03 |
| update_job_status | agent | 353 | 191 | 14 | 2026-10-01 | 2026-10-03 |
| cancel_incident | agent | 212 | 140 | 8 | 2026-10-01 | 2026-10-03 |
| assign_handyman | agent | 165 | 161 | 10 | 2026-09-28 | 2026-10-03 |
| submit_feedback | agent | 154 | 75 | 10 | 2026-10-01 | 2026-10-03 |
| assign_handyman | ui | 8 | 7 | 2 | 2026-09-28 | 2026-10-03 |
| update_job_status | ui | 5 | 5 | 1 | 2026-10-02 | 2026-10-03 |
| cancel_incident | ui | 3 | 3 | 1 | 2026-10-02 | 2026-10-03 |
| submit_feedback | ui | 1 | 1 | 1 | 2026-10-03 | 2026-10-03 |

## Third-party API (Geoapify)

Source: `analytics_api_usage_daily`

| api_call | calls | failures | failure_pct | worst_daily_p95_ms |
|---|---|---|---|---|
| geoapify_route_matrix | 1,553 | 0 | 0.00 | 5,544 |
| geoapify_transit_routing | 836 | 0 | 0.00 | 7,924 |
| geoapify_geocode | 16 | 1 | 6.25 | 16,233 |

## Guardrail decisions

Source: `analytics_guardrails_daily`

| guardrail | events |
|---|---|
| emergency_detected | 315 |
| prompt_injection | 75 |
| input_too_long | 68 |
| figures_mixed_up | 27 |
| rate_limited | 1 |

## Agent requests by role

Source: `analytics_agent_requests_hourly` (summed over all hours)

| role | requests | input_tokens | output_tokens | estimated_cost_usd |
|---|---|---|---|---|
| client | 4,176 | 18,417,291 | 698,976 | 65.74 |
| handyman | 1,673 | 5,404,894 | 193,133 | 19.11 |
| visitor | 797 | 1,407,301 | 141,664 | 6.35 |

## Which recommendation clients choose

Source: `analytics_recommendation_rank`

| chosen_rank | assignments |
|---|---|
| 1 | 10 |
| 2 | 83 |
| 3 | 78 |

## Billing per month (latest)

Source: `analytics_billing_monthly`

| month | incident_type | jobs | hours_worked | amount_paid_eur | avg_hourly_rate |
|---|---|---|---|---|---|
| 2026-09-01T00:00:00.000Z | electrical | 1,746 | 5227.75 | 349071.08 | 66.77 |
| 2026-09-01T00:00:00.000Z | heating_hvac | 1,496 | 5265.75 | 381388.67 | 72.43 |
| 2026-09-01T00:00:00.000Z | carpentry | 1,078 | 4338.00 | 277460.15 | 63.96 |
| 2026-09-01T00:00:00.000Z | plumbing | 1,026 | 2610.25 | 183849.28 | 70.43 |
| 2026-09-01T00:00:00.000Z | painting | 634 | 4493.75 | 224040.15 | 49.86 |
| 2026-09-01T00:00:00.000Z | roofing | 264 | 1610.50 | 102918.81 | 63.90 |
| 2026-09-01T00:00:00.000Z | flooring | 89 | 605.75 | 33989.00 | 56.11 |
| 2026-09-01T00:00:00.000Z | appliance_repair | 33 | 50.50 | 3153.81 | 62.45 |
| 2026-09-01T00:00:00.000Z | locksmith | 7 | 6.50 | 487.34 | 74.98 |
| 2026-08-01T00:00:00.000Z | electrical | 3,695 | 11142.00 | 751262.23 | 67.43 |
