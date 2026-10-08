-- Worker presence, so the UI can say whether queued jobs will run.
CREATE TABLE IF NOT EXISTS worker (
    name text PRIMARY KEY,
    lanes text NOT NULL,
    host text,
    started_at timestamptz NOT NULL DEFAULT now(),
    seen_at timestamptz NOT NULL DEFAULT now()
);
