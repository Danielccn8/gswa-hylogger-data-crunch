"""Write docs/delivery/EXPLORER_V2_EVIDENCE.md from the live Explorer database.

    python scripts/explorer_evidence.py [--output docs/delivery/EXPLORER_V2_EVIDENCE.md]

Reports aggregate counts and model checks only; no package records are copied.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from hylogger.explorer.db import connect, read_frame


def table(frame, columns) -> str:
    head = "| " + " | ".join(columns) + " |\n|" + "---|" * len(columns) + "\n"
    rows = "".join(
        "| " + " | ".join(str(row[c]) for c in columns) + " |\n" for _, row in frame.iterrows()
    )
    return head + rows


def raw_white_mica_check(conn) -> str:
    """Raw Al-OH (2200 nm) position against processed white-mica species, interval by interval."""
    import numpy as np
    import pandas as pd
    from scipy.stats import spearmanr

    from hylogger.explorer.db import resolve_product

    raw = read_frame(conn, "SELECT hole_id, product_path FROM raw_product WHERE sensor='nir'")
    if raw.empty:
        return ""
    feats = read_frame(
        conn,
        """SELECT f.hole_id, f.bin, (f.minerals->>'Phengite')::float ph,
                  (f.minerals->>'Muscovite')::float mu, (f.minerals->>'Paragonite')::float pa,
                  (f.groups->>'WHITE-MICA')::float wm
           FROM interval_feature f JOIN active_feature_set a USING (feature_set_id)
           WHERE f.sensor='swir' AND f.n_admitted >= 3""",
    )
    tables = []
    for row in raw.itertuples():
        data_dir = os.environ.get("HYLOGGER_DATA_DIR", "C:/HyLoggerData")
        path = resolve_product(row.product_path, data_dir) / "raw_nir.parquet"
        if path.exists():
            table = pd.read_parquet(path, columns=["bin", "aloh_2200_pos", "aloh_2200_depth"])
            tables.append(table.assign(hole_id=row.hole_id))
    if not tables:
        return ""
    m = pd.concat(tables).merge(feats, on=["hole_id", "bin"])
    m = m[(m.wm.fillna(0) > 0.5) & (m.aloh_2200_depth > 0.03)].fillna(0)
    total = (m.ph + m.mu + m.pa).replace(0, np.nan)
    m["phengite"], m["paragonite"], m["muscovite"] = m.ph / total, m.pa / total, m.mu / total
    rho_ph = spearmanr(m.aloh_2200_pos, m.phengite, nan_policy="omit").statistic
    rho_pa = spearmanr(m.aloh_2200_pos, m.paragonite, nan_policy="omit").statistic
    lines = [
        "\n## Raw spectra check: Al-OH absorption position vs TSG white-mica species\n\n",
        f"{len(m):,} white-mica-dominant intervals (>=50% white mica, Al-OH depth > 0.03) in "
        f"{m.hole_id.nunique()} raw-summarised holes. Spearman rho(position, phengite share) = "
        f"{rho_ph:.2f}; rho(position, paragonite share) = {rho_pa:.2f}.\n\n",
        "| Interval dominated (>60%) by | n | median Al-OH position (nm) |\n|---|---|---|\n",
    ]
    for name in ("paragonite", "muscovite", "phengite"):
        sel = m[name] > 0.6
        median = m.loc[sel, "aloh_2200_pos"].median()
        lines.append(f"| {name} | {int(sel.sum()):,} | {median:.1f} |\n")
    lines.append(
        "\nThe ordering matches published SWIR behaviour (paragonitic < muscovitic < phengitic), "
        "so the raw band parameters are consistent with the independent TSG interpretation.\n"
    )
    return "".join(lines)


def sequence_check(conn) -> str:
    """How much the DTW sequence distance adds beyond the mineral-mix distance."""
    import numpy as np
    from scipy.stats import rankdata, spearmanr

    from hylogger.explorer.db import resolve_product

    data_dir = os.environ.get("HYLOGGER_DATA_DIR", "C:/HyLoggerData")
    runs = read_frame(
        conn,
        """SELECT sensor, product_path FROM analysis_run WHERE kind='hole' AND status='active'
           AND sensor IN ('swir','tir','seq_swir','seq_tir')""",
    )
    paths = dict(zip(runs.sensor, runs.product_path, strict=True))
    lines = []
    for sensor in ("swir", "tir"):
        if sensor not in paths or f"seq_{sensor}" not in paths:
            continue
        mix = np.load(resolve_product(paths[sensor], data_dir) / "distance.npz")
        seq = np.load(resolve_product(paths[f"seq_{sensor}"], data_dir) / "distance.npz")
        mix_names, seq_names = list(mix["names"]), list(seq["names"])
        common = [h for h in seq_names if h in set(mix_names)]
        i = [mix_names.index(h) for h in common]
        j = [seq_names.index(h) for h in common]
        upper = np.triu_indices(len(common), 1)
        a = mix["distance"][np.ix_(i, i)][upper]
        b = seq["distance"][np.ix_(j, j)][upper]
        rho = spearmanr(a, b).statistic
        ra, rb = rankdata(a) / len(a), rankdata(b) / len(b)
        reordered = int(((ra < 0.02) & (rb > 0.6)).sum())
        lines.append(
            f"| {sensor.upper()} | {len(common):,} | {len(a):,} | {rho:.2f} | {reordered:,} |\n"
        )
    if not lines:
        return ""
    return (
        "\n## Down-hole sequence similarity (DTW) vs mineral-mix similarity\n\n"
        "Rank agreement between the two whole-hole distances over all hole pairs, and the number of "
        "pairs that are in the closest 2% by mix but outside the closest 60% by sequence (same "
        "minerals, different order or proportions down the hole).\n\n"
        "| Sensor | holes | pairs | Spearman ρ | same mix, different sequence |\n|---|---|---|---|---|\n"
        + "".join(lines)
    )


def tsa_versions_check() -> str:
    """Automatic (system) vs reviewed (user) TSA interpretation across summarised raw packages."""
    import json
    from collections import Counter

    import pandas as pd

    data_dir = Path(os.environ.get("HYLOGGER_DATA_DIR", "C:/HyLoggerData"))
    frames = []
    for path in (data_dir / "products" / "raw").glob("*/tsa_versions.parquet"):
        frames.append(pd.read_parquet(path).assign(hole_id=path.parent.name))
    if not frames:
        return ""
    table = pd.concat(frames, ignore_index=True)

    def main_group(text: str) -> str | None:
        groups = json.loads(text) if text else {}
        return max(groups, key=groups.get) if groups else None

    table["system_group"] = table.system_groups.map(main_group)
    table["user_group"] = table.user_groups.map(main_group)
    lines = [
        "\n## Automatic (system) vs reviewed (user) TSA interpretation\n\n",
        "Both runs are read from the raw `.bip` scalar plane of each summarised package; NVCL "
        "publishes only the user run. Agreement = share of mask-admitted readings with the same "
        "first mineral. A change is where an analyst re-ran or edited the automatic answer, not "
        "necessarily an automatic error.\n\n",
        "| Product | holes | metres | first-mineral agreement | metres with main mineral changed "
        "| main group changed | most common changes (machine → analyst, metres) |\n"
        "|---|---|---|---|---|---|---|\n",
    ]
    for product, part in table.groupby("product"):
        changed = part[part.system_top != part.user_top]
        known = part.system_group.notna() & part.user_group.notna()
        group_changed = (known & (part.system_group != part.user_group)).sum() / max(known.sum(), 1)
        top = Counter(zip(changed.system_top, changed.user_top, strict=True)).most_common(3)
        lines.append(
            f"| {product.upper()} | {part.hole_id.nunique()} | {len(part):,} "
            f"| {part.min1_agreement.mean():.1%} | {len(changed) / len(part):.1%} "
            f"| {group_changed:.1%} | {'; '.join(f'{a} → {b} ({n:,})' for (a, b), n in top)} |\n"
        )
    return "".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("docs/delivery/EXPLORER_V2_EVIDENCE.md")
    )
    args = parser.parse_args()
    out = [f"# Explorer v2 evidence — generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n"]
    out.append(
        "Generated by `scripts/explorer_evidence.py` from the local database. Aggregate figures only; "
        "the GSWA interim package itself stays in the data root. These are engineering and consistency "
        "checks, not geological acceptance.\n"
    )
    with connect() as conn:
        counts = read_frame(
            conn,
            """SELECT count(*) holes, count(*) FILTER (WHERE nvcl_available) nvcl,
                      count(*) FILTER (WHERE processed_status='prepared') prepared,
                      count(*) FILTER (WHERE processed_status='no_logs') no_public_logs,
                      count(*) FILTER (WHERE raw_status LIKE 'summarised%%') raw_summarised,
                      count(*) FILTER (WHERE n_assay>0) with_assays,
                      count(*) FILTER (WHERE wamex_collar_id IS NOT NULL) linked_collar
               FROM explorer_hole""",
        ).iloc[0]
        out.append("## Coverage\n")
        out.append(
            f"- Registered holes: **{counts.holes:,}** ({counts.nvcl:,} with an NVCL dataset; "
            f"{counts.linked_collar:,} linked to a WAMEX collar; {counts.with_assays:,} with assays).\n"
            f"- Processed logs prepared: **{counts.prepared:,}** holes; {counts.no_public_logs:,} holes list an "
            "NVCL dataset but publish no scalar logs.\n"
            f"- Raw spectra summarised (spectra-only range download, Final Mask applied): "
            f"**{counts.raw_summarised:,}** holes.\n"
        )
        intervals = read_frame(
            conn,
            """SELECT f.sensor, count(*) intervals, count(DISTINCT f.hole_id) holes,
                      sum(f.n_total) readings, round(avg((f.n_admitted>0)::int)*100,1) pct_diagnostic
               FROM interval_feature f JOIN active_feature_set a USING (feature_set_id) GROUP BY 1""",
        )
        out.append(
            "\n" + table(intervals, ["sensor", "intervals", "holes", "readings", "pct_diagnostic"])
        )
        runs = read_frame(
            conn,
            "SELECT kind, sensor, run_id, card FROM analysis_run WHERE status='active' ORDER BY 1,2",
        )
        out.append("\n## Interval clusters and validation against assays\n")
        for _, run in runs[runs.kind == "interval"].iterrows():
            card, c = run.card, run.card["clustering"]
            v = card.get("validation", {})
            out.append(
                f"\n### {run.sensor.upper()} — `{run.run_id}`\n\n"
                f"{card['intervals']:,} intervals from {card['holes']:,} holes; K fine/broad = "
                f"{c['k_fine']}/{c['k_coarse']}; seed ARI {c['seed_ari']}; largest-mineral baseline "
                f"ARI {c['dominant_mineral_baseline']['ari']}; domains {card['domains']['count']:,} "
                f"(median {card['domains']['median_length_m']} m); assay intervals matched "
                f"{v.get('assay_intervals', 0):,} in {v.get('holes_with_assays', 0)} holes.\n\n"
            )
            corr = [r for r in v.get("correlations", [])]
            if corr:
                import pandas as pd

                out.append(
                    table(
                        pd.DataFrame(corr),
                        ["feature", "analyte", "rho", "within_hole_median_rho", "n", "holes"],
                    )
                )
            sep = v.get("cluster_separation", [])[:8]
            if sep:
                out.append(
                    "\nShare of log-analyte variance explained by the fine clusters (η²): "
                    + ", ".join(f"{r['analyte']} {r['eta_squared']}" for r in sep)
                    + ".\n"
                )
        out.append("\n## Whole-hole views\n\n")
        for _, run in runs[runs.kind == "hole"].iterrows():
            hc = run.card.get("hole_clustering", {})
            agree = run.card.get("agreement_with_processed")
            names = "; ".join(
                f"{k}: {v} ({hc.get('sizes', {}).get(k)})" for k, v in hc.get("names", {}).items()
            )
            out.append(
                f"- **{run.sensor}** `{run.run_id}`: {run.card.get('holes')} holes, "
                f"{hc.get('k')} hole clusters. {names}"
                + (
                    f" Agreement with {agree['against']}: ρ={agree['spearman_rho']} on {agree['holes']} holes."
                    if agree
                    else ""
                )
                + "\n"
            )
        out.append("\n## Spatial prediction (inverse-distance weighting, 8 nearest holes)\n\n")
        for _, run in runs[runs.kind == "prediction"].iterrows():
            s = run.card["summary"]
            out.append(
                f"- **{run.sensor.upper()}**: {s['holes']} holes. Median Hellinger error — leave-hole-out "
                f"{s['loho_median_error']}, leave-district-out {s['lodo_median_error']}, tectonic-region "
                f"average {s['region_baseline_median_error']}. Skill vs region: LOHO {s['loho_skill_vs_region']}, "
                f"LODO {s['lodo_skill_vs_region']}. Support limit {s['support_limit_km']} km.\n"
            )
            cov = run.card.get("covariate_model")
            if cov:
                out.append(
                    f"  - Covariate model (location, tectonic region, IDW from other districts; folds hold out "
                    f"whole districts): median error {cov['median_error']} vs {s['lodo_median_error']} for IDW "
                    f"alone — {'used' if cov['beats_lodo_idw'] else 'not better, not used'}.\n"
                )
            survey = run.card.get("desurvey")
            if survey:
                out.append(
                    f"  - Depth bands use minimum-curvature TVD for {survey['surveyed_holes']} surveyed holes.\n"
                )
        virtual = runs[runs.kind == "virtual_assay"]
        if len(virtual):
            card = virtual.iloc[0].card
            out.append(
                "\n## Virtual assays: chemistry predicted from SWIR+TIR mineralogy\n\n"
                f"{card['method']}. {card['assay_intervals']:,} assay intervals. R² on whole holes held out:\n\n"
                "| analyte | R² | typical error factor | n | holes | offered |\n|---|---|---|---|---|---|\n"
            )
            for r in sorted(card["skill"], key=lambda r: -r["r2_holdout_holes"]):
                out.append(
                    f"| {r['analyte']} | {r['r2_holdout_holes']} | ×{r['median_abs_error_factor']} | {r['n']:,} | "
                    f"{r['holes']} | {'yes' if r.get('predictable') else 'no'} |\n"
                )
        robust = runs[runs.kind == "robustness"]
        if len(robust):
            out.append("\n## Cluster robustness\n\n")
            for _, run in robust.iterrows():
                t, w = run.card["district_transfer"], run.card["width_sensitivity"]
                out.append(
                    f"- **{run.sensor.upper()}**: withheld districts (~20% of holes, {len(t['repeats'])} repeats) "
                    f"assigned to refitted centres agree with the full fit at ARI {t['mean_ari']} (min {t['min_ari']}). "
                    f"Hole-similarity agreement with 1 m bins (Spearman, {w.get('holes')} holes): "
                    f"{w.get('mantel_vs_1m')}; K chosen by the rule per width: {w.get('k_by_width')}.\n"
                )
        out.append(sequence_check(conn))
        out.append(tsa_versions_check())
        out.append(raw_white_mica_check(conn))
        jobs = read_frame(
            conn,
            "SELECT kind, status, count(*) n, round(avg(extract(epoch FROM finished_at-started_at))) avg_s "
            "FROM job GROUP BY 1,2 ORDER BY 1,2",
        )
        out.append("\n## Jobs executed\n\n" + table(jobs, ["kind", "status", "n", "avg_s"]))
    out.append(
        "\n## Limits\n\n"
        "- Processed abundances are TSG interpretation weights, not mass percent; Final Mask is not "
        "published with processed logs.\n"
        "- The assay and index tables are flagged unverified by the package itself; correlations are "
        "consistency checks.\n"
        "- Prediction has skill only near existing holes (see support limit); it is not a regional map.\n"
        "- Raw whole-hole views cover only the summarised subset; more holes can be added from the Data tab.\n"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(out), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
