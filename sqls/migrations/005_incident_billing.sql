-- 005_incident_billing: what a completed job took and cost. The handyman enters both when completing the job;
-- the pipeline derives each handyman's hourly rate (handyman_details.avg_price) from them.
-- Both or neither, and only on completed jobs (jobs completed before this migration were backfilled).
ALTER TABLE maintops.incidents
    ADD COLUMN IF NOT EXISTS hours_worked numeric(5, 2),
    ADD COLUMN IF NOT EXISTS amount_paid_eur numeric(10, 2);

ALTER TABLE maintops.incidents
    DROP CONSTRAINT IF EXISTS chk_incident_billing,
    ADD CONSTRAINT chk_incident_billing CHECK (
        (hours_worked IS NULL AND amount_paid_eur IS NULL)
        OR (status = 'completed' AND hours_worked > 0 AND hours_worked <= 24 AND amount_paid_eur >= 0)
    );
