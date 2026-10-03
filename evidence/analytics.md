# Analytics pipeline output

Tables of the Lakeflow Declarative Pipeline `maintops_analytics` (refreshed every 30 minutes from Lakebase CDF). Most traffic comes from the release-gate evaluations, which run real conversations on test accounts.

_Exported 2026-10-03 15:09 UTC by `evidence/export_evidence.py`._

## Tool usage and success

Source: `analytics_tool_usage`

| tool | calls | success_pct | avg_latency_ms |
|---|---|---|---|
| find_handymen | 1,122 | 98.7 | 4471.0 |
| create_incident | 1,077 | 98.6 | 970.0 |
| search_faq | 495 | 100.0 | 618.0 |
| get_incident | 458 | 65.5 | 714.0 |
| update_job_status | 353 | 54.1 | 548.0 |
| get_my_performance | 224 | 99.6 | 1091.0 |
| cancel_incident | 212 | 66.0 | 294.0 |
| search_my_reviews | 174 | 100.0 | 872.0 |
| get_my_incidents | 166 | 99.4 | 1358.0 |
| assign_handyman | 161 | 97.5 | 540.0 |
| submit_feedback | 154 | 48.7 | 459.0 |
| get_my_jobs | 82 | 100.0 | 730.0 |

## Write actions

Source: `analytics_write_actions`

| action | channel | actions | succeeded | users | first_day | last_day |
|---|---|---|---|---|---|---|
| create_incident | agent | 1,077 | 1,062 | 18 | 2026-09-28 | 2026-10-03 |
| update_job_status | agent | 353 | 191 | 14 | 2026-10-01 | 2026-10-03 |
| cancel_incident | agent | 212 | 140 | 8 | 2026-10-01 | 2026-10-03 |
| assign_handyman | agent | 161 | 157 | 10 | 2026-09-28 | 2026-10-03 |
| submit_feedback | agent | 154 | 75 | 10 | 2026-10-01 | 2026-10-03 |
| update_job_status | ui | 5 | 5 | 1 | 2026-10-02 | 2026-10-03 |
| assign_handyman | ui | 4 | 3 | 2 | 2026-09-28 | 2026-10-03 |
| cancel_incident | ui | 3 | 3 | 1 | 2026-10-02 | 2026-10-03 |
| submit_feedback | ui | 1 | 1 | 1 | 2026-10-03 | 2026-10-03 |

## Third-party API (Geoapify)

Source: `analytics_api_usage_daily`

| api_call | calls | failures | failure_pct | worst_daily_p95_ms |
|---|---|---|---|---|
| geoapify_route_matrix | 1,520 | 0 | 0.00 | 5,748 |
| geoapify_transit_routing | 817 | 0 | 0.00 | 7,869 |
| geoapify_geocode | 13 | 1 | 7.69 | 16,233 |

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
| client | 4,149 | 18,279,684 | 693,210 | 65.24 |
| handyman | 1,662 | 5,372,230 | 192,008 | 19.0 |
| visitor | 789 | 1,396,529 | 140,105 | 6.29 |

## Which recommendation clients choose

Source: `analytics_recommendation_rank`

| chosen_rank | assignments |
|---|---|
| 1 | 4 |
| 2 | 80 |
| 3 | 77 |

## Billing per month (latest)

Source: `analytics_billing_monthly`

| month | incident_type | jobs | hours_worked | amount_paid_eur | avg_hourly_rate |
|---|---|---|---|---|---|
| 2026-10-01T00:00:00.000Z | heating_hvac | 1 | 3.00 | 210.00 | 70.00 |
| 2026-10-01T00:00:00.000Z | electrical | 1 | 2.50 | 135.00 | 54.00 |
| 2026-09-01T00:00:00.000Z | electrical | 1,746 | 5227.75 | 349071.08 | 66.77 |
| 2026-09-01T00:00:00.000Z | heating_hvac | 1,496 | 5265.75 | 381388.67 | 72.43 |
| 2026-09-01T00:00:00.000Z | carpentry | 1,078 | 4338.00 | 277460.15 | 63.96 |
| 2026-09-01T00:00:00.000Z | plumbing | 1,026 | 2610.25 | 183849.28 | 70.43 |
| 2026-09-01T00:00:00.000Z | painting | 634 | 4493.75 | 224040.15 | 49.86 |
| 2026-09-01T00:00:00.000Z | roofing | 264 | 1610.50 | 102918.81 | 63.90 |
| 2026-09-01T00:00:00.000Z | flooring | 89 | 605.75 | 33989.00 | 56.11 |
| 2026-09-01T00:00:00.000Z | appliance_repair | 33 | 50.50 | 3153.81 | 62.45 |
