-- Schema for the HyLogger platform.
--
-- Runs only when a new database volume is created. Apply later additive changes
-- from db/migrations; never delete a populated volume to migrate.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------
-- hole: one row per drill hole. ~1,900 rows. This is the map layer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hole (
    hole_id          text PRIMARY KEY,
    lat              double precision NOT NULL,
    lon              double precision NOT NULL,
    -- The reason for PostGIS. Lets us ask "holes within 50 km of here" or
    -- "holes inside this polygon" as an indexed query instead of a table scan.
    geom             geography(Point, 4326) NOT NULL,

    length_m         double precision,
    -- length_m straight from the source can be absent or nonsense (hole YGD4
    -- reports -115,224 m). Keep the raw value, and flag it, rather than
    -- silently dropping the row or quietly "fixing" the client's data.
    length_is_valid  boolean NOT NULL DEFAULT true,

    drilling_method  text,
    operator         text,
    project          text,
    tenement         text,
    drill_start_date text,

    loaded_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS hole_geom_idx      ON hole USING GIST (geom);
CREATE INDEX IF NOT EXISTS hole_operator_idx  ON hole (operator);
CREATE INDEX IF NOT EXISTS hole_project_idx   ON hole (project);
CREATE INDEX IF NOT EXISTS hole_length_idx    ON hole (length_m) WHERE length_is_valid;

COMMENT ON TABLE hole IS
    'One row per NVCL HyLogger-scanned drill hole in WA. Source: GSWA NVCL WFS via nvcl-kit.';
COMMENT ON COLUMN hole.length_is_valid IS
    'false when the source length_m is null or <= 0. Kept visible rather than dropped.';


-- ---------------------------------------------------------------------------
-- hole_interval: earlier depth-binned TSG interpretation table.
-- This is what draws a strip log without touching a single spectrum.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hole_interval (
    hole_id       text NOT NULL REFERENCES hole(hole_id) ON DELETE CASCADE,
    depth_from_m  double precision NOT NULL,
    depth_to_m    double precision NOT NULL,
    mineral_group text,
    mineral       text,
    weight        double precision,   -- TSG fit weight, not rock mass percentage
    n_readings    integer,            -- how many raw readings this bin summarises
    PRIMARY KEY (hole_id, depth_from_m, mineral_group)
);

CREATE INDEX IF NOT EXISTS hole_interval_hole_idx ON hole_interval (hole_id, depth_from_m);


-- ---------------------------------------------------------------------------
-- hole_feature: one feature vector per hole. The input to clustering, and
-- where cluster labels come back to. Empty until the ML workstream fills it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hole_feature (
    hole_id      text PRIMARY KEY REFERENCES hole(hole_id) ON DELETE CASCADE,
    features     jsonb NOT NULL,      -- {"CHLORITE": 0.31, "CARBONATE": 0.12, ...}
    cluster_id   integer,             -- filled by the clustering job
    anomaly_score double precision,   -- filled by the anomaly job
    model_version text,               -- which run produced this; never overwrite blindly
    computed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS hole_feature_cluster_idx ON hole_feature (cluster_id);
