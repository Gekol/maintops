"""MaintOps – Flask frontend entry point."""

import base64
import json
import os
from functools import wraps

import bcrypt
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
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)

from db import get_connection

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# ─────────────────────────────────────────────────────────────
# Flask-Login setup
# ─────────────────────────────────────────────────────────────
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "error"


class User(UserMixin):
    """Lightweight user object for Flask-Login session management."""

    def __init__(self, id, email, first_name, last_name, is_handyman, is_active):
        self.id = id
        self.email = email
        self.first_name = first_name
        self.last_name = last_name
        self.is_handyman = is_handyman
        self._is_active = is_active

    @property
    def is_active(self):
        return self._is_active


@login_manager.user_loader
def load_user(user_id):
    """Reload user from Lakebase on every request (Flask-Login callback)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, first_name, last_name, is_handyman, is_active "
                "FROM maintops.users WHERE id = %s",
                (int(user_id),),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return User(*row)


# ─────────────────────────────────────────────────────────────
# Auth decorators
# ─────────────────────────────────────────────────────────────


def handyman_required(f):
    """Restrict a route to logged-in handymen."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_handyman:
            flash("This page is for handymen only.", "error")
            return redirect(url_for("landing"))
        return f(*args, **kwargs)
    return decorated


def client_required(f):
    """Restrict a route to logged-in clients (non-handymen)."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.is_handyman:
            flash("This page is for clients only.", "error")
            return redirect(url_for("landing"))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────
# Databricks AI config
# ─────────────────────────────────────────────────────────────
DBX_HOST = os.environ.get("DATABRICKS_HOST", "")       # e.g. https://dbc-xxx.cloud.databricks.com
DBX_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")     # PAT or OAuth token
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


@app.route("/dashboard")
@login_required
def dashboard():
    """Authenticated user dashboard — role-aware."""
    hm = None
    incidents = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            if current_user.is_handyman:
                # Fetch handyman profile stats
                cur.execute(
                    "SELECT specialisations, completed_cases, rating_avg, "
                    "rating_count, avg_price "
                    "FROM maintops.handyman_details WHERE user_id = %s",
                    (current_user.id,),
                )
                row = cur.fetchone()
                if row:
                    hm = {
                        "specialisations": row[0] or [],
                        "completed_cases": row[1],
                        "rating_avg": float(row[2]),
                        "rating_count": row[3],
                        "avg_price": row[4],
                    }
                else:
                    hm = {
                        "specialisations": [],
                        "completed_cases": 0,
                        "rating_avg": 0.0,
                        "rating_count": 0,
                        "avg_price": None,
                    }

                # Fetch assigned incidents
                cur.execute(
                    "SELECT id, description, incident_type, urgency, "
                    "status, created_at "
                    "FROM maintops.incidents "
                    "WHERE handyman_user_id = %s "
                    "ORDER BY created_at DESC LIMIT 20",
                    (current_user.id,),
                )
                for r in cur.fetchall():
                    incidents.append({
                        "id": r[0], "description": r[1],
                        "incident_type": r[2], "urgency": r[3],
                        "status": r[4], "created_at": r[5],
                    })
            else:
                # Fetch client's reported incidents (with handyman name)
                cur.execute(
                    "SELECT i.id, i.description, i.incident_type, i.urgency, "
                    "i.status, i.created_at, "
                    "u.first_name || ' ' || u.last_name AS handyman_name "
                    "FROM maintops.incidents i "
                    "LEFT JOIN maintops.users u ON i.handyman_user_id = u.id "
                    "WHERE i.reported_by_user_id = %s "
                    "ORDER BY i.created_at DESC LIMIT 20",
                    (current_user.id,),
                )
                for r in cur.fetchall():
                    incidents.append({
                        "id": r[0], "description": r[1],
                        "incident_type": r[2], "urgency": r[3],
                        "status": r[4], "created_at": r[5],
                        "handyman_name": r[6],
                    })

    return render_template(
        "dashboard.html", hm=hm, incidents=incidents,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """View and update personal information."""
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        date_of_birth = request.form.get("date_of_birth", "").strip() or None
        house = request.form.get("house", "").strip() or None
        postal_code = request.form.get("postal_code", "").strip() or None
        city = request.form.get("city", "").strip() or None
        state = request.form.get("state", "").strip() or None
        country = request.form.get("country", "").strip() or None

        if not first_name or not last_name:
            flash("First name and last name are required.", "error")
            return redirect(url_for("profile"))

        # --- Password change (optional) ---
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_new_password", "")
        new_hash = None

        if new_pw:
            if not current_pw:
                flash("Please enter your current password to change it.", "error")
                return redirect(url_for("profile"))
            if new_pw != confirm_pw:
                flash("New passwords do not match.", "error")
                return redirect(url_for("profile"))
            if len(new_pw) < 8:
                flash("New password must be at least 8 characters.", "error")
                return redirect(url_for("profile"))

            # Verify current password
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT password_hash FROM maintops.users WHERE id = %s",
                        (current_user.id,),
                    )
                    stored_hash = cur.fetchone()[0]

            if not bcrypt.checkpw(current_pw.encode(), stored_hash.encode()):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("profile"))

            new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()

        # --- Update user record ---
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    if new_hash:
                        cur.execute(
                            "UPDATE maintops.users SET first_name=%s, last_name=%s, "
                            "phone=%s, date_of_birth=%s, house=%s, postal_code=%s, "
                            "city=%s, state=%s, country=%s, password_hash=%s "
                            "WHERE id=%s",
                            (
                                first_name, last_name, phone, date_of_birth,
                                house, postal_code, city, state, country,
                                new_hash, current_user.id,
                            ),
                        )
                    else:
                        cur.execute(
                            "UPDATE maintops.users SET first_name=%s, last_name=%s, "
                            "phone=%s, date_of_birth=%s, house=%s, postal_code=%s, "
                            "city=%s, state=%s, country=%s "
                            "WHERE id=%s",
                            (
                                first_name, last_name, phone, date_of_birth,
                                house, postal_code, city, state, country,
                                current_user.id,
                            ),
                        )

                    # Update handyman details if applicable
                    if current_user.is_handyman:
                        specs = request.form.getlist("specialisations")
                        skills_raw = request.form.get("skills", "").strip()
                        skills = [s.strip() for s in skills_raw.split(",") if s.strip()] if skills_raw else None
                        experience = request.form.get("experience_summary", "").strip() or None
                        has_car = request.form.get("has_car") == "on"

                        cur.execute(
                            "UPDATE maintops.handyman_details "
                            "SET specialisations=%s, skills=%s, "
                            "experience_summary=%s, has_car=%s "
                            "WHERE user_id=%s",
                            (specs, skills, experience, has_car, current_user.id),
                        )

                    conn.commit()

            flash("Profile updated successfully.", "success")
        except Exception as exc:
            app.logger.error("Profile update failed: %s", exc)
            flash("Failed to update profile. Please try again.", "error")

        return redirect(url_for("profile"))

    # --- GET: load current data ---
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT first_name, last_name, email, phone, date_of_birth, "
                "house, postal_code, city, state, country, created_at "
                "FROM maintops.users WHERE id = %s",
                (current_user.id,),
            )
            row = cur.fetchone()
            user_data = {
                "first_name": row[0], "last_name": row[1], "email": row[2],
                "phone": row[3], "date_of_birth": row[4], "house": row[5],
                "postal_code": row[6], "city": row[7], "state": row[8],
                "country": row[9], "created_at": row[10],
            }

            hm = None
            if current_user.is_handyman:
                cur.execute(
                    "SELECT specialisations, skills, experience_summary, has_car "
                    "FROM maintops.handyman_details WHERE user_id = %s",
                    (current_user.id,),
                )
                hrow = cur.fetchone()
                if hrow:
                    hm = {
                        "specialisations": hrow[0] or [],
                        "skills": hrow[1] or [],
                        "experience_summary": hrow[2],
                        "has_car": hrow[3],
                    }

    return render_template(
        "profile.html",
        user_data=user_data,
        hm=hm,
        specialisations=SPECIALISATIONS,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return redirect(url_for("login"))

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, first_name, last_name, "
                    "is_handyman, is_active, password_hash "
                    "FROM maintops.users WHERE email = %s",
                    (email,),
                )
                row = cur.fetchone()

        if row is None:
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        uid, em, fn, ln, is_hm, is_act, pw_hash = row

        if not bcrypt.checkpw(password.encode(), pw_hash.encode()):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        if not is_act:
            flash("Your account has been deactivated.", "error")
            return redirect(url_for("login"))

        user = User(uid, em, fn, ln, is_hm, is_act)
        login_user(user)
        flash(f"Welcome back, {fn}!", "success")

        next_page = request.args.get("next")
        return redirect(next_page or url_for("dashboard"))

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
            return redirect(url_for("register", role=role))

        # Hash password with bcrypt
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        is_handyman = role == "handyman"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO maintops.users "
                        "(email, password_hash, first_name, last_name, "
                        " house, postal_code, city, country, is_handyman) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "RETURNING id",
                        (
                            email, pw_hash, first_name, last_name,
                            house, postal_code, city, country, is_handyman,
                        ),
                    )
                    user_id = cur.fetchone()[0]

                    # Create handyman_details row if registering as handyman
                    if is_handyman:
                        specs = request.form.getlist("specialisations")
                        cur.execute(
                            "INSERT INTO maintops.handyman_details "
                            "(user_id, specialisations) VALUES (%s, %s)",
                            (user_id, specs),
                        )

                    conn.commit()

            user = User(user_id, email, first_name, last_name, is_handyman, True)
            login_user(user)
            flash(f"Welcome to MaintOps, {first_name}!", "success")
            return redirect(url_for("dashboard"))

        except Exception as exc:
            if "users_email_key" in str(exc):
                flash("An account with this email already exists.", "error")
            else:
                app.logger.error("Registration failed: %s", exc)
                flash("Registration failed. Please try again.", "error")
            return redirect(url_for("register", role=role))

    return render_template(
        "register.html",
        role=role,
        specialisations=SPECIALISATIONS,
    )


@app.route("/logout")
@login_required
def logout():
    """Log the user out and redirect to landing."""
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("landing"))


@app.route("/parse-cv", methods=["POST"])
@login_required
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
