-- 003_incident_required_skills: the skills Manny derived from the client's description,
-- used by find_handymen for skill matching (specialisation = coarse filter, skills = fine match).
ALTER TABLE maintops.incidents ADD COLUMN IF NOT EXISTS required_skills text[];

ALTER TABLE maintops.incidents
    DROP CONSTRAINT IF EXISTS chk_incident_required_skills,
    ADD CONSTRAINT chk_incident_required_skills CHECK (
        required_skills IS NULL OR cardinality(required_skills) <= 10
    );
