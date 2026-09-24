-- Additive migration. Apply with psql -v ON_ERROR_STOP=1 -f ...
CREATE TABLE IF NOT EXISTS etl_run (
    interval_set_id text PRIMARY KEY,
    hole_id text NOT NULL,
    dataset_id text NOT NULL,
    region text NOT NULL,
    source_sha256 text NOT NULL,
    qc jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS interval_product (
    interval_id text PRIMARY KEY,
    interval_set_id text NOT NULL REFERENCES etl_run(interval_set_id),
    hole_id text NOT NULL,
    dataset_id text NOT NULL,
    region text NOT NULL,
    depth_from_m double precision NOT NULL,
    depth_to_m double precision NOT NULL,
    n_total integer NOT NULL,
    n_admitted integer NOT NULL,
    source_rows jsonb NOT NULL,
    source_depth_min_m double precision,
    source_depth_max_m double precision,
    features jsonb NOT NULL,
    dominant_group text,
    CHECK (depth_to_m > depth_from_m),
    CHECK (n_admitted <= n_total)
);
CREATE INDEX IF NOT EXISTS interval_product_hole_depth_idx ON interval_product(hole_id, region, depth_from_m);
ALTER TABLE interval_product ADD COLUMN IF NOT EXISTS reason_counts jsonb;
CREATE TABLE IF NOT EXISTS batch_run (
    batch_id text PRIMARY KEY,
    summary jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS model_run (
    model_id text PRIMARY KEY,
    region text NOT NULL,
    card jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS interval_assignment (
    model_id text NOT NULL REFERENCES model_run(model_id),
    interval_id text NOT NULL REFERENCES interval_product(interval_id),
    cluster_id integer,
    distance double precision,
    status text NOT NULL,
    PRIMARY KEY (model_id, interval_id)
);
CREATE INDEX IF NOT EXISTS interval_assignment_cluster_idx ON interval_assignment(model_id, cluster_id);
CREATE TABLE IF NOT EXISTS active_model (
    region text PRIMARY KEY,
    model_id text NOT NULL REFERENCES model_run(model_id)
);
