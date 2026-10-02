"""Manny — the MaintOps agent: one MLflow ResponsesAgent for every AI interaction in the app.

Registered in Unity Catalog (bootcamp_students.maintops.manny) and served by a Model Serving endpoint
(agent/deploy_manny). The Flask backend calls it with custom_inputs set by the server:
    {"role": "visitor" | "client" | "handyman", "user_id": int | None, "task": "chat" | "extract_cv"}
The LLM never supplies identity: tools receive user_id from custom_inputs, and each role only sees
the tools it may use.

Governance:
  - guardrails: input length, prompt-injection refusal, emergency → safety advice first, tool-argument
    validation, confirmation required for consequential actions, check that quoted percentages come from tools,
    no phone numbers except 112
  - resilience: LLM calls retried with backoff on rate limits / 5xx (then a polite "busy" reply instead of an
    error); read tools retried once on a dropped database connection
  - tracing: MLflow autolog of every LLM call + a span per tool call (served traces land in the experiment
    and the endpoint's inference table)
  - app_events: every request, tool call and guardrail decision is logged to Lakebase (→ CDF → analytics)
"""

import json
import os
import re
import time
import uuid

import mlflow
import openai
import psycopg
from mlflow.entities import SpanType
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from maintops_core import grounding, matching, rag
from maintops_core import incidents as inc
from maintops_core.events import log_event

LLM_ENDPOINT = os.environ.get("MANNY_LLM_ENDPOINT", "databricks-claude-sonnet-4-6")
MAX_STEPS = 8
MAX_INPUT_CHARS = 2000
MAX_HISTORY = 20

mlflow.openai.autolog()

# ─────────────────────────────────────────────────────────────
# Guardrails
# ─────────────────────────────────────────────────────────────
_INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|your)\s+"
    r"(instructions|rules|prompt|guidelines)|system\s+prompt|developer\s+mode|you\s+are\s+now\s+|"
    r"jailbreak|act\s+as\s+(an?\s+)?(admin|administrator|system|developer)", re.I)
_EMERGENCY = re.compile(
    r"smell(s|ing)?\s+(of\s+)?gas|gas\s+(leak|smell)|\bfire\b|flames?|smoke|burning\s+smell|sparks?|sparking|"
    r"electric(al)?\s+shock|flood(ing|ed)?|"
    r"water\s+.*ceiling|carbon\s+monoxide", re.I)
# A phone number (starts with + or 0, at least 6 digits) other than 112 never comes from a tool: never give one
_PHONE = re.compile(r"(?<![\w.#])(?:\+|0)\d[\d \-/()]{4,}\d(?![\d.])")
PHONE_REMOVED = "(number removed — use 112 or the number on your provider's official website or bill)"
FIGURE_CORRECTION = ("[MaintOps check, not the user] Your draft gives a handyman figures that belong to "
                     "another candidate: {}. Rewrite the reply so that every figure you state for a handyman is "
                     "that handyman's own value from find_handymen. Do not call tools.")
BUSY_REPLY = "I'm getting a lot of requests right now and couldn't finish that. Please try again in a minute."
LLM_RETRY_DELAYS = (2, 4, 8, 16)                   # seconds; ~30 s in total before giving up politely
RETRY_ON_DB_DROP = {"search_faq", "get_my_incidents", "get_incident", "find_handymen", "get_my_jobs",
                    "get_my_reviews", "search_my_reviews", "get_my_performance"}   # reads (find_handymen recomputes)
SAFETY_NOTE = ("⚠️ If anyone is in danger, call the emergency number 112 first. For a gas smell: no flames or "
               "light switches, open the windows, leave and call your gas provider's emergency line. For water "
               "or electrical danger, turn off the main water valve or the fuse box if it is safe to do so.")

# ─────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────


def _fn(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or [],
                       "additionalProperties": False}}}


_CONFIRM = {"type": "boolean", "description": "true only after the user explicitly confirmed this action"}
TOOLS = {
    "search_faq": _fn("search_faq", "Search the MaintOps user guide / FAQ. Use for any question about how "
                      "MaintOps works, pricing, coverage, registration, the process or policies.",
                      {"question": {"type": "string"}}, ["question"]),
    "get_my_incidents": _fn("get_my_incidents", "List the client's recent incidents with their status."),
    "get_incident": _fn("get_incident", "Details of one incident of this user.",
                        {"incident_id": {"type": "integer"}}, ["incident_id"]),
    "create_incident": _fn(
        "create_incident", "Create an incident from the client's problem description. Call it once you "
        "understand the problem well enough to classify it.",
        {"description": {"type": "string", "description": "the problem in the client's words, cleaned up"},
         "incident_type": {"type": "string", "enum": inc.SPECIALISATIONS},
         "urgency": {"type": "string", "enum": inc.URGENCIES},
         "required_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                             "description": "specific skills needed, e.g. 'pipe repair', 'leak detection'"}},
        ["description", "incident_type", "urgency", "required_skills"]),
    "find_handymen": _fn("find_handymen", "Rank the best 3 handymen for an incident (skills, track record, "
                         "reviews, workload, real travel time). Call right after create_incident.",
                         {"incident_id": {"type": "integer"}}, ["incident_id"]),
    "assign_handyman": _fn("assign_handyman", "Assign one of the recommended handymen to the incident.",
                           {"incident_id": {"type": "integer"}, "handyman_id": {"type": "integer"},
                            "confirm": _CONFIRM}, ["incident_id", "handyman_id", "confirm"]),
    "cancel_incident": _fn("cancel_incident", "Cancel an incident that is not yet in progress.",
                           {"incident_id": {"type": "integer"}, "confirm": _CONFIRM},
                           ["incident_id", "confirm"]),
    "submit_feedback": _fn("submit_feedback", "Rate a completed job (1–5) with an optional written review.",
                           {"incident_id": {"type": "integer"},
                            "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "feedback": {"type": "string"}, "confirm": _CONFIRM},
                           ["incident_id", "rating", "confirm"]),
    "get_my_jobs": _fn("get_my_jobs", "List the handyman's assigned jobs, most urgent first."),
    "get_my_reviews": _fn("get_my_reviews", "The handyman's recent ratings and their review summary."),
    "search_my_reviews": _fn(
        "search_my_reviews", "Find the handyman's own rated jobs, e.g. the worst- or best-rated ones. Use for "
        "requests to see jobs or reviews ('show my worst feedback', 'my low-rated plumbing jobs'). The app shows "
        "the returned jobs as a list.",
        {"order": {"type": "string", "enum": list(inc.REVIEW_ORDERS),
                   "description": "worst = lowest rating first, best = highest first, recent = newest first"},
         "incident_type": {"type": "string", "enum": inc.SPECIALISATIONS},
         "min_rating": {"type": "integer", "minimum": 1, "maximum": 5},
         "max_rating": {"type": "integer", "minimum": 1, "maximum": 5},
         "limit": {"type": "integer", "minimum": 1, "maximum": inc.MAX_REVIEW_RESULTS}},
        ["order"]),
    "get_my_performance": _fn(
        "get_my_performance", "The handyman's scorecard: overall and per job type (success rate, rating, "
        "resolution time, cancellations), review sentiment and summary, the strongest and weakest job type as "
        "computed by MaintOps, and recent low- and top-rated review texts. Use for questions about strengths, "
        "weaknesses or how customers see them."),
    "update_job_status": _fn("update_job_status", "Move one of the handyman's jobs to in_progress or completed.",
                             {"incident_id": {"type": "integer"},
                              "status": {"type": "string", "enum": ["in_progress", "completed"]},
                              "confirm": _CONFIRM}, ["incident_id", "status", "confirm"]),
}
ROLE_TOOLS = {
    "visitor": ["search_faq"],
    "client": ["search_faq", "get_my_incidents", "get_incident", "create_incident", "find_handymen",
               "assign_handyman", "cancel_incident", "submit_feedback"],
    "handyman": ["search_faq", "get_my_jobs", "get_incident", "get_my_reviews", "search_my_reviews",
                 "get_my_performance", "update_job_status"],
}
CONFIRM_REQUIRED = {"assign_handyman", "cancel_incident", "submit_feedback", "update_job_status"}
_STARS = re.compile(r"\b(\d+)\s*-?\s*(?:stars?|★)", re.I)   # "6 stars", "4-star", "5 ★"

# ─────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────
_BASE = """You are Manny, the MaintOps assistant. MaintOps matches people with household maintenance problems
to suitable handymen in Germany. Be concise, friendly and factual.
Rules:
- Ground every fact in tool results. For questions about MaintOps itself, use search_faq; if the guide does not
  cover it, say it is not specified. Never invent prices, availability, handymen or incident data.
- A "yes" confirms exactly what you proposed and nothing else. If the reply changes or contradicts any detail
  (another rating, another handyman, another job), do not act: say what differs and ask again.
- Never claim an action happened unless the tool returned success. If a tool returns an error, relay it plainly and
  only suggest next steps that exist in MaintOps (profile page, trying again, contacting support) — never guess causes.
- MaintOps is not an emergency service: if there is danger to people or property, advise calling 112 first.
  Never give any other phone number: refer to "your gas / water / electricity provider's emergency line".
- Only use numbers (percentages, minutes, ratings, prices) exactly as returned by tools."""

PROMPTS = {
    "visitor": _BASE + """
The user is not logged in. Answer questions about MaintOps with search_faq. To report a problem or hire a
handyman they must register (Sign up, top right) — you cannot create incidents for visitors.""",
    "client": _BASE + f"""
The user is a logged-in client. When they describe a maintenance problem:
1. Act on the first message whenever you can tell which kind of handyman is needed — details such as the exact
   part, cause or model are for the handyman to find out, never a reason to ask. Only if you cannot even tell
   the trade (e.g. "something is broken") ask ONE short question. If it is not a household or property
   maintenance problem, say MaintOps cannot help with it.
2. Classify it: incident_type (one of {', '.join(inc.SPECIALISATIONS)}; general_maintenance covers small jobs
   such as mounting a TV or shelves, assembling furniture, hanging pictures or resealing), urgency (low = cosmetic / no rush,
   medium = should be fixed this week, high = damage getting worse or an important function lost,
   critical = danger to people or property), and 2–4 specific required_skills.
3. Call create_incident, then immediately find_handymen with the new incident id.
4. Start by confirming what you did, e.g. “I have logged incident #1000123 (plumbing, high urgency).” The app shows
   the three candidates as cards with all the figures, so do NOT list them again. In 2–4 sentences: name your top
   pick and why (skills, track record on this job type, travel time), mention any recurring complaint from the
   reviews, then ask which one they want (they can also press "Choose" on a card). Describe travel time as
   distance, never as when someone will arrive, and by travel_mode: drive = "a 7-minute drive away",
   transit (the handyman has no car) = "about 35 minutes away by public transport", never a drive.
   Describe skill coverage only with skills_matched ("3 of 4 required skills"); say "all" only when both
   numbers are equal.
   avg_hourly_rate_eur is what the handyman charged per hour on past jobs: say "about €68 per hour",
   never present it as the price of this job (the total depends on how long the job takes).
5. When the user picks one, confirm the name and ask "Shall I assign <name>?" Only after an explicit yes call
   assign_handyman with confirm=true. The same applies to cancel_incident. After assigning, say the job now
   appears in the handyman's job list — never promise when or how they will get in touch.
If they refer to an incident or a candidate you cannot see above, call get_my_incidents before saying you don't know.
After a completed job, you can record their rating (1–5) and review with submit_feedback: first repeat the job,
rating and review back and ask "Shall I save this?"; only after an explicit yes call it with confirm=true.""",  # noqa: E501
    "handyman": _BASE + """
The user is a logged-in handyman. Help them see their jobs (get_my_jobs), details, and reviews
(get_my_reviews), and update job status. Before update_job_status, ask for explicit confirmation and only
then call it with confirm=true.
Questions about their feedback:
- To see specific jobs ("worst feedback", "best reviews", "low-rated plumbing jobs") call search_my_reviews. The app
  shows the returned jobs as a list, so do NOT repeat them; in 1–3 sentences name the pattern you see (recurring
  complaints or praise). If nothing matches, say so.
- For strengths, weaknesses or how customers see them, call get_my_performance. If the comparison says there is
  not enough data, say so instead of guessing; if clear_difference is false, say the job types perform about the
  same and let the review themes be the answer.
  - Weaknesses: use the weakest job type, recurring complaints in review_summary and low_rated_reviews, and end with
    one concrete, practical suggestion drawn from the complaints — without made-up figures or example numbers.
  - Strengths: use the strongest job type, praise in review_summary and top_rated_reviews (you may quote one short
    review). Do not add complaints or tips unless they ask.
  - Both or a general question: cover both sides briefly.""",
}

CV_PROMPT = f"""
You extract a handyman profile from CV text. Return ONLY valid JSON, no markdown:
{{"first_name": "", "last_name": "", "email": "", "phone": "", "specialisations": [], "skills": "", "experience": ""}}
- specialisations must be a subset of: {', '.join(inc.SPECIALISATIONS)}.
- skills: comma-separated specific abilities; experience: a 1–2 sentence professional summary.
- Use empty strings / arrays for anything not in the CV. Never invent details."""


# ─────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────


def _text_of(content) -> str:
    """Message content may be a string or a list of content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _percent_values(obj) -> set[str]:
    """Every *_percent value in a tool result, as strings (for the number guardrail)."""
    if isinstance(obj, dict):
        found = {str(v) for k, v in obj.items() if k.endswith("_percent") and v is not None}
        return found.union(*(_percent_values(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_percent_values(v) for v in obj))
    return set()


class MannyAgent(ResponsesAgent):
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from databricks.sdk import WorkspaceClient
            self._client = WorkspaceClient().serving_endpoints.get_open_ai_client()
        return self._client

    # ── entry point ──────────────────────────────────────────
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        ci = dict(request.custom_inputs or {})
        role = ci.get("role") if ci.get("role") in PROMPTS else "visitor"
        user_id = ci.get("user_id")
        if role != "visitor" and not isinstance(user_id, int):
            role, user_id = "visitor", None               # no trusted identity → visitor capabilities only
        ctx = {"role": role, "user_id": user_id, "request_id": ci.get("request_id") or str(uuid.uuid4()),
               "candidates": None, "incident_id": None, "actions": [], "tokens": [0, 0],
               "incidents": None, "grounded_percents": set()}
        started = time.time()
        mlflow.update_current_trace(tags={"role": role, "task": ci.get("task", "chat"),
                                          "request_id": ctx["request_id"]})

        if ci.get("task") == "extract_cv":
            reply, custom = self._extract_cv(ci.get("cv_text", ""), ctx)
        else:
            history = [{"role": m.get("role"), "content": _text_of(m.get("content"))}
                       for m in (item.model_dump() if hasattr(item, "model_dump") else dict(item)
                                 for item in request.input)
                       if m.get("role") in ("user", "assistant")][-MAX_HISTORY:]
            reply = self._chat(history, ctx)
            custom = {"candidates": ctx["candidates"], "incident_id": ctx["incident_id"],
                      "actions": ctx["actions"], "incidents": ctx["incidents"]}

        log_event("agent_request", ci.get("task", "chat"), True, user_id=user_id, request_id=ctx["request_id"],
                  incident_id=ctx["incident_id"], latency_ms=int((time.time() - started) * 1000),
                  input_tokens=ctx["tokens"][0], output_tokens=ctx["tokens"][1],
                  details={"role": role, "actions": ctx["actions"]})
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=reply, id=str(uuid.uuid4()))],
            custom_outputs={**custom, "request_id": ctx["request_id"], "role": role})

    # ── chat ─────────────────────────────────────────────────
    def _chat(self, history: list[dict], ctx: dict) -> str:
        last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        ctx["last_user"] = last
        if not last.strip():
            return "Hi! I'm Manny. How can I help you today?"
        if len(last) > MAX_INPUT_CHARS:
            self._guardrail(ctx, "input_too_long")
            limit = MAX_INPUT_CHARS
            return f"That message is a bit long for me — please keep it under {limit} characters."
        if _INJECTION.search(last):
            self._guardrail(ctx, "prompt_injection", last)
            return ("I can only help with MaintOps: questions about the service, reporting maintenance problems "
                    "and managing your jobs. What can I do for you?")
        emergency = bool(_EMERGENCY.search(last))
        if emergency:
            self._guardrail(ctx, "emergency_detected", last)

        system = PROMPTS[ctx["role"]] + self._state_note(ctx)
        if emergency:
            system += "\nThe latest message may describe an emergency: start your answer with safety advice."
        messages = [{"role": "system", "content": system}] + history
        tools = [TOOLS[n] for n in ROLE_TOOLS[ctx["role"]]]

        for _ in range(MAX_STEPS):
            resp = self._complete(ctx, messages=messages, tools=tools, temperature=0.1, max_tokens=1200)
            if resp is None:
                return f"{SAFETY_NOTE}\n\n{BUSY_REPLY}" if emergency else BUSY_REPLY
            if resp.usage:
                ctx["tokens"][0] += resp.usage.prompt_tokens or 0
                ctx["tokens"][1] += resp.usage.completion_tokens or 0
            msg = resp.choices[0].message
            if not msg.tool_calls:
                reply = (msg.content or "").strip()
                mixed = self._mixed_up_figures(reply, history, ctx)
                if mixed and not ctx.get("corrected"):
                    ctx["corrected"] = True              # one rewrite, then the data-only summary below
                    self._guardrail(ctx, "figures_mixed_up", "; ".join(mixed))
                    messages += [{"role": "assistant", "content": reply},
                                 {"role": "user", "content": FIGURE_CORRECTION.format("; ".join(mixed))}]
                    continue
                if mixed:
                    self._guardrail(ctx, "figures_mixed_up_fallback", "; ".join(mixed))
                    reply = grounding.candidate_summary(ctx["incident_id"], ctx["candidates"])
                reply = self._check_numbers(reply, ctx)
                reply = self._remove_phone_numbers(reply, ctx)
                if emergency and "112" not in reply:
                    reply = f"{SAFETY_NOTE}\n\n{reply}"
                return reply
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                result = self._run_tool(tc.function.name, tc.function.arguments, ctx)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, default=str)[:12000]})
        return "Sorry, I couldn't finish that request. Please try again or rephrase it."

    def _complete(self, ctx: dict, **kwargs):
        """One LLM call, retried with backoff on rate limits and server errors; None = still failing."""
        for delay in (*LLM_RETRY_DELAYS, None):
            try:
                return self.client.chat.completions.create(model=LLM_ENDPOINT, **kwargs)
            except (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError) as exc:
                if delay is None:
                    self._guardrail(ctx, "llm_unavailable", f"{type(exc).__name__}: {exc}")
                    return None
                time.sleep(delay)

    # ── state from the database ──────────────────────────────
    def _state_note(self, ctx: dict) -> str:
        """The user's current incidents / jobs, read from Lakebase every turn, so follow-ups like
        "the second one" resolve to real ids without trusting anything sent by the browser."""
        try:
            return self._read_state(ctx)
        except psycopg.OperationalError:                 # a dropped connection: try once more with a fresh one
            try:
                return self._read_state(ctx)
            except Exception:  # noqa: BLE001 — context is a convenience; the tools still validate everything
                return "\n\n(Could not load the user's incidents: call the list tool before answering about them.)"
        except Exception:  # noqa: BLE001
            return "\n\n(Could not load the user's incidents: call the list tool before answering about them.)"

    def _read_state(self, ctx: dict) -> str:
        """The active incidents (client) or jobs (handyman) as a note for the system prompt."""
        if ctx["role"] == "client":
            items = [i for i in inc.list_client_incidents(ctx["user_id"], limit=5)
                     if i["status"] in ("open", "recommended", "assigned", "in_progress")]
            if not items:
                return "\n\nThe client has no active incidents."
            names = inc.user_names({h for i in items for h in (i["recommended_handyman_ids"] or [])})
            lines = []
            for i in items:
                line = (f"- incident {i['id']} ({i['status']}, {i['incident_type']}, {i['urgency']}): "
                        f"{i['description'][:80]}")
                if i["status"] == "recommended":
                    line += "; recommended in this order: " + ", ".join(
                        f"{n}. {names.get(h, '?')} (handyman_id {h})"
                        for n, h in enumerate(i["recommended_handyman_ids"] or [], 1))
                lines.append(line)
            return "\n\nThe client's active incidents (from the database, newest first):\n" + "\n".join(lines)
        if ctx["role"] == "handyman":
            jobs = [j for j in inc.list_handyman_jobs(ctx["user_id"], limit=10)
                    if j["status"] in ("assigned", "in_progress")]
            return "\n\nThe handyman's active jobs: " + ("; ".join(
                f"incident {j['id']} ({j['status']}, {j['urgency']}): {j['description'][:60]}" for j in jobs)
                or "none") + "."
        return ""

    # ── tools ────────────────────────────────────────────────
    @mlflow.trace(span_type=SpanType.TOOL)
    def _run_tool(self, name: str, raw_args: str, ctx: dict) -> dict:
        started = time.time()
        error, result, args = None, None, None
        try:
            if name not in ROLE_TOOLS[ctx["role"]]:
                raise inc.ServiceError(f"The tool {name} is not available for this user.")
            try:
                args = json.loads(raw_args or "{}")
            except json.JSONDecodeError as exc:
                raise inc.ServiceError("Tool arguments were not valid JSON.") from exc
            if not isinstance(args, dict):
                raise inc.ServiceError("Tool arguments must be an object.")
            missing = [k for k in TOOLS[name]["function"]["parameters"]["required"] if k not in args]
            if missing:
                raise inc.ServiceError(f"Missing arguments: {', '.join(missing)}.")
            if name in CONFIRM_REQUIRED and args.get("confirm") is not True:
                result = {"needs_confirmation": True,
                          "message": "Ask the user to explicitly confirm this action, "
                                     "then call again with confirm=true."}
            elif name == "submit_feedback" and self._rating_contradicted(args, ctx):
                self._guardrail(ctx, "confirmation_mismatch", ctx.get("last_user", ""))
                result = {"needs_confirmation": True,
                          "message": "The user's last message names a different star rating than the one you are "
                                     "saving. Do not save it: point out the difference and ask again (1–5 stars)."}
            else:
                try:
                    result = self._dispatch(name, args, ctx)
                except psycopg.OperationalError:
                    if name not in RETRY_ON_DB_DROP:
                        raise
                    result = self._dispatch(name, args, ctx)   # a dropped connection: the pool hands out a fresh one
        except inc.ServiceError as exc:
            error, result = str(exc), {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — never leak internals to the model or the user
            error = f"{type(exc).__name__}: {exc}"
            result = {"error": "Something went wrong on our side. Please try again."}
        log_event("tool_call", name, error is None, user_id=ctx["user_id"], request_id=ctx["request_id"],
                  incident_id=ctx["incident_id"], error=error, latency_ms=int((time.time() - started) * 1000),
                  details={"args": args, "needs_confirmation": bool(result and result.get("needs_confirmation"))})
        return result

    def _dispatch(self, name: str, a: dict, ctx: dict):
        uid = ctx["user_id"]
        as_int = lambda k: int(a[k])                     # noqa: E731 — raises ValueError on bad input
        if name == "search_faq":
            return {"results": [{"text": r["text"], "score": round(r["score"], 3)}
                                for r in rag.search_faq(str(a["question"]), k=3)]}
        if name == "get_my_incidents":
            return {"incidents": inc.list_client_incidents(uid)}
        if name == "get_incident":
            return inc.get_incident(uid, as_int("incident_id"))
        if name == "create_incident":
            skills = a.get("required_skills") or []
            if not isinstance(skills, list):
                raise inc.ServiceError("required_skills must be a list of strings.")
            out = inc.create_incident(uid, str(a["description"]), str(a["incident_type"]), str(a["urgency"]),
                                      [str(s) for s in skills])
            ctx["incident_id"] = out["id"]
            ctx["actions"].append({"action": "create_incident", **out, "incident_type": a["incident_type"],
                                   "urgency": a["urgency"], "required_skills": skills})
            return out
        if name == "find_handymen":
            out = matching.find_handymen(uid, as_int("incident_id"))
            ctx["incident_id"] = out["incident_id"]
            ctx["candidates"] = out["candidates"]
            # The model gets what it needs to explain; the cards get the full data via custom_outputs
            return {**out, "candidates": [{k: v for k, v in c.items() if k != "factors"}
                                          for c in out["candidates"]]}
        if name == "assign_handyman":
            out = inc.assign_handyman(uid, as_int("incident_id"), as_int("handyman_id"))
            ctx["incident_id"] = out["incident_id"]
            ctx["actions"].append({"action": "assign_handyman", **out})
            return out
        if name == "cancel_incident":
            out = inc.cancel_incident(uid, as_int("incident_id"))
            ctx["actions"].append({"action": "cancel_incident", **out})
            return out
        if name == "submit_feedback":
            out = inc.submit_feedback(uid, as_int("incident_id"), as_int("rating"), a.get("feedback"))
            ctx["actions"].append({"action": "submit_feedback", **out})
            return out
        if name == "get_my_jobs":
            return {"jobs": inc.list_handyman_jobs(uid)}
        if name == "get_my_reviews":
            return inc.get_handyman_reviews(uid)
        if name == "search_my_reviews":
            rows = inc.search_handyman_reviews(
                uid, str(a["order"]), a.get("incident_type"), a.get("min_rating"), a.get("max_rating"),
                a.get("limit", 10))
            ctx["incidents"] = rows                      # shown as a list by the app
            return {"count": len(rows), "incidents": rows}
        if name == "get_my_performance":
            out = inc.get_handyman_performance(uid)
            ctx["grounded_percents"] |= _percent_values(out)
            return out
        if name == "update_job_status":
            out = inc.update_job_status(uid, as_int("incident_id"), str(a["status"]))
            ctx["actions"].append({"action": "update_job_status", **out})
            return out
        raise inc.ServiceError(f"Unknown tool {name}.")

    # ── output guardrail ─────────────────────────────────────
    def _check_numbers(self, reply: str, ctx: dict) -> str:
        """Percentages quoted about candidates or a scorecard must come from the tools; otherwise flag it."""
        if not ctx["candidates"] and not ctx["grounded_percents"]:
            return reply
        allowed = {str(c.get(k)) for c in ctx["candidates"] or [] for k in ("match_percent", "success_rate_percent")}
        allowed |= ctx["grounded_percents"]
        quoted = set(re.findall(r"(\d{1,3})\s?%", reply))
        if quoted - allowed:
            self._guardrail(ctx, "ungrounded_number", ", ".join(sorted(quoted - allowed)))
            source = "the cards" if ctx["candidates"] else "your dashboard"
            reply += f"\n\n(Please rely on the figures shown on {source}.)"
        return reply

    @staticmethod
    def _rating_contradicted(args: dict, ctx: dict) -> bool:
        """The user's latest message states a star count that differs from the rating about to be saved."""
        said = {int(n) for n in _STARS.findall(ctx.get("last_user", ""))}
        return bool(said) and args.get("rating") not in said

    @staticmethod
    def _mixed_up_figures(reply: str, history: list[dict], ctx: dict) -> list[str]:
        """Sentences that give a candidate another candidate's figures (see maintops_core.grounding)."""
        if not ctx["candidates"]:
            return []
        shared = {float(ctx["incident_id"] or 0), 3.0}
        shared |= {float(m.group(2)) for c in ctx["candidates"]
                   for m in [re.match(r"(\d+) of (\d+)", c.get("skills_matched") or "")] if m}
        shared |= {n for m in history if m["role"] == "user" for n in grounding.numbers_in(m["content"])}
        return grounding.misattributed_figures(reply, ctx["candidates"], shared)

    def _remove_phone_numbers(self, reply: str, ctx: dict) -> str:
        """No tool returns phone numbers, so any in a reply is unverified — in an emergency a wrong one is dangerous."""
        found = [m.group(0) for m in _PHONE.finditer(reply) if sum(ch.isdigit() for ch in m.group(0)) >= 6]
        if not found:
            return reply
        self._guardrail(ctx, "phone_number_removed", ", ".join(found))
        for number in found:
            reply = reply.replace(number, PHONE_REMOVED)
        return reply

    def _guardrail(self, ctx: dict, name: str, text: str = "") -> None:
        log_event("guardrail", name, True, user_id=ctx["user_id"], request_id=ctx["request_id"],
                  details={"excerpt": text[:200]} if text else None)

    # ── CV extraction (registration) ─────────────────────────
    def _extract_cv(self, cv_text: str, ctx: dict) -> tuple[str, dict]:
        cv_text = (cv_text or "").strip()[:20000]
        if not cv_text:
            return "No CV text received.", {"profile": None}
        resp = self._complete(ctx, temperature=0, max_tokens=800,
                              messages=[{"role": "system", "content": CV_PROMPT}, {"role": "user", "content": cv_text}])
        if resp is None:
            return BUSY_REPLY, {"profile": None}
        if resp.usage:
            ctx["tokens"] = [resp.usage.prompt_tokens or 0, resp.usage.completion_tokens or 0]
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw).strip()
        try:
            profile = json.loads(raw)
        except json.JSONDecodeError:
            return "Could not read a profile from this CV.", {"profile": None}
        specs = [s for s in profile.get("specialisations") or [] if s in inc.SPECIALISATIONS]
        profile["specialisations"] = specs
        return "Profile extracted.", {"profile": profile}


mlflow.models.set_model(MannyAgent())
