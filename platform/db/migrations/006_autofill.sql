-- Durable acquisition state; contains public catalogue IDs, never reference data.
CREATE TABLE IF NOT EXISTS autofill_control (
    id integer PRIMARY KEY CHECK (id=1),
    enabled boolean NOT NULL DEFAULT false,
    catalog_checked_at timestamptz,
    catalog_job uuid,
    analysis_job uuid,
    dirty_count integer NOT NULL DEFAULT 0,
    message text NOT NULL DEFAULT 'Ready to discover public WA data',
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO autofill_control(id) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS autofill_item (
    hole_id text NOT NULL,
    source text NOT NULL CHECK (source IN ('processed','raw')),
    state text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    retry_at timestamptz,
    job_id uuid REFERENCES job(job_id),
    error text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(hole_id,source)
);
CREATE INDEX IF NOT EXISTS autofill_pending ON autofill_item(source,state,retry_at);
-- Optional reference import metadata: an empty installation has no reference package.
CREATE TABLE IF NOT EXISTS ref_load (
    loaded_at timestamptz DEFAULT now(), sqlite_path text, sha256 text,
    build jsonb, row_counts jsonb
);
