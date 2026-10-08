-- Background jobs. The API only enqueues; the worker claims with SKIP LOCKED.
CREATE TABLE IF NOT EXISTS job (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL,
    lane text NOT NULL DEFAULT 'analysis',
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'cancel_requested', 'succeeded', 'failed', 'cancelled')
    ),
    priority integer NOT NULL DEFAULT 100,
    parent_id uuid REFERENCES job(job_id) ON DELETE SET NULL,
    progress jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb,
    error text,
    attempts integer NOT NULL DEFAULT 0,
    worker text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    heartbeat_at timestamptz
);
CREATE INDEX IF NOT EXISTS job_claim_idx ON job (lane, status, priority, created_at);
CREATE INDEX IF NOT EXISTS job_created_idx ON job (created_at DESC);

CREATE TABLE IF NOT EXISTS job_log (
    id bigserial PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES job(job_id) ON DELETE CASCADE,
    ts timestamptz NOT NULL DEFAULT now(),
    level text NOT NULL DEFAULT 'info',
    message text NOT NULL
);
CREATE INDEX IF NOT EXISTS job_log_job_idx ON job_log (job_id, id);
