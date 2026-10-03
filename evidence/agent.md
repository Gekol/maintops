# Manny: release gate and live performance

Every Manny version is evaluated on a temporary staging endpoint (63 multi-turn scenarios × 3 runs, every check in code) and promoted to production only if no check fails.

_Exported 2026-10-03 15:09 UTC by `evidence/export_evidence.py`._

## Release-gate runs

Source: reports of job `maintops_manny_deploy` (`eval/results/` in the deployed bundle; details and transcripts in MLflow experiment `maintops_manny_eval`)

| run (UTC) | scenarios | runs passed | checks | failed | gate | what failed | MLflow run |
|---|---|---|---|---|---|---|---|
| 2026-10-02 01:57 | 58 | 126/159 | 1,316 | 78 | FAIL | job_start_confirmed: agent_responded; report_locksmith: status_recommended; report_locksmith: three_candidates; report_locksmith: create_then_find; report_locks | be65ee69537d4e03823918651c508da8 |
| 2026-10-02 02:50 | 58 | 171/173 | 1,467 | 5 | FAIL | assign_by_position: setup; assign_by_position: names_choice; assign_by_position: assigned_to_choice; assign_by_position: assign_handyman_args; assign_by_positio | e77d66975b16450bae9a517fa3bf156e |
| 2026-10-02 04:02 | 58 | 170/174 | 1,473 | 4 | FAIL | report_painting: skill_claims; report_roofing: skill_claims; assign_by_position: harness_database; client_cannot_change_job_status: agent_responded | aee8b227ed0745d2a6f2b49caf10566f |
| 2026-10-02 04:56 | 58 | 153/174 | 1,397 | 48 | FAIL | report_electrical: skill_claims; report_flooring: cards_equal_recommendation; report_flooring: three_candidates; report_flooring: reply_names_a_candidate; repor | a81733b2e282498196f9483294ad7d31 |
| 2026-10-02 06:37 | 58 | 167/174 | 1,500 | 13 | FAIL | report_carpentry: cards_equal_recommendation; report_carpentry: three_candidates; report_carpentry: reply_names_a_candidate; report_general_maintenance: one_inc | f050fed0b938409795f75a097d676e69 |
| 2026-10-02 09:53 | 59 | 174/177 | 1,548 | 3 | FAIL | report_electrical: figures_belong_to_candidate | 36cc40234cd34f5aac9aabec44bdb637 |
| 2026-10-02 11:13 | 59 | 173/177 | 1,620 | 4 | FAIL | report_painting: prices_are_per_hour; report_roofing: prices_are_per_hour; assign_by_name: figures_grounded; emergency_ceiling_water: skill_claims | 6cee35788bea46a68bb163ac7dbc80d5 |
| 2026-10-02 11:36 | 59 | 177/177 | 1,620 | 0 | PASS | – | ef0058da47964ca782f7c4d8f94b1a1f |
| 2026-10-02 20:45 | 63 | 187/189 | 1,776 | 2 | FAIL | report_roofing: skill_claims; weakest_side: figures_grounded | 3cb675eb1ad745358950ffaaa0399ec6 |
| 2026-10-02 21:09 | 63 | 187/189 | 1,776 | 2 | FAIL | report_electrical: reply_names_incident; emergency_ceiling_water: skill_claims | 61429e89eec643bba5a7d8b2b72dbed6 |
| 2026-10-02 21:38 | 63 | 187/189 | 1,776 | 2 | FAIL | weakest_side: figures_grounded; strongest_side: figures_grounded | cff399ea4a264e9d878129975f45936c |
| 2026-10-02 22:04 | 63 | 189/189 | 1,776 | 0 | PASS | – | 56009acbb0db48dc8804cd86d7a5315c |
| 2026-10-03 10:12 | 63 | 188/189 | 1,803 | 1 | FAIL | emergency_ceiling_water: figures_grounded | 5fcf8cb8341b4ffe99c00dc0a0650cf2 |
| 2026-10-03 10:36 | 63 | 189/189 | 1,803 | 0 | PASS | – | 808b4a8fa918432c97ebe59f0774da3c |
| 2026-10-03 11:33 | 63 | 188/189 | 1,821 | 1 | FAIL | feedback_confirmed: handyman_rating_count | ec34717dd28840e0b1c4a6a4ec154caa |
| 2026-10-03 11:57 | 63 | 188/189 | 1,821 | 1 | FAIL | job_complete_amount_not_invented: figures_grounded | 946670ed5c634208ad99a5471c1c4c34 |
| 2026-10-03 13:08 | 63 | 186/189 | 1,804 | 6 | FAIL | report_heating_hvac: agent_responded; report_heating_hvac: one_incident_created; report_electrical: cards_equal_recommendation; report_electrical: three_candida | 4a910bc43931426a9c766b110b96ad54 |
| 2026-10-03 13:34 | 63 | 189/189 | 1,821 | 0 | PASS | – | 63ea7e35d30a47cbb053fcb1e36f2e82 |
| 2026-10-03 14:21 | 63 | 188/189 | 1,857 | 1 | FAIL | feedback_confirmed: handyman_rating_count | 757b841b596942b6bc1fb71a3c9158c8 |
| 2026-10-03 14:44 | 63 | 189/189 | 1,857 | 0 | PASS | – | 8340c593d97c445397e43177c375fc27 |

## Tool calls (last 7 days)

Source: `maintops.app_events` (every tool call is logged with arguments and timing)

| tool | calls | success_pct | median_ms |
|---|---|---|---|
| find_handymen | 1,125 | 98.7 | 2,780 |
| create_incident | 1,079 | 98.6 | 21 |
| search_faq | 497 | 100.0 | 251 |
| get_incident | 458 | 65.5 | 12 |
| update_job_status | 353 | 54.1 | 17 |
| get_my_performance | 224 | 99.6 | 21 |
| cancel_incident | 212 | 66.0 | 15 |
| search_my_reviews | 176 | 100.0 | 12 |
| get_my_incidents | 166 | 99.4 | 12 |
| assign_handyman | 161 | 97.5 | 25 |
| submit_feedback | 154 | 48.7 | 19 |
| get_my_jobs | 82 | 100.0 | 13 |

## Why write tools were refused (last 7 days)

Source: `maintops.app_events`. Almost all of these come from the evaluation's test accounts deliberately asking for forbidden actions; ids are replaced by N

| tool | rule_that_refused | calls | from_eval_accounts |
|---|---|---|---|
| cancel_incident | Incident N is 'in_progress' and can no longer be cancelled. | 72 | 72 |
| submit_feedback | Feedback can only be given once the job is completed. | 67 | 67 |
| update_job_status | Job N is 'completed' and cannot move to 'in_progress'. | 48 | 48 |
| update_job_status | €N for N h is €N.N per hour; rates between €N and €N per hour are accepted. Please check both figures. | 42 | 42 |
| update_job_status | Hours worked must be between N.N and N. | 42 | 42 |
| update_job_status | Job N not found among your assigned jobs. | 27 | 27 |
| create_incident | PoolTimeout: couldn't get a connection after N.N sec | 13 | 13 |
| submit_feedback | You have already rated this job. | 12 | 12 |
| update_job_status | PoolTimeout: couldn't get a connection after N.N sec | 2 | 2 |
| assign_handyman | OperationalError: consuming input failed: SSL connection has been closed unexpectedly | 1 | 1 |
| update_job_status | OutOfMemory: out of memory DETAIL:  Failed on request of size N in memory context "ExecutorState". | 1 | 1 |
| create_incident | OperationalError: consuming input failed: SSL connection has been closed unexpectedly | 1 | 1 |
| assign_handyman | This handyman has just become fully booked. Please choose another candidate. | 1 | 1 |
| create_incident | Your account is not active. | 1 | 0 |
| assign_handyman | PoolTimeout: couldn't get a connection after N.N sec | 1 | 1 |
| assign_handyman | Incident N not found. | 1 | 0 |
