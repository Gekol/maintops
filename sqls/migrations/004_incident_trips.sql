-- 004_incident_trips: the handyman's trip to the client ("I'm on my way").
-- The handyman picks where they start (home, their last job, or another address) and, if they have a car,
-- whether they drive or take public transport. The route is computed with Geoapify and the client sees the
-- departure time, travel time and expected arrival as an estimate — never the starting address.
-- One row per incident: starting the trip again replaces it. A separate table, so incidents is unchanged.
CREATE TABLE IF NOT EXISTS maintops.incident_trips (
    incident_id       bigint PRIMARY KEY REFERENCES maintops.incidents (id) ON DELETE CASCADE,
    handyman_user_id  bigint NOT NULL REFERENCES maintops.users (id),
    origin_kind       varchar(16) NOT NULL,
    origin_address    varchar(256),                 -- for the handyman only; never shown to the client
    origin_latitude   double precision NOT NULL,
    origin_longitude  double precision NOT NULL,
    travel_mode       varchar(16) NOT NULL,
    distance_km       numeric(10, 2) NOT NULL,
    travel_minutes    numeric(10, 2) NOT NULL,
    estimated         boolean DEFAULT false NOT NULL,  -- true = straight-line fallback, the routing API failed
    departed_at       timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at        timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at        timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_trip_origin_kind CHECK (origin_kind IN ('home', 'last_job', 'custom')),
    CONSTRAINT chk_trip_travel_mode CHECK (travel_mode IN ('drive', 'transit')),
    CONSTRAINT chk_trip_coordinates CHECK (
        origin_latitude BETWEEN -90 AND 90 AND origin_longitude BETWEEN -180 AND 180),
    CONSTRAINT chk_trip_numbers CHECK (distance_km >= 0 AND travel_minutes >= 0)
);

CREATE INDEX IF NOT EXISTS incident_trips_handyman_idx
    ON maintops.incident_trips (handyman_user_id, departed_at DESC);

CREATE OR REPLACE TRIGGER trg_incident_trips_updated_at
    BEFORE UPDATE ON maintops.incident_trips
    FOR EACH ROW EXECUTE FUNCTION maintops.set_updated_at();

-- The traveller must be a handyman (same rule as incidents.handyman_user_id)
CREATE OR REPLACE FUNCTION maintops.check_trip_handyman() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM maintops.assert_is_handyman(NEW.handyman_user_id);
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_incident_trips_handyman
    BEFORE INSERT OR UPDATE OF handyman_user_id ON maintops.incident_trips
    FOR EACH ROW EXECUTE FUNCTION maintops.check_trip_handyman();

-- Full before-images for Change Data Feed, like the other tables
ALTER TABLE maintops.incident_trips REPLICA IDENTITY FULL;
