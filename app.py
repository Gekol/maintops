"""MaintOps – Flask frontend entry point."""

import base64
import json
import os

import requests as http_requests
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# ─────────────────────────────────────────────────────────────
# Databricks AI config
# ─────────────────────────────────────────────────────────────
DBX_HOST = os.environ.get("DATABRICKS_HOST", "")       # e.g. https://adb-123.4.azuredatabricks.net
DBX_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")     # PAT or OAuth token
DBX_WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")  # SQL warehouse ID
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

SPECIALISATIONS = [
    "plumbing",
    "electrical",
    "heating_hvac",
    "carpentry",
    "painting",
    "roofing",
    "flooring",
    "appliance_repair",
    "locksmith",
    "general_maintenance",
]

_SAMPLE_QA = {
    "what is maintops": (
        "MaintOps is an AI-powered handyman matching platform. You "
        "describe your home-repair problem in plain language and our "
        "system finds the best-qualified professional based on skills, "
        "experience, ratings, workload, and travel time."
    ),
    "how does matching work": (
        "When you submit an incident, our AI classifies the problem, "
        "identifies required specialisations, and invokes a "
        "deterministic ranking pipeline. It filters by skill and "
        "availability, pre-filters geographically, calls the Geoapify "
        "Route Matrix for real travel times, merges historical "
        "performance data, and returns the top three candidates."
    ),
    "is it free": (
        "Creating an account and submitting incidents is free. "
        "Pricing details for premium features will be announced soon."
    ),
    "how do i register": (
        'Click the "Sign up" button in the top-right corner. You can '
        "register as a client (to request repairs) or as a handyman "
        "(to receive job assignments)."
    ),
}


def _get_placeholder_answer(question: str) -> str:
    """Return a canned answer or a fallback."""
    q = question.lower().strip().rstrip("?")
    for key, answer in _SAMPLE_QA.items():
        if key in q:
            return answer
    return (
        "Great question! Once the RAG knowledge base is connected, "
        "I'll be able to answer detailed questions about MaintOps "
        "services, pricing, coverage areas, and more. Stay tuned!"
    )


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────


@app.route("/")
def landing():
    """Public landing page."""
    chat_history = session.get("chat_history", [])
    return render_template("landing.html", chat_history=chat_history)


@app.route("/chat", methods=["POST"])
def chat():
    """Handle RAG chat form submission (placeholder)."""
    question = request.form.get("question", "").strip()
    if question:
        history = session.get("chat_history", [])
        history.append({"role": "user", "content": question})
        history.append(
            {"role": "assistant", "content": _get_placeholder_answer(question)}
        )
        session["chat_history"] = history
    return redirect(url_for("landing") + "#features")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
        else:
            # TODO: wire to Lakebase auth backend
            flash(
                "Frontend login flow complete. Connect this form to your "
                "Lakebase authentication backend next.",
                "success",
            )
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Registration page."""
    role = request.args.get("role", "client")

    if request.method == "POST":
        role = request.form.get("role", "client")
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        house = request.form.get("house", "").strip()
        postal_code = request.form.get("postal_code", "").strip()
        city = request.form.get("city", "").strip()
        country = request.form.get("country", "").strip()
        terms = request.form.get("terms")

        errors = []
        if not all([first_name, last_name, email, house, postal_code, city, country, password, confirm_password]):
            errors.append("Please complete all required fields.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        if not terms:
            errors.append("You must accept the Terms of Service.")
        entry_method = request.form.get("entry_method", "manual")
        if role == "handyman" and entry_method == "cv" and "cv_file" not in request.files:
            errors.append("Please upload a CV for handyman registration.")

        if errors:
            for err in errors:
                flash(err, "error")
        else:
            # TODO: wire to Lakebase, Geoapify geocoding, CV processing
            flash(
                f"{role.title()} registration UI is ready. "
                "Next, connect this form to Lakebase, Geoapify geocoding, and CV processing.",
                "success",
            )
        return redirect(url_for("register", role=role))

    return render_template(
        "register.html",
        role=role,
        specialisations=SPECIALISATIONS,
    )


@app.route("/parse-cv", methods=["POST"])
def parse_cv():
    """Accept a CV upload, parse it via Databricks ai_parse_document,
    then extract structured handyman profile fields via an LLM."""
    if "cv_file" not in request.files or request.files["cv_file"].filename == "":
        return jsonify({"error": "No file provided."}), 400

    cv_file = request.files["cv_file"]
    file_bytes = cv_file.read()

    # ---- Step 1: Parse the document via Databricks SQL Statement API ----
    parsed_text = _parse_document(file_bytes)
    if parsed_text is None:
        return jsonify({"error": "Failed to parse the CV. Check server logs."}), 502

    # ---- Step 2: Extract structured fields via Foundation Model ----
    extracted = _extract_profile_fields(parsed_text)
    if extracted is None:
        return jsonify({"error": "Failed to extract profile from parsed CV."}), 502

    return jsonify(extracted)


# ─────────────────────────────────────────────────────────────
# CV parsing helpers
# ─────────────────────────────────────────────────────────────


def _dbx_headers():
    """Standard auth headers for Databricks REST calls."""
    return {
        "Authorization": f"Bearer {DBX_TOKEN}",
        "Content-Type": "application/json",
    }


def _parse_document(file_bytes: bytes) -> str | None:
    """Call ai_parse_document via the Databricks SQL Statement Execution API.

    Encodes the file as base64, wraps it in a SQL query that calls
    ai_parse_document, and extracts the concatenated text content
    from the parsed VARIANT response.
    """
    b64 = base64.b64encode(file_bytes).decode()

    # SQL that decodes the base64 bytes, parses the document, and
    # flattens the elements into a single text column.
    sql = f"""
    WITH parsed AS (
      SELECT ai_parse_document(
        from_base64('{b64}'),
        MAP('version', '2.0')
      ) AS doc
    )
    SELECT concat_ws(
      '\\n\\n',
      transform(
        try_cast(doc:document:elements AS ARRAY<VARIANT>),
        el -> try_cast(el:content AS STRING)
      )
    ) AS full_text
    FROM parsed
    WHERE is_variant_null(doc:error_status)
    """

    resp = http_requests.post(
        f"{DBX_HOST}/api/2.0/sql/statements",
        headers=_dbx_headers(),
        json={
            "warehouse_id": DBX_WAREHOUSE_ID,
            "statement": sql,
            "wait_timeout": "120s",
            "disposition": "INLINE",
        },
        timeout=180,
    )

    if resp.status_code != 200:
        app.logger.error("ai_parse_document SQL failed: %s", resp.text)
        return None

    payload = resp.json()
    status = payload.get("status", {}).get("state")
    if status != "SUCCEEDED":
        app.logger.error("SQL statement status: %s  %s", status, payload)
        return None

    rows = payload.get("result", {}).get("data_array", [])
    if not rows or not rows[0][0]:
        app.logger.error("ai_parse_document returned no text.")
        return None

    return rows[0][0]


def _extract_profile_fields(cv_text: str) -> dict | None:
    """Call a Databricks Foundation Model to extract structured
    handyman profile fields from the parsed CV text."""

    system_prompt = (
        "You are a data extraction assistant. Given the text of a handyman's CV, "
        "extract the following fields and return ONLY valid JSON with no markdown "
        "formatting, no code fences, no extra text:\n"
        '{"first_name": "", "last_name": "", "email": "", "phone": "", '
        '"specialisations": [], "skills": "", "experience": ""}\n\n'
        "Rules:\n"
        "- specialisations must be a subset of: plumbing, electrical, heating_hvac, "
        "carpentry, painting, roofing, flooring, appliance_repair, locksmith, "
        "general_maintenance.\n"
        "- skills should be a comma-separated string of specific abilities.\n"
        "- experience should be a short professional summary.\n"
        "- If a field cannot be determined, leave it as an empty string or empty array."
    )

    resp = http_requests.post(
        f"{DBX_HOST}/serving-endpoints/{LLM_ENDPOINT}/invocations",
        headers=_dbx_headers(),
        json={
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": cv_text},
            ],
            "max_tokens": 1024,
            "temperature": 0.0,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        app.logger.error("LLM extraction failed: %s", resp.text)
        return None

    try:
        llm_output = resp.json()["choices"][0]["message"]["content"]
        return json.loads(llm_output)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        app.logger.error("Failed to parse LLM response: %s  raw=%s", exc, resp.text)
        return None


# ─────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
