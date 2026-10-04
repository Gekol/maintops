"""The handyman's trip to the client ("I'm on my way") — shown to the client as an estimate.

The handyman says where they set off from (home, the client of their last job, or another address) and how they
travel: a handyman who has a car (handyman_details.has_car) chooses car or public transport; one without a car
always travels by public transport and is not offered the choice. The route is computed with Geoapify
(car: Route Matrix, public transport: Routing API) and stored in incident_trips. The client sees departure time,
travel time and expected arrival — never where the handyman set off from (that may be another client's home).
"""

from datetime import datetime, timedelta

from maintops_core import geo
from maintops_core.db import get_connection
from maintops_core.incidents import ServiceError, format_address

ORIGINS = ("home", "last_job", "custom")
MODES = ("drive", "transit")
LAST_JOB_WINDOW_HOURS = 12        # "my last job" = in progress, or completed within this window


def choose_mode(has_car: bool, requested: str | None) -> str:
    """The travel mode for a trip: a choice only for handymen with a car; without one, public transport."""
    if not has_car:
        if requested not in (None, "", "transit"):
            raise ServiceError("Your profile says you don't have a car, so the trip is by public transport. "
                               "Add a car in your profile if you have one.")
        return "transit"
    if requested not in MODES:
        raise ServiceError("Choose how you travel: by car or by public transport.")
    return requested


def expected_arrival(departed_at: datetime, travel_minutes) -> datetime:
    return departed_at + timedelta(minutes=float(travel_minutes))


def trip_origins(handyman_id: int) -> dict:
    """What the "I'm on my way" form offers: has_car, the home address, and the last job's address."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT u.house, u.postal_code, u.city, u.latitude, u.longitude, d.has_car
            FROM maintops.users u JOIN maintops.handyman_details d ON d.user_id = u.id
            WHERE u.id = %s""", (handyman_id,))
        row = cur.fetchone()
        if row is None:
            raise ServiceError("Handyman profile not found.")
        house, postal, city, lat, lon, has_car = row
        window = LAST_JOB_WINDOW_HOURS
        cur.execute("""
            SELECT i.id, c.house, c.postal_code, c.city, c.latitude, c.longitude
            FROM maintops.incidents i JOIN maintops.users c ON c.id = i.reported_by_user_id
            WHERE i.handyman_user_id = %s AND c.latitude IS NOT NULL
              AND (i.status = 'in_progress'
                   OR (i.status = 'completed' AND i.completed_at > now() - make_interval(hours => %s)))
            ORDER BY COALESCE(i.completed_at, i.assigned_at) DESC
            LIMIT 1""", (handyman_id, window))
        last = cur.fetchone()
    home = {"address": format_address(house, postal, city), "lat": lat, "lon": lon} if lat is not None else None
    last_job = ({"incident_id": last[0], "address": format_address(*last[1:4]), "lat": last[4], "lon": last[5]}
                if last else None)
    return {"has_car": bool(has_car), "home": home, "last_job": last_job}


def start_trip(handyman_id: int, incident_id: int, origin: str, mode: str | None = None,
               address: str | None = None) -> dict:
    """Record that the handyman set off for an assigned job; returns the stored trip."""
    if origin not in ORIGINS:
        raise ServiceError("Choose where you are setting off from.")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT i.status, c.latitude, c.longitude
            FROM maintops.incidents i JOIN maintops.users c ON c.id = i.reported_by_user_id
            WHERE i.id = %s AND i.handyman_user_id = %s""", (incident_id, handyman_id))
        row = cur.fetchone()
    if row is None:
        raise ServiceError(f"Job {incident_id} not found among your assigned jobs.")
    status, client_lat, client_lon = row
    if status != "assigned":
        raise ServiceError(f"Job {incident_id} is '{status}'; you can only set off for an assigned job.")
    if client_lat is None:
        raise ServiceError("The client's address has no coordinates, so no route can be computed.")

    options = trip_origins(handyman_id)
    travel_mode = choose_mode(options["has_car"], mode)
    if origin == "custom":
        address = " ".join((address or "").split())[:256]
        if not address:
            raise ServiceError("Enter the address you are setting off from.")
        try:
            found = geo.geocode(address, user_id=handyman_id)
        except geo.GeoError as exc:
            raise ServiceError(f"We couldn't find that address ({exc}). Please check it and try again.") from exc
        start = {"address": found.get("formatted") or address, "lat": found["lat"], "lon": found["lon"]}
    else:
        start = options[origin]
        if start is None:
            raise ServiceError("You have no job in progress or finished in the last hours to set off from."
                               if origin == "last_job" else "Your profile has no address with coordinates.")

    source, target = (float(start["lat"]), float(start["lon"])), (float(client_lat), float(client_lon))
    route = (geo.route_matrix if travel_mode == "drive" else geo.transit_times)(
        [source], target, user_id=handyman_id, incident_id=incident_id)[0]

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO maintops.incident_trips
              (incident_id, handyman_user_id, origin_kind, origin_address, origin_latitude, origin_longitude,
               travel_mode, distance_km, travel_minutes, estimated, departed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (incident_id) DO UPDATE SET
              origin_kind = EXCLUDED.origin_kind, origin_address = EXCLUDED.origin_address,
              origin_latitude = EXCLUDED.origin_latitude, origin_longitude = EXCLUDED.origin_longitude,
              travel_mode = EXCLUDED.travel_mode, distance_km = EXCLUDED.distance_km,
              travel_minutes = EXCLUDED.travel_minutes, estimated = EXCLUDED.estimated,
              departed_at = EXCLUDED.departed_at
            RETURNING departed_at""",
                    (incident_id, handyman_id, origin, start["address"], source[0], source[1], travel_mode,
                     route["distance_km"], route["travel_minutes"], route["estimated"]))
        departed_at = cur.fetchone()[0]
        conn.commit()
    return {"incident_id": incident_id, "origin_kind": origin, "origin_address": start["address"],
            "travel_mode": travel_mode, "distance_km": route["distance_km"],
            "travel_minutes": route["travel_minutes"], "estimated": route["estimated"],
            "departed_at": departed_at,
            "expected_arrival": expected_arrival(departed_at, route["travel_minutes"])}


def trips_for(incident_ids) -> dict:
    """{incident_id: trip} for the dashboards (origin_address is for the handyman's view only)."""
    ids = [int(i) for i in incident_ids or []]
    if not ids:
        return {}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT incident_id, origin_kind, origin_address, travel_mode, distance_km, travel_minutes,
                   estimated, departed_at
            FROM maintops.incident_trips WHERE incident_id = ANY(%s)""", (ids,))
        rows = cur.fetchall()
    keys = ("incident_id", "origin_kind", "origin_address", "travel_mode", "distance_km", "travel_minutes",
            "estimated", "departed_at")
    trips = {}
    for r in rows:
        t = dict(zip(keys, r, strict=True))
        t["expected_arrival"] = expected_arrival(t["departed_at"], t["travel_minutes"])
        trips[t["incident_id"]] = t
    return trips
