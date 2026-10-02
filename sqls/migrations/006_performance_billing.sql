-- 006_performance_billing: billed hours and amounts on the scorecards, and the hourly rate derived from them.
-- hourly_rate = amount paid / hours worked over the handyman's latest billed jobs (pipeline_lib.compute_performance);
-- the pipeline copies the overall row's rate into handyman_details.avg_price.
ALTER TABLE maintops.handyman_performance
    ADD COLUMN IF NOT EXISTS billed_jobs integer DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS hours_billed numeric(12, 2) DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS amount_billed numeric(14, 2) DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS hourly_rate numeric(10, 2);

ALTER TABLE maintops.handyman_performance
    DROP CONSTRAINT IF EXISTS chk_performance_billing,
    ADD CONSTRAINT chk_performance_billing CHECK (
        billed_jobs >= 0 AND hours_billed >= 0 AND amount_billed >= 0
        AND (hourly_rate IS NULL OR hourly_rate >= 0)
    );
