-- 001_hardening: audit timestamps, integrity rules, indexes, event log and CDF prerequisites.
-- Idempotent: every statement can be re-applied (IF NOT EXISTS / OR REPLACE / DROP ... IF EXISTS + ADD).

-- ─────────────────────────────────────────────────────────────
-- Audit timestamps: every table has created_at/updated_at, kept current by one trigger function
-- ─────────────────────────────────────────────────────────────
ALTER TABLE maintops.users
    ADD COLUMN IF NOT EXISTS updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL;
ALTER TABLE maintops.handyman_details
    ADD COLUMN IF NOT EXISTS created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    ADD COLUMN IF NOT EXISTS updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL;

CREATE OR REPLACE FUNCTION maintops.set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON maintops.users
    FOR EACH ROW EXECUTE FUNCTION maintops.set_updated_at();
CREATE OR REPLACE TRIGGER trg_handyman_details_updated_at
    BEFORE UPDATE ON maintops.handyman_details
    FOR EACH ROW EXECUTE FUNCTION maintops.set_updated_at();
CREATE OR REPLACE TRIGGER trg_incidents_updated_at
    BEFORE UPDATE ON maintops.incidents
    FOR EACH ROW EXECUTE FUNCTION maintops.set_updated_at();

-- ─────────────────────────────────────────────────────────────
-- Users
-- ─────────────────────────────────────────────────────────────
-- Emails are unique regardless of case (Foo@x.com and foo@x.com are the same account)
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_key ON maintops.users (lower(email));

ALTER TABLE maintops.users
    DROP CONSTRAINT IF EXISTS chk_user_coordinates,
    ADD CONSTRAINT chk_user_coordinates CHECK (
        (latitude IS NULL AND longitude IS NULL)
        OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
    );

-- Handyman locations for the geographic pre-filter in find_handymen
CREATE INDEX IF NOT EXISTS users_handyman_location_idx
    ON maintops.users (latitude, longitude) WHERE is_handyman AND is_active;

-- ─────────────────────────────────────────────────────────────
-- Handyman details
-- ─────────────────────────────────────────────────────────────
-- Specialisations must come from the controlled vocabulary (SPECIALISATIONS in app.py / 00_config)
ALTER TABLE maintops.handyman_details
    DROP CONSTRAINT IF EXISTS chk_handyman_specialisations,
    ADD CONSTRAINT chk_handyman_specialisations CHECK (
        specialisations IS NULL OR specialisations <@ ARRAY[
            'plumbing', 'electrical', 'heating_hvac', 'carpentry', 'painting',
            'roofing', 'flooring', 'appliance_repair', 'locksmith', 'general_maintenance'
        ]::text[]
    ),
    DROP CONSTRAINT IF EXISTS chk_handyman_avg_price,
    ADD CONSTRAINT chk_handyman_avg_price CHECK (avg_price IS NULL OR avg_price >= 0);

CREATE INDEX IF NOT EXISTS handyman_details_specialisations_idx
    ON maintops.handyman_details USING gin (specialisations);
CREATE INDEX IF NOT EXISTS handyman_details_skills_idx
    ON maintops.handyman_details USING gin (skills);

-- A handyman_details row (and an incident assignment) must point at a user with is_handyman = true
CREATE OR REPLACE FUNCTION maintops.assert_is_handyman(uid bigint) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF uid IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM maintops.users WHERE id = uid AND is_handyman
    ) THEN
        RAISE EXCEPTION 'user % is not a handyman', uid USING ERRCODE = 'check_violation';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION maintops.check_handyman_details_user() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM maintops.assert_is_handyman(NEW.user_id);
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_handyman_details_user
    BEFORE INSERT OR UPDATE OF user_id ON maintops.handyman_details
    FOR EACH ROW EXECUTE FUNCTION maintops.check_handyman_details_user();

-- ─────────────────────────────────────────────────────────────
-- Incidents
-- ─────────────────────────────────────────────────────────────
ALTER TABLE maintops.incidents
    DROP CONSTRAINT IF EXISTS chk_incident_description,
    ADD CONSTRAINT chk_incident_description CHECK (length(btrim(description)) BETWEEN 5 AND 2000),
    -- Assigned, in-progress and completed incidents always have a handyman
    DROP CONSTRAINT IF EXISTS chk_incident_handyman_required,
    ADD CONSTRAINT chk_incident_handyman_required CHECK (
        status IN ('open', 'recommended', 'cancelled') OR handyman_user_id IS NOT NULL
    ),
    DROP CONSTRAINT IF EXISTS chk_incident_not_self_assigned,
    ADD CONSTRAINT chk_incident_not_self_assigned CHECK (handyman_user_id <> reported_by_user_id),
    -- Lifecycle timestamps are in order
    DROP CONSTRAINT IF EXISTS chk_incident_timestamps,
    ADD CONSTRAINT chk_incident_timestamps CHECK (
        (assigned_at IS NULL OR assigned_at >= created_at)
        AND (completed_at IS NULL OR (assigned_at IS NOT NULL AND completed_at >= assigned_at))
    ),
    DROP CONSTRAINT IF EXISTS chk_incident_completed_at,
    ADD CONSTRAINT chk_incident_completed_at CHECK (status <> 'completed' OR completed_at IS NOT NULL),
    -- Ratings and feedback only for completed work
    DROP CONSTRAINT IF EXISTS chk_incident_feedback_completed,
    ADD CONSTRAINT chk_incident_feedback_completed CHECK (
        (rating IS NULL AND feedback IS NULL) OR status = 'completed'
    ),
    DROP CONSTRAINT IF EXISTS chk_incident_feedback_length,
    ADD CONSTRAINT chk_incident_feedback_length CHECK (feedback IS NULL OR length(feedback) <= 2000),
    DROP CONSTRAINT IF EXISTS chk_incident_travel,
    ADD CONSTRAINT chk_incident_travel CHECK (
        (distance_km IS NULL OR distance_km >= 0) AND (travel_time_minutes IS NULL OR travel_time_minutes >= 0)
    );

CREATE OR REPLACE FUNCTION maintops.check_incident_handyman() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM maintops.assert_is_handyman(NEW.handyman_user_id);
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_incidents_handyman
    BEFORE INSERT OR UPDATE OF handyman_user_id ON maintops.incidents
    FOR EACH ROW EXECUTE FUNCTION maintops.check_incident_handyman();

-- A client cannot submit the same problem twice while the first one is still open
CREATE UNIQUE INDEX IF NOT EXISTS incidents_no_duplicate_open_idx
    ON maintops.incidents (reported_by_user_id, md5(lower(btrim(description))))
    WHERE status IN ('open', 'recommended');

CREATE INDEX IF NOT EXISTS incidents_reporter_idx
    ON maintops.incidents (reported_by_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS incidents_handyman_status_idx
    ON maintops.incidents (handyman_user_id, status);
CREATE INDEX IF NOT EXISTS incidents_status_idx
    ON maintops.incidents (status, created_at DESC);

-- ─────────────────────────────────────────────────────────────
-- App events: agent requests, tool calls, external API calls, UI actions, guardrail decisions.
-- Feeds the analytics pipeline through Lakebase CDF.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS maintops.app_events (
    id            bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    created_at    timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    event_type    varchar(32) NOT NULL,
    name          varchar(64) NOT NULL,
    request_id    varchar(64),
    user_id       bigint REFERENCES maintops.users (id) ON DELETE SET NULL,
    incident_id   bigint REFERENCES maintops.incidents (id) ON DELETE SET NULL,
    success       boolean NOT NULL,
    error         text,
    latency_ms    integer,
    input_tokens  integer,
    output_tokens integer,
    details       jsonb,
    CONSTRAINT chk_event_type CHECK (
        event_type IN ('agent_request', 'tool_call', 'api_call', 'ui_action', 'guardrail')
    ),
    CONSTRAINT chk_event_numbers CHECK (
        (latency_ms IS NULL OR latency_ms >= 0)
        AND (input_tokens IS NULL OR input_tokens >= 0)
        AND (output_tokens IS NULL OR output_tokens >= 0)
    )
);

CREATE INDEX IF NOT EXISTS app_events_created_idx ON maintops.app_events (created_at);
CREATE INDEX IF NOT EXISTS app_events_type_name_idx ON maintops.app_events (event_type, name, created_at);
CREATE INDEX IF NOT EXISTS app_events_user_idx ON maintops.app_events (user_id, created_at);
CREATE INDEX IF NOT EXISTS app_events_request_idx ON maintops.app_events (request_id);

-- ─────────────────────────────────────────────────────────────
-- Lakebase CDF: capture full before/after images of every change
-- ─────────────────────────────────────────────────────────────
ALTER TABLE maintops.users            REPLICA IDENTITY FULL;
ALTER TABLE maintops.handyman_details REPLICA IDENTITY FULL;
ALTER TABLE maintops.incidents        REPLICA IDENTITY FULL;
ALTER TABLE maintops.app_events       REPLICA IDENTITY FULL;
