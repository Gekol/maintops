"""Unit tests for maintops_core logic that needs no database or network (HTTP is mocked).

Run: .venv/bin/python -m pytest -q tests
"""

import os
import time

import pytest
import requests

from maintops_core import geo, incidents, matching

os.environ.setdefault("GEOAPIFY_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def no_event_logging(monkeypatch):
    """Event logging writes to Lakebase; tests don't."""
    monkeypatch.setattr(geo, "log_event", lambda *a, **k: None)


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response, self.exc = response, exc

    def get(self, *a, **k):
        if self.exc:
            raise self.exc
        return self.response

    post = get


# ─────────────────────────────────────────────────────────────
# Skill matching
# ─────────────────────────────────────────────────────────────


def test_skill_score_full_partial_and_none():
    skills = ["pipe repair", "leak detection", "tap replacement"]
    assert matching._skill_score(["pipe repair", "leak detection"], skills) == (1.0, ["pipe repair", "leak detection"])
    score, matched = matching._skill_score(["pipe repair", "boiler maintenance"], skills)
    assert score == 0.75 and matched == ["pipe repair"]
    assert matching._skill_score(["roof tile repair"], ["socket installation"])[0] == 0.5


def test_skill_score_without_required_skills_is_neutral():
    assert matching._skill_score([], ["anything"]) == (0.75, [])


def test_weights_sum_to_one():
    assert sum(matching.WEIGHTS.values()) == pytest.approx(1)
    assert sum(matching.URGENT_WEIGHTS.values()) == pytest.approx(1)


# ─────────────────────────────────────────────────────────────
# Geoapify
# ─────────────────────────────────────────────────────────────


def test_haversine_berlin_hamburg():
    assert geo.haversine_km(52.52, 13.405, 53.551, 9.993) == pytest.approx(255, abs=5)


def test_geocode_valid(monkeypatch):
    payload = {"results": [{"lat": 52.52, "lon": 13.41, "formatted": "Berlin", "state": "Berlin",
                            "country": "Germany", "rank": {"confidence": 0.9}}]}
    monkeypatch.setattr(geo, "_get_session", lambda: FakeSession(FakeResponse(200, payload)))
    loc = geo.geocode("Alexanderplatz 1, Berlin")
    assert (loc["lat"], loc["state"], loc["country"]) == (52.52, "Berlin", "Germany")


@pytest.mark.parametrize("payload, message", [
    ({"results": []}, "not found"),
    ({"results": [{"lat": 95, "lon": 13, "rank": {"confidence": 1}}]}, "malformed"),
    ({"results": [{"lat": 52, "lon": 13, "rank": {"confidence": 0.2}}]}, "uncertain"),
])
def test_geocode_rejects_bad_answers(monkeypatch, payload, message):
    monkeypatch.setattr(geo, "_get_session", lambda: FakeSession(FakeResponse(200, payload)))
    with pytest.raises(geo.GeoError, match=message):
        geo.geocode("somewhere")


def test_route_matrix_parses_response(monkeypatch):
    payload = {"sources_to_targets": [[{"distance": 5000, "time": 600, "source_index": 0, "target_index": 0}],
                                      [{"distance": 1200, "time": 180, "source_index": 1, "target_index": 0}]]}
    monkeypatch.setattr(geo, "_get_session", lambda: FakeSession(FakeResponse(200, payload)))
    out = geo.route_matrix([(52.5, 13.4), (52.51, 13.41)], (52.52, 13.42))
    assert out == [{"distance_km": 5.0, "travel_minutes": 10.0, "estimated": False},
                   {"distance_km": 1.2, "travel_minutes": 3.0, "estimated": False}]


@pytest.mark.parametrize("session", [
    FakeSession(FakeResponse(429, {})),                                   # still rate-limited after retries
    FakeSession(FakeResponse(200, {"unexpected": True})),                 # malformed body
    FakeSession(FakeResponse(200, ValueError("not json"))),               # not JSON
    FakeSession(exc=requests.ConnectionError("down")),                    # network failure
])
def test_route_matrix_falls_back_to_flagged_estimate(monkeypatch, session):
    monkeypatch.setattr(geo, "_get_session", lambda: session)
    out = geo.route_matrix([(52.5, 13.4)], (52.52, 13.42))
    assert out[0]["estimated"] is True and out[0]["distance_km"] > 0


def test_route_matrix_limits_sources():
    with pytest.raises(ValueError):
        geo.route_matrix([(52.5, 13.4)] * (geo.MAX_MATRIX_SOURCES + 1), (52.5, 13.4))


# ─────────────────────────────────────────────────────────────
# Service validation (rejected before any database access)
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kwargs, message", [
    ({"description": "hi", "incident_type": "plumbing", "urgency": "low"}, "between 5 and 2000"),
    ({"description": "The tap drips", "incident_type": "astrology", "urgency": "low"}, "Unknown incident type"),
    ({"description": "The tap drips", "incident_type": "plumbing", "urgency": "whenever"}, "Unknown urgency"),
])
def test_create_incident_validation(kwargs, message):
    with pytest.raises(incidents.ServiceError, match=message):
        incidents.create_incident(1, **kwargs)


@pytest.mark.parametrize("rating", [0, 6, "5", None])
def test_feedback_rating_validation(rating):
    with pytest.raises(incidents.ServiceError, match="1 to 5"):
        incidents.submit_feedback(1, 1, rating, "ok")


def test_job_status_validation():
    with pytest.raises(incidents.ServiceError, match="in_progress' or 'completed"):
        incidents.update_job_status(1, 1, "cancelled")


@pytest.mark.parametrize("hours, amount, message", [
    (None, 180, "enter the hours worked and the amount"),
    (3, "", "enter the hours worked and the amount"),
    ("three", 180, "Hours worked must be a number"),
    (-2, 180, "cannot be negative"),
    (0.1, 5, "between 0.25 and 24"),
    (30, 900, "between 0.25 and 24"),
    (20, 12000, "at most €10,000"),
    (3, 2000, "€666.67 per hour"),
    (8, 20, "€2.50 per hour"),
])
def test_billing_validation(hours, amount, message):
    with pytest.raises(incidents.ServiceError, match=message):
        incidents.validate_billing(hours, amount)


def test_billing_accepts_form_strings():
    assert incidents.validate_billing("3,5", "€182") == (3.5, 182.0)
    assert incidents.validate_billing(2, 96.456) == (2.0, 96.46)


def test_billing_only_when_completing():
    with pytest.raises(incidents.ServiceError, match="only when the job is completed"):
        incidents.update_job_status(1, 1, "in_progress", hours_worked=2, amount_paid_eur=90)
    with pytest.raises(incidents.ServiceError, match="enter the hours worked"):
        incidents.update_job_status(1, 1, "completed")


@pytest.mark.parametrize("kwargs, message", [
    ({"order": "cheapest"}, "Unknown order"),
    ({"incident_type": "astrology"}, "Unknown incident type"),
    ({"min_rating": 0}, "min_rating must be"),
    ({"max_rating": "5"}, "max_rating must be"),
    ({"min_rating": 4, "max_rating": 2}, "cannot be higher"),
    ({"limit": "ten"}, "limit must be"),
])
def test_review_search_validation(kwargs, message):
    with pytest.raises(incidents.ServiceError, match=message):
        incidents.search_handyman_reviews(1, **kwargs)


# ─────────────────────────────────────────────────────────────
# Handyman insights: strongest / weakest job type
# ─────────────────────────────────────────────────────────────


def _type_row(incident_type, success, rated, rating=4.0):
    return {"incident_type": incident_type, "success_rate_percent": success, "rated_jobs": rated, "avg_rating": rating}


def test_compare_job_types_picks_weakest_and_strongest():
    rows = [_type_row("all", 70, 40), _type_row("plumbing", 85, 20), _type_row("roofing", 50, 6),
            _type_row("painting", 20, 2)]                 # painting: too few rated jobs to count
    out = incidents.compare_job_types(rows)
    assert out["weakest"]["incident_type"] == "roofing"
    assert out["strongest"]["incident_type"] == "plumbing"
    assert out["compared_types"] == 2
    assert out["gap_points"] == 35 and out["clear_difference"] is True


def test_compare_job_types_small_gap_is_about_even():
    out = incidents.compare_job_types([_type_row("plumbing", 93, 600), _type_row("heating_hvac", 95, 1000)])
    assert out["clear_difference"] is False and "about the same" in out["note"]


def test_compare_job_types_tie_prefers_more_evidence():
    out = incidents.compare_job_types([_type_row("plumbing", 60, 3), _type_row("roofing", 60, 9),
                                       _type_row("painting", 90, 5)])
    assert out["weakest"]["incident_type"] == "roofing"


def test_compare_job_types_needs_two_eligible_types():
    out = incidents.compare_job_types([_type_row("all", 70, 40), _type_row("plumbing", 85, 20),
                                       _type_row("roofing", None, 5)])
    assert out["weakest"] is None and out["strongest"] is None
    assert "not enough data" in out["note"]


class SplitSession:
    """Route Matrix (POST, car) and Routing API (GET, public transport) answered separately."""

    def __init__(self, matrix, routing):
        self.matrix, self.routing, self.routing_calls = matrix, routing, []

    def post(self, url, **k):
        return self.matrix

    def get(self, url, **k):
        self.routing_calls.append(k["params"])
        return self.routing


def test_travel_times_routes_by_car_or_public_transport(monkeypatch):
    matrix = FakeResponse(200, {"sources_to_targets": [[{"distance": 5000, "time": 600, "source_index": 0}]]})
    routing = FakeResponse(200, {"features": [{"properties": {"distance": 7000, "time": 2100}}]})
    session = SplitSession(matrix, routing)
    monkeypatch.setattr(geo, "_get_session", lambda: session)
    out = geo.travel_times([(52.5, 13.4, False), (52.51, 13.41, True)], (52.52, 13.42))
    assert out[0] == {"distance_km": 7.0, "travel_minutes": 35.0, "estimated": False, "mode": "transit"}
    assert out[1] == {"distance_km": 5.0, "travel_minutes": 10.0, "estimated": False, "mode": "drive"}
    assert [c["mode"] for c in session.routing_calls] == [geo.TRANSIT_MODE]      # only the handyman without a car


def test_transit_falls_back_to_slower_flagged_estimate(monkeypatch):
    session = SplitSession(FakeResponse(200, {}), FakeResponse(500, {}))
    monkeypatch.setattr(geo, "_get_session", lambda: session)
    transit = geo.travel_times([(52.5, 13.4, False)], (52.52, 13.42))[0]
    drive = geo.route_matrix([(52.5, 13.4)], (52.52, 13.42))[0]
    assert transit["estimated"] and transit["mode"] == "transit"
    assert transit["travel_minutes"] > drive["travel_minutes"]                     # public transport is slower


def _rows(rng, n):
    """Random candidate rows in _CANDIDATES_SQL column order."""
    skills_pool = ["pipe repair", "leak detection", "boiler", "tiling", "wiring", "sockets"]
    return [(i, f"H{i}", "Test", 52.5, 13.4, rng.sample(skills_pool, rng.randint(0, 4)), 50.0, rng.random() < 0.5,
             rng.randint(0, 2), rng.randint(0, 60), rng.randint(0, 40), None, None, None, rng.randint(0, 200),
             rng.randint(0, 150), round(rng.uniform(2.5, 5), 2), round(rng.uniform(-1, 1), 2), None, i * 0.7)
            for i in range(n)]


@pytest.mark.parametrize("seed", range(200))
def test_rank_candidates_matches_computing_every_travel_time(seed):
    """Fetching travel only where it can change the result gives exactly the top 3 of fetching it for all."""
    import random
    rng = random.Random(seed)
    rows = _rows(rng, rng.randint(3, 20))
    minutes = {r[0]: round(rng.uniform(2, 90), 1) for r in rows}
    travel = lambda idx: [{"distance_km": 1.0, "travel_minutes": minutes[rows[i][0]],  # noqa: E731
                           "estimated": False, "mode": "drive"} for i in idx]
    required = rng.sample(["pipe repair", "leak detection", "boiler", "tiling"], rng.randint(0, 3))
    weights = matching.URGENT_WEIGHTS if seed % 2 else matching.WEIGHTS
    lazy, lookups = matching.rank_candidates(rows, required, weights, travel)
    everything = []
    for i, r in enumerate(rows):
        c, base = matching._candidate(r, required, weights)
        ts = 1 - min(minutes[r[0]] / matching.TRAVEL_ZERO_AT_MIN, 1)
        everything.append((i, round((base + weights["travel"] * ts) * 100), c["handyman_id"]))
    everything.sort(key=lambda x: x[1], reverse=True)              # stable: straight-line order on ties
    expected = [(h, p) for _, p, h in everything[:matching.TOP_N]]
    assert [(c["handyman_id"], c["match_percent"]) for c in lazy] == expected
    assert lookups <= len(rows)


def test_dbx_auth_uses_the_token_without_a_service_principal(monkeypatch):
    from maintops_core import dbx_auth
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    monkeypatch.setenv("DATABRICKS_TOKEN", "user-token")
    assert dbx_auth.headers()["Authorization"] == "Bearer user-token"


def test_dbx_auth_service_principal_token_is_fetched_once_and_renewed(monkeypatch):
    from maintops_core import dbx_auth
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-id")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")
    monkeypatch.setattr(dbx_auth, "_cached", {"token": None, "expires_at": 0.0})
    calls = []

    def fake_post(url, auth, timeout, data):
        calls.append((url, auth, data["grant_type"]))
        return FakeResponse(200, {"access_token": f"t{len(calls)}", "expires_in": 3600})
    monkeypatch.setattr(dbx_auth.requests, "post", fake_post)
    assert dbx_auth.bearer() == "t1" and dbx_auth.bearer() == "t1"          # cached
    assert calls == [("https://dbc.example/oidc/v1/token", ("sp-id", "sp-secret"), "client_credentials")]
    dbx_auth._cached["expires_at"] = time.time() + 100                     # within the renewal window
    assert dbx_auth.bearer() == "t2"


def test_dbx_auth_failure_is_a_request_error(monkeypatch):
    from maintops_core import dbx_auth
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-id")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "wrong")
    monkeypatch.setattr(dbx_auth, "_cached", {"token": None, "expires_at": 0.0})
    monkeypatch.setattr(dbx_auth.requests, "post", lambda *a, **k: FakeResponse(401, {"error": "invalid_client"}))
    with pytest.raises(requests.RequestException):
        dbx_auth.bearer()
