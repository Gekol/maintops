# Unstructured data (Variety)

CV PDFs parsed with `ai_parse_document`, the FAQ PDF chunked and embedded for vector search, and review texts scored and summarised with AI functions.

_Exported 2026-10-03 17:55 UTC by `evidence/export_evidence.py`._

## CV PDFs

Source: `cv_parsed`

| pdfs | parsed_ok | avg_chars |
|---|---|---|
| 10,000 | 10,000 | 1579.0 |

## FAQ retrieval quality

Source: `rag_eval_runs` (hit@3 on a fixed question set, latest run)

| run_on | index_name | n_questions | k | hits | hit_rate |
|---|---|---|---|---|---|
| 2026-09-27 | bootcamp_students.maintops.faq_index | 12 | 3 | 12 | 1.0 |

## Review sentiment

Source: `review_sentiment` (`ai_analyze_sentiment`, one row per distinct review text)

| sentiment | distinct_texts |
|---|---|
| mixed | 58 |
| positive | 45 |
| negative | 13 |
| neutral | 6 |

## Review summaries

Source: `gold_handyman_feedback` (`ai_query`, at most 25 words each)

| handymen | with_summary |
|---|---|
| 9,295 | 8,728 |
