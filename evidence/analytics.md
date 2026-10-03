# Analytics pipeline output

Tables of the Lakeflow Declarative Pipeline `maintops_analytics` (refreshed every 30 minutes from Lakebase CDF). Most traffic comes from the release-gate evaluations, which run real conversations on test accounts.

_Exported 2026-10-03 13:46 UTC by `evidence/export_evidence.py`._

## Tool usage and success

Source: `analytics_tool_usage`

| tool | calls | success_pct | avg_latency_ms |
|---|---|---|---|
| find_handymen | 1,024 | 98.5 | 4162.0 |
| create_incident | 986 | 98.5 | 1035.0 |
| search_faq | 428 | 100.0 | 651.0 |
| get_incident | 420 | 65.2 | 778.0 |
| update_job_status | 308 | 53.2 | 626.0 |
| get_my_performance | 197 | 99.5 | 1238.0 |
| cancel_incident | 194 | 66.0 | 319.0 |
| search_my_reviews | 156 | 100.0 | 971.0 |
| get_my_incidents | 153 | 99.3 | 1473.0 |
| assign_handyman | 149 | 97.3 | 581.0 |
| submit_feedback | 141 | 48.9 | 500.0 |
| get_my_jobs | 73 | 100.0 | 818.0 |

## Write actions

Source: `analytics_write_actions`

| action | channel | actions | succeeded | users | first_day | last_day |
|---|---|---|---|---|---|---|
| create_incident | agent | 986 | 971 | 18 | 2026-09-28 | 2026-10-03 |
| update_job_status | agent | 308 | 164 | 14 | 2026-10-01 | 2026-10-03 |
| cancel_incident | agent | 194 | 128 | 8 | 2026-10-01 | 2026-10-03 |
| assign_handyman | agent | 149 | 145 | 10 | 2026-09-28 | 2026-10-03 |
| submit_feedback | agent | 141 | 69 | 10 | 2026-10-01 | 2026-10-03 |
| update_job_status | ui | 5 | 5 | 1 | 2026-10-02 | 2026-10-03 |
| assign_handyman | ui | 3 | 2 | 2 | 2026-09-28 | 2026-10-03 |
| cancel_incident | ui | 2 | 2 | 1 | 2026-10-02 | 2026-10-03 |
| submit_feedback | ui | 1 | 1 | 1 | 2026-10-03 | 2026-10-03 |

## Third-party API (Geoapify)

Source: `analytics_api_usage_daily`

| api_call | calls | failures | failure_pct | worst_daily_p95_ms |
|---|---|---|---|---|
| geoapify_route_matrix | 1,364 | 0 | 0.00 | 5,748 |
| geoapify_transit_routing | 689 | 0 | 0.00 | 8,012 |
| geoapify_geocode | 13 | 1 | 7.69 | 16,233 |

## Guardrail decisions

Source: `analytics_guardrails_daily`

| guardrail | events |
|---|---|
| emergency_detected | 282 |
| prompt_injection | 69 |
| input_too_long | 59 |
| figures_mixed_up | 27 |
| rate_limited | 1 |

## Agent requests by role

Source: `analytics_agent_requests_hourly` (summed over all hours)

| role | requests | input_tokens | output_tokens | estimated_cost_usd |
|---|---|---|---|---|
| client | 3,790 | 16,851,165 | 639,231 | 60.14 |
| handyman | 1,428 | 4,623,551 | 166,484 | 16.37 |
| visitor | 696 | 1,265,167 | 126,662 | 5.7 |

## Which recommendation clients choose

Source: `analytics_recommendation_rank`

| chosen_rank | assignments |
|---|---|
| 1 | 3 |
| 2 | 74 |
| 3 | 71 |

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
