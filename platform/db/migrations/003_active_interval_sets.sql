-- Select one reviewed source/width/QC revision per hole and sensor.
-- Historical interval products and assignments remain available by exact ID.
CREATE TABLE IF NOT EXISTS active_interval_set (
    hole_id text NOT NULL,
    region text NOT NULL,
    interval_set_id text NOT NULL REFERENCES etl_run(interval_set_id),
    selected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hole_id, region)
);
-- A legacy pair with exactly one revision is unambiguous. Multiple versions
-- require an explicit ETL load/selection and are never silently pooled.
INSERT INTO active_interval_set(hole_id, region, interval_set_id)
SELECT hole_id, region, min(interval_set_id) FROM etl_run
GROUP BY hole_id, region HAVING count(*) = 1
ON CONFLICT (hole_id, region) DO NOTHING;
CREATE INDEX IF NOT EXISTS interval_product_set_depth_idx
ON interval_product(interval_set_id, depth_from_m);
CREATE TABLE IF NOT EXISTS interval_set_summary (
    interval_set_id text PRIMARY KEY REFERENCES etl_run(interval_set_id),
    summary jsonb NOT NULL
);
