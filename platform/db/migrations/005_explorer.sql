-- Explorer v2: statewide hole register, mineral-level interval features and
-- versioned analysis results. Reference tables (ref_*) are rebuilt by
-- hylogger.explorer.reference from the GSWA SQLite package.

CREATE TABLE IF NOT EXISTS explorer_hole (
    hole_id text PRIMARY KEY,
    name text,
    lat double precision,
    lon double precision,
    geom geography(Point, 4326),
    elevation_m double precision,
    length_m double precision,
    nvcl_available boolean NOT NULL DEFAULT false,
    raw_available boolean NOT NULL DEFAULT false,
    raw_size_bytes bigint,
    operator text,
    project text,
    drilling_method text,
    hole_type text,
    commodity text,
    tectonic_region text,
    source_database text,
    index_id integer,
    data_name text,
    wamex_collar_id bigint,
    link_method text,
    scan_swir text,
    scan_tir text,
    n_assay integer NOT NULL DEFAULT 0,
    n_geology integer NOT NULL DEFAULT 0,
    n_survey integer NOT NULL DEFAULT 0,
    mosaic_url text,
    summary_histogram_url text,
    summary_report_url text,
    mineral_log_url text,
    district_id integer,
    processed_status text NOT NULL DEFAULT 'not_fetched',
    raw_status text NOT NULL DEFAULT 'not_fetched',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS explorer_hole_geom_idx ON explorer_hole USING GIST (geom);
CREATE INDEX IF NOT EXISTS explorer_hole_collar_idx ON explorer_hole (wamex_collar_id);

-- One prepared interval product per hole, dataset revision, sensor and QC version.
CREATE TABLE IF NOT EXISTS feature_set (
    feature_set_id text PRIMARY KEY,
    hole_id text NOT NULL,
    dataset_id text NOT NULL,
    sensor text NOT NULL CHECK (sensor IN ('swir', 'tir', 'vnir')),
    width_m double precision NOT NULL,
    qc_version text NOT NULL,
    source_sha256 text NOT NULL,
    qc jsonb NOT NULL,
    product_path text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS active_feature_set (
    hole_id text NOT NULL,
    sensor text NOT NULL,
    feature_set_id text NOT NULL REFERENCES feature_set(feature_set_id),
    selected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hole_id, sensor)
);
CREATE TABLE IF NOT EXISTS interval_feature (
    feature_set_id text NOT NULL REFERENCES feature_set(feature_set_id) ON DELETE CASCADE,
    bin integer NOT NULL,
    hole_id text NOT NULL,
    sensor text NOT NULL,
    depth_from_m double precision NOT NULL,
    depth_to_m double precision NOT NULL,
    n_total integer NOT NULL,
    n_admitted integer NOT NULL,
    minerals jsonb NOT NULL,
    groups jsonb NOT NULL,
    dominant_mineral text,
    dominant_group text,
    error_mean double precision,
    PRIMARY KEY (feature_set_id, bin)
);
CREATE INDEX IF NOT EXISTS interval_feature_hole_idx ON interval_feature (hole_id, sensor, depth_from_m);

-- Analysis runs are staged first and promoted explicitly, so retraining never
-- silently changes what users see.
CREATE TABLE IF NOT EXISTS analysis_run (
    run_id text PRIMARY KEY,
    kind text NOT NULL,
    sensor text NOT NULL,
    status text NOT NULL DEFAULT 'staged' CHECK (status IN ('staged', 'active', 'archived')),
    card jsonb NOT NULL,
    product_path text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS analysis_run_one_active
    ON analysis_run (kind, sensor) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS interval_label (
    run_id text NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    feature_set_id text NOT NULL,
    bin integer NOT NULL,
    hole_id text NOT NULL,
    cluster_id integer,
    coarse_cluster_id integer,
    distance double precision,
    domain_id integer,
    anomaly_score double precision,
    anomaly_reasons text[],
    PRIMARY KEY (run_id, feature_set_id, bin)
);
CREATE INDEX IF NOT EXISTS interval_label_hole_idx ON interval_label (run_id, hole_id);
CREATE INDEX IF NOT EXISTS interval_label_anomaly_idx ON interval_label (run_id, anomaly_score DESC);

CREATE TABLE IF NOT EXISTS cluster_profile (
    run_id text NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    level text NOT NULL,
    cluster_id integer NOT NULL,
    parent_id integer,
    name text NOT NULL,
    n_intervals integer NOT NULL,
    n_holes integer NOT NULL,
    minerals jsonb NOT NULL,
    groups jsonb NOT NULL,
    chemistry jsonb,
    PRIMARY KEY (run_id, level, cluster_id)
);

CREATE TABLE IF NOT EXISTS domain (
    run_id text NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    hole_id text NOT NULL,
    domain_id integer NOT NULL,
    depth_from_m double precision NOT NULL,
    depth_to_m double precision NOT NULL,
    cluster_id integer,
    minerals jsonb NOT NULL,
    PRIMARY KEY (run_id, hole_id, domain_id)
);

CREATE TABLE IF NOT EXISTS hole_profile (
    run_id text NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    hole_id text NOT NULL,
    cluster_id integer,
    x double precision,
    y double precision,
    anomaly_score double precision,
    anomaly_reasons text[],
    summary jsonb NOT NULL,
    PRIMARY KEY (run_id, hole_id)
);

CREATE TABLE IF NOT EXISTS hole_neighbor (
    run_id text NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    hole_id text NOT NULL,
    rank integer NOT NULL,
    neighbor_id text NOT NULL,
    distance double precision NOT NULL,
    PRIMARY KEY (run_id, hole_id, rank)
);

CREATE TABLE IF NOT EXISTS raw_product (
    hole_id text NOT NULL,
    sensor text NOT NULL,
    dataset_id text,
    product_path text NOT NULL,
    n_intervals integer NOT NULL,
    depth_from_m double precision,
    depth_to_m double precision,
    summary jsonb NOT NULL,
    source jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hole_id, sensor)
);
