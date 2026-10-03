"""MaintOps – Flask frontend entry point."""

import os
import time
import uuid
from datetime import UTC
from functools import wraps
from zoneinfo import ZoneInfo

import requests as http_requests
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
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
from flask_wtf.csrf import CSRFProtect

from maintops_core import geo, trips
from maintops_core import incidents as inc
from maintops_core.db import get_connection
from maintops_core.events import log_event

app = Flask(__name__)

password_hasher = PasswordHasher()


def verify_password(stored_hash, password):
    try:
        return password_hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
csrf = CSRFProtect(app)   # every POST (forms and fetch calls) must carry the session's CSRF token

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
MANNY_ENDPOINT = os.environ.get("MANNY_ENDPOINT", "maintops-manny")   # Model Serving endpoint of the agent
MANNY_URL = "/".join((DBX_HOST, "serving-endpoints", MANNY_ENDPOINT, "invocations"))
MANNY_MAX_MESSAGES = 20
MANNY_MAX_CHARS = 2000
MANNY_RATE_LIMIT = (30, 600)      # at most 30 requests per 10 minutes per browser session
                                  # (AI Gateway rate limits aren't available for agent endpoints in this workspace)
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")          # SQL warehouse for ai_parse_document
CV_UPLOAD_DIR = "/Volumes/bootcamp_students/maintops/maintops_docs/cv_uploads"
CV_MAX_BYTES = 5 * 1024 * 1024
CV_TYPES = {".pdf", ".png", ".jpg", ".jpeg"}
CV_PARSES_PER_SESSION = 5

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


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────


@app.route("/healthz")
def healthz():
    """Liveness + Lakebase check for the hosting platform."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return jsonify({"status": "ok"})
    except Exception as exc:  # noqa: BLE001
        app.logger.error("Health check failed: %s", exc)
        return jsonify({"status": "degraded", "lakebase": "unreachable"}), 503


@app.route("/")
def landing():
    """Public landing page."""
    return render_template("landing.html")


# ─────────────────────────────────────────────────────────────
# Manny (agent served from Unity Catalog via Model Serving)
# ─────────────────────────────────────────────────────────────


def _current_role():
    """Role and user id sent to Manny — decided by the server session, never by the browser."""
    if not current_user.is_authenticated:
        return "visitor", None
    return ("handyman" if current_user.is_handyman else "client"), int(current_user.id)


@app.route("/api/manny", methods=["POST"])
def manny_chat():
    """Forward the chat to the Manny serving endpoint and return its reply plus structured data
    (candidate cards, actions) for the widget."""
    data = request.get_json(silent=True) or {}
    messages = [
        {"role": m.get("role"), "content": str(m.get("content", ""))[:MANNY_MAX_CHARS]}
        for m in (data.get("messages") or [])[-MANNY_MAX_MESSAGES:]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    if not messages or messages[-1]["role"] != "user" or not messages[-1]["content"].strip():
        return jsonify({"error": "Please type a message."}), 400

    limit, window = MANNY_RATE_LIMIT
    now = time.time()
    recent = [t for t in session.get("manny_calls", []) if now - t < window]
    if len(recent) >= limit:
        log_event("guardrail", "rate_limited", True, user_id=_current_role()[1])
        return jsonify({"error": "You're sending messages very quickly — please wait a few minutes."}), 429
    session["manny_calls"] = recent + [now]

    role, user_id = _current_role()
    payload = {"input": messages,
               "custom_inputs": {"role": role, "user_id": user_id, "task": "chat"}}
    try:
        resp = http_requests.post(
            MANNY_URL,
            headers=_dbx_headers(), json=payload, timeout=120,
        )
    except http_requests.RequestException as exc:
        app.logger.error("Manny request failed: %s", exc)
        return jsonify({"error": "Manny is not reachable right now. Please try again in a moment."}), 502
    if resp.status_code != 200:
        app.logger.error("Manny endpoint returned %s: %s", resp.status_code, resp.text[:500])
        return jsonify({"error": "Manny is not available right now. Please try again in a moment."}), 502

    body = resp.json()
    reply = " ".join(
        part.get("text", "")
        for item in body.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text"
    ).strip()
    custom = body.get("custom_outputs") or {}
    return jsonify({
        "reply": reply or "Sorry, I have no answer to that.",
        "candidates": custom.get("candidates"),
        "incident_id": custom.get("incident_id"),
        "actions": custom.get("actions") or [],
        "incidents": custom.get("incidents"),
    })


@app.route("/api/incidents/<int:incident_id>/assign", methods=["POST"])
@client_required
def assign_incident(incident_id):
    """'Choose' button on a candidate card (the widget asks for confirmation first)."""
    handyman_id = (request.get_json(silent=True) or {}).get("handyman_id")
    if not isinstance(handyman_id, int):
        return jsonify({"error": "Missing handyman."}), 400
    try:
        result = inc.assign_handyman(int(current_user.id), incident_id, handyman_id)
    except inc.ServiceError as exc:
        log_event("ui_action", "assign_handyman", False, user_id=int(current_user.id),
                  incident_id=incident_id, error=str(exc))
        return jsonify({"error": str(exc)}), 409
    log_event("ui_action", "assign_handyman", True, user_id=int(current_user.id), incident_id=incident_id,
              details={"handyman_id": handyman_id, "rank": result["recommendation_rank"]})
    return jsonify(result)


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
                    "FROM maintops.handyman_details "
                    "WHERE user_id = %s",
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
                    "status, created_at, rating, feedback, hours_worked, amount_paid_eur "
                    "FROM maintops.incidents "
                    "WHERE handyman_user_id = %s "
                    "ORDER BY status IN "
                    "('assigned', 'in_progress') DESC, "
                    "created_at DESC LIMIT 20",
                    (current_user.id,),
                )
                for r in cur.fetchall():
                    incidents.append({
                        "id": r[0], "description": r[1],
                        "incident_type": r[2], "urgency": r[3],
                        "status": r[4], "created_at": r[5],
                        "rating": r[6], "feedback": r[7],
                        "hours_worked": r[8], "amount_paid_eur": r[9],
                    })
            else:
                # Fetch client's reported incidents (with handyman name)
                cur.execute(
                    "SELECT i.id, i.description, i.incident_type, i.urgency, "
                    "i.status, i.created_at, "
                    "u.first_name || ' ' || u.last_name AS handyman_name, i.rating, i.feedback, "
                    "i.hours_worked, i.amount_paid_eur "
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
                        "handyman_name": r[6], "rating": r[7], "feedback": r[8],
                        "hours_worked": r[9], "amount_paid_eur": r[10],
                    })

    # "I'm on my way" trips: the handyman's own view, or the client's estimate (no starting address)
    trip_by_id = trips.trips_for([i["id"] for i in incidents if i["status"] == "assigned"])
    trip_options = None
    if current_user.is_handyman and any(i["status"] == "assigned" for i in incidents):
        trip_options = trips.trip_origins(int(current_user.id))
    return render_template(
        "dashboard.html", hm=hm, incidents=incidents, trips=trip_by_id, trip_options=trip_options,
    )


@app.route("/jobs/<int:incident_id>/trip", methods=["POST"])
@handyman_required
def job_trip(incident_id):
    """Handyman sets off for a job: starting point and travel mode → route → estimate for the client."""
    origin = request.form.get("origin", "")
    try:
        trip = trips.start_trip(int(current_user.id), incident_id, origin, request.form.get("mode") or None,
                                request.form.get("address"))
    except inc.ServiceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    log_event("ui_action", "start_trip", True, user_id=int(current_user.id), incident_id=incident_id,
              details={"origin": origin, "mode": trip["travel_mode"], "minutes": trip["travel_minutes"]})
    how = "by car" if trip["travel_mode"] == "drive" else "by public transport"
    minutes = max(1, round(trip["travel_minutes"]))
    flash(f"Safe trip! About {minutes} min {how}; the client now sees your estimated arrival.", "success")
    return redirect(url_for("dashboard"))


LOCAL_TZ = ZoneInfo("Europe/Berlin")


@app.template_filter("local_time")
def local_time(value) -> str:
    """A UTC timestamp from Lakebase as Berlin wall-clock time, e.g. 14:05."""
    if value is None:
        return ""
    return value.replace(tzinfo=UTC).astimezone(LOCAL_TZ).strftime("%H:%M")


@app.route("/incidents/<int:incident_id>/feedback", methods=["POST"])
@client_required
def incident_feedback(incident_id):
    """Rate a completed job; the live stream refreshes the handyman's scorecard within a minute."""
    try:
        rating = int(request.form.get("rating", ""))
    except ValueError:
        flash("Please choose a rating from 1 to 5.", "error")
        return redirect(url_for("dashboard"))
    try:
        inc.submit_feedback(int(current_user.id), incident_id, rating, request.form.get("feedback"))
    except inc.ServiceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    log_event("ui_action", "submit_feedback", True, user_id=int(current_user.id), incident_id=incident_id,
              details={"rating": rating})
    flash("Thanks for your feedback! It will shape future recommendations.", "success")
    return redirect(url_for("dashboard"))


@app.route("/incidents/<int:incident_id>/cancel", methods=["POST"])
@client_required
def incident_cancel(incident_id):
    try:
        inc.cancel_incident(int(current_user.id), incident_id)
    except inc.ServiceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    log_event("ui_action", "cancel_incident", True, user_id=int(current_user.id), incident_id=incident_id)
    flash(f"Incident #{incident_id} was cancelled.", "success")
    return redirect(url_for("dashboard"))


@app.route("/jobs/<int:incident_id>/status", methods=["POST"])
@handyman_required
def job_status(incident_id):
    """Handyman moves a job to in_progress, or completes it with the hours worked and the amount paid."""
    new_status = request.form.get("status", "")
    billing = {}
    if new_status == "completed":
        billing = {"hours_worked": request.form.get("hours_worked", ""),
                   "amount_paid_eur": request.form.get("amount_paid_eur", "")}
    try:
        out = inc.update_job_status(int(current_user.id), incident_id, new_status, **billing)
    except inc.ServiceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    log_event("ui_action", "update_job_status", True, user_id=int(current_user.id), incident_id=incident_id,
              details={k: v for k, v in out.items() if k != "incident_id"})
    if new_status == "completed":
        flash(f"Job #{incident_id} is now completed: {out['hours_worked']:g} h, €{out['amount_paid_eur']:.2f}.",
              "success")
    else:
        flash(f"Job #{incident_id} is now {new_status.replace('_', ' ')}.", "success")
    return redirect(url_for("dashboard"))


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
                        "SELECT password_hash "
                        "FROM maintops.users WHERE id = %s",
                        (current_user.id,),
                    )
                    stored_hash = cur.fetchone()[0]

            if not verify_password(stored_hash, current_pw):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("profile"))

            new_hash = password_hasher.hash(new_pw)

        # --- Update user record ---
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT house, postal_code, city, country "
                                "FROM maintops.users WHERE id = %s", (current_user.id,))
                    address_changed = cur.fetchone() != (house, postal_code, city, country)
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

            if address_changed:    # re-geocode only when the address actually changed
                _geocode_user(current_user.id, house, postal_code, city, country)
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
                    "FROM maintops.handyman_details "
                    "WHERE user_id = %s",
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

        if not verify_password(pw_hash, password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        if not is_act:
            flash("Your account has been deactivated.", "error")
            return redirect(url_for("login"))

        user = User(uid, em, fn, ln, is_hm, is_act)
        login_user(user)
        flash(f"Welcome back, {fn}!", "success")

        # Only follow local paths ("/dashboard"), never another site ("//evil.com", "https://...")
        next_page = request.args.get("next", "")
        if not (next_page.startswith("/") and not next_page.startswith("//") and "\\" not in next_page):
            next_page = url_for("dashboard")
        return redirect(next_page)

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
        state = request.form.get("state", "").strip() or None
        country = request.form.get("country", "").strip() or None      # empty: taken from geocoding
        phone = request.form.get("phone", "").strip() or None
        date_of_birth = request.form.get("dob", "").strip() or None
        terms = request.form.get("terms")

        errors = []
        if not all([first_name, last_name, email, house, postal_code, city, password, confirm_password]):
            errors.append("Please complete all required fields.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        if not terms:
            errors.append("You must accept the Terms of Service.")
        entry_method = request.form.get("entry_method", "manual")
        cv_path = session.get("cv_path") if role == "handyman" else None
        if role == "handyman" and entry_method == "cv" and not cv_path:
            errors.append("Please upload and read your CV first (or choose manual entry).")

        if errors:
            for err in errors:
                flash(err, "error")
            return redirect(url_for("register", role=role))

        # Hash password with Argon2id
        pw_hash = password_hasher.hash(password)
        is_handyman = role == "handyman"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO maintops.users "
                        "(email, password_hash, first_name, last_name, date_of_birth, phone, "
                        " house, postal_code, city, state, country, is_handyman) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "RETURNING id",
                        (
                            email, pw_hash, first_name, last_name, date_of_birth, phone,
                            house, postal_code, city, state, country, is_handyman,
                        ),
                    )
                    user_id = cur.fetchone()[0]

                    # Create handyman_details row if registering as handyman
                    if is_handyman:
                        specs = [x for x in request.form.getlist("specialisations") if x in SPECIALISATIONS]
                        skills_raw = request.form.get("skills", "")
                        skills = [x.strip() for x in skills_raw.split(",") if x.strip()] or None
                        experience = request.form.get("experience", "").strip() or None
                        cur.execute(
                            "INSERT INTO maintops.handyman_details "
                            "(user_id, specialisations, skills, experience_summary, cv_path) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (user_id, specs, skills, experience, cv_path),
                        )

                    conn.commit()
            session.pop("cv_path", None)
            _geocode_user(user_id, house, postal_code, city, country)

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


def _geocode_user(user_id, house, postal_code, city, country):
    """Store the address coordinates (Geoapify), and its state and country where the user left those fields empty.
    Registration still succeeds if geocoding fails; find_handymen geocodes lazily on first use."""
    address = ", ".join(p for p in (house, f"{postal_code or ''} {city or ''}".strip(), country) if p)
    try:
        loc = geo.geocode(address, user_id=user_id)
    except geo.GeoError as exc:
        app.logger.warning("Geocoding failed for user %s: %s", user_id, exc)
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE maintops.users SET latitude = %s, longitude = %s, state = COALESCE(state, %s), "
                    "country = COALESCE(country, %s) WHERE id = %s",
                    (loc["lat"], loc["lon"], loc.get("state"), loc.get("country"), user_id))
        conn.commit()


@app.route("/logout")
@login_required
def logout():
    """Log the user out and redirect to landing."""
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("landing"))


@app.route("/parse-cv", methods=["POST"])
def parse_cv():
    """Registration CV upload (no login yet): store the file in the UC Volume, parse it with
    ai_parse_document, and let Manny (task extract_cv) extract the profile fields for the form.
    The stored path is remembered in the session and saved with the account at registration."""
    cv_file = request.files.get("cv_file")
    if cv_file is None or not cv_file.filename:
        return jsonify({"error": "No file provided."}), 400
    ext = os.path.splitext(cv_file.filename.lower())[1]
    if ext not in CV_TYPES:
        return jsonify({"error": "Please upload a PDF, PNG or JPG file."}), 400
    file_bytes = cv_file.read(CV_MAX_BYTES + 1)
    if len(file_bytes) > CV_MAX_BYTES:
        return jsonify({"error": "The CV must be smaller than 5 MB."}), 400
    parses = session.get("cv_parses", 0)
    if parses >= CV_PARSES_PER_SESSION:
        return jsonify({"error": "Too many CV uploads — please fill in the form manually."}), 429
    session["cv_parses"] = parses + 1

    path = _upload_cv(file_bytes, ext)
    if path is None:
        return jsonify({"error": "Failed to store the CV. Please try again."}), 502
    parsed_text = _parse_document(path)
    if parsed_text is None:
        return jsonify({"error": "Failed to read the CV. Please fill in the form manually."}), 502
    profile = _extract_profile_fields(parsed_text)
    if profile is None:
        return jsonify({"error": "Failed to extract a profile from the CV."}), 502

    session["cv_path"] = path    # saved with the account at registration (the cookie can't hold the text)
    return jsonify(profile)


# ─────────────────────────────────────────────────────────────
# CV parsing helpers
# ─────────────────────────────────────────────────────────────


def _dbx_headers():
    """Standard auth headers for Databricks REST calls."""
    return {
        "Authorization": f"Bearer {DBX_TOKEN}",
        "Content-Type": "application/json",
    }


def _upload_cv(file_bytes: bytes, ext: str) -> str | None:
    """Store the uploaded CV in the Unity Catalog Volume (Files API); returns its Volume path."""
    path = f"{CV_UPLOAD_DIR}/{uuid.uuid4().hex}{ext}"
    resp = http_requests.put(
        f"{DBX_HOST}/api/2.0/fs/files{path}", params={"overwrite": "true"}, data=file_bytes, timeout=60,
        headers={"Authorization": f"Bearer {DBX_TOKEN}", "Content-Type": "application/octet-stream"},
    )
    if resp.status_code not in (200, 201, 204):
        app.logger.error("CV upload failed: %s %s", resp.status_code, resp.text[:300])
        return None
    return path


def _parse_document(path: str) -> str | None:
    """Run ai_parse_document on the stored CV (SQL Statement Execution API) and return its text.

    The path is generated by the server (uuid), never taken from the request.
    """
    sql = f"""
    WITH parsed AS (
      SELECT ai_parse_document(content, map('version', '2.0')) AS doc
      FROM read_files('{path}', format => 'binaryFile')
    )
    SELECT concat_ws('\\n\\n', transform(try_cast(doc:document:elements AS ARRAY<VARIANT>),
                                          el -> try_cast(el:content AS STRING))) AS full_text,
           try_cast(doc:error_status AS STRING) AS error
    FROM parsed
    """
    resp = http_requests.post(
        f"{DBX_HOST}/api/2.0/sql/statements", headers=_dbx_headers(), timeout=70,
        json={"warehouse_id": WAREHOUSE_ID, "statement": sql, "wait_timeout": "50s",
              "on_wait_timeout": "CONTINUE", "disposition": "INLINE"},
    )
    if resp.status_code != 200:
        app.logger.error("ai_parse_document SQL failed: %s", resp.text[:500])
        return None
    payload = resp.json()
    deadline = time.time() + 120
    while payload.get("status", {}).get("state") in ("PENDING", "RUNNING") and time.time() < deadline:
        time.sleep(3)
        status_url = "/".join((DBX_HOST, "api/2.0/sql/statements", payload["statement_id"]))
        payload = http_requests.get(status_url, headers=_dbx_headers(), timeout=30).json()
    if payload.get("status", {}).get("state") != "SUCCEEDED":
        app.logger.error("ai_parse_document status: %s", payload.get("status"))
        return None
    rows = payload.get("result", {}).get("data_array") or []
    if not rows or rows[0][1] or not rows[0][0]:
        app.logger.error("ai_parse_document returned no text: %s", rows[:1])
        return None
    return rows[0][0]


def _extract_profile_fields(cv_text: str) -> dict | None:
    """Ask Manny (task extract_cv) for structured handyman profile fields."""
    try:
        resp = http_requests.post(
            MANNY_URL, headers=_dbx_headers(), timeout=120,
            json={"input": [{"role": "user", "content": "Extract the handyman profile from this CV."}],
                  "custom_inputs": {"role": "visitor", "task": "extract_cv", "cv_text": cv_text}},
        )
    except http_requests.RequestException as exc:
        app.logger.error("Manny extract_cv failed: %s", exc)
        return None
    if resp.status_code != 200:
        app.logger.error("Manny extract_cv returned %s: %s", resp.status_code, resp.text[:500])
        return None
    return (resp.json().get("custom_outputs") or {}).get("profile")


# ─────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
