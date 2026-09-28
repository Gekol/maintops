-- 002_handyman_scorecards: read models written by the Spark pipeline (gold tables) and read by find_handymen.
-- Recomputed by the batch pipeline and, per affected handyman, by the live stream within a minute of a change.

CREATE TABLE IF NOT EXISTS maintops.handyman_performance (
    handyman_user_id     bigint NOT NULL REFERENCES maintops.users (id) ON DELETE CASCADE,
    incident_type        varchar(64) NOT NULL,          -- a specialisation, or 'all' for the overall row
    jobs_completed       integer DEFAULT 0 NOT NULL,
    jobs_cancelled       integer DEFAULT 0 NOT NULL,
    jobs_active          integer DEFAULT 0 NOT NULL,
    rated_jobs           integer DEFAULT 0 NOT NULL,
    successful_jobs      integer DEFAULT 0 NOT NULL,    -- rated 4 or 5
    success_rate         numeric(5, 4),                 -- successful_jobs / rated_jobs
    avg_rating           numeric(3, 2),
    avg_resolution_hours numeric(10, 2),                -- assigned → completed
    avg_travel_minutes   numeric(10, 2),
    last_job_at          timestamp,
    computed_at          timestamp NOT NULL,
    updated_at           timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (handyman_user_id, incident_type),
    CONSTRAINT chk_performance_type CHECK (incident_type IN (
        'all', 'plumbing', 'electrical', 'heating_hvac', 'carpentry', 'painting',
        'roofing', 'flooring', 'appliance_repair', 'locksmith', 'general_maintenance')),
    CONSTRAINT chk_performance_counts CHECK (
        jobs_completed >= 0 AND jobs_cancelled >= 0 AND jobs_active >= 0
        AND rated_jobs >= 0 AND successful_jobs BETWEEN 0 AND rated_jobs),
    CONSTRAINT chk_performance_rates CHECK (
        (success_rate IS NULL OR success_rate BETWEEN 0 AND 1)
        AND (avg_rating IS NULL OR avg_rating BETWEEN 1 AND 5))
);

CREATE TABLE IF NOT EXISTS maintops.handyman_feedback (
    handyman_user_id    bigint PRIMARY KEY REFERENCES maintops.users (id) ON DELETE CASCADE,
    review_count        integer DEFAULT 0 NOT NULL,
    recent_review_count integer DEFAULT 0 NOT NULL,     -- reviews behind the sentiment and summary (latest 20)
    positive_share      numeric(5, 4),
    negative_share      numeric(5, 4),
    sentiment_score     numeric(5, 4),                  -- positive_share - negative_share, -1..1
    review_summary      text,                           -- LLM summary of strengths / recurring complaints
    summary_updated_at  timestamp,
    last_review_at      timestamp,
    computed_at         timestamp NOT NULL,
    updated_at          timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_feedback_shares CHECK (
        (positive_share IS NULL OR positive_share BETWEEN 0 AND 1)
        AND (negative_share IS NULL OR negative_share BETWEEN 0 AND 1)
        AND (sentiment_score IS NULL OR sentiment_score BETWEEN -1 AND 1))
);

CREATE OR REPLACE TRIGGER trg_handyman_performance_updated_at
    BEFORE UPDATE ON maintops.handyman_performance
    FOR EACH ROW EXECUTE FUNCTION maintops.set_updated_at();
CREATE OR REPLACE TRIGGER trg_handyman_feedback_updated_at
    BEFORE UPDATE ON maintops.handyman_feedback
    FOR EACH ROW EXECUTE FUNCTION maintops.set_updated_at();

ALTER TABLE maintops.handyman_performance REPLICA IDENTITY FULL;
ALTER TABLE maintops.handyman_feedback    REPLICA IDENTITY FULL;
