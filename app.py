"""MaintOps – Flask frontend entry point."""

import os

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

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
        if role == "handyman" and "cv_file" not in request.files:
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


# ─────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
