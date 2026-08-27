from __future__ import annotations

import html
import json
import webbrowser
from pathlib import Path
from typing import Any

import pandas as pd

from .config import AppConfig
from .manifest import Manifest
from .raw import find_package_dir, find_raw_archive
from .util import atomic_write_text, human_bytes, utc_now


def inspect_hole(config: AppConfig, hole_id: str) -> dict[str, Any]:
    hole_id = hole_id.upper()
    processed_paths = sorted(
        (config.data_dir / "downloads" / "processed" / hole_id).glob("*/scalar_logs.parquet")
    )
    processed_summary = [_summarise_frame(pd.read_parquet(path), path) for path in processed_paths]

    archive = find_raw_archive(config, hole_id)
    package_dir = find_package_dir(config, hole_id)
    raw_summary: dict[str, Any] | None = None
    if archive or package_dir:
        raw_summary = {
            "archive": str(archive) if archive else None,
            "archive_size_bytes": archive.stat().st_size if archive else None,
            "package_dir": str(package_dir) if package_dir else None,
            "sections": {},
        }
    if package_dir:
        raw_summary["sections"] = _summarise_package(package_dir)

    prepared_qc_path = config.data_dir / "prepared" / "samples" / f"{hole_id}_qc.json"
    prepared_qc = None
    if prepared_qc_path.exists():
        prepared_qc = json.loads(prepared_qc_path.read_text(encoding="utf-8"))
    prepared_sources = {}
    for source in ("processed", "raw"):
        path = config.data_dir / "prepared" / "samples" / f"{hole_id}_{source}_qc.json"
        if path.exists():
            prepared_sources[source] = json.loads(path.read_text(encoding="utf-8"))
    if prepared_sources:
        prepared_qc = next(iter(prepared_sources.values()))

    manifest = Manifest(config.manifest_path).find(hole_id=hole_id)
    next_commands = []
    if not processed_paths:
        next_commands.append(f"hylogger processed preview {hole_id}")
        next_commands.append(f"hylogger processed fetch {hole_id}")
    if not archive:
        next_commands.append(f"hylogger raw fetch {hole_id} --components spectra --dry-run")
    if processed_paths or package_dir:
        if not prepared_qc:
            next_commands.append(f"hylogger prepare {hole_id}")

    return {
        "hole_id": hole_id,
        "inspected_at": utc_now(),
        "processed": processed_summary,
        "raw": raw_summary,
        "prepared": prepared_qc,
        "prepared_sources": prepared_sources,
        "manifest": manifest,
        "next_commands": next_commands,
    }


def print_inspection(summary: dict[str, Any]) -> None:
    print(f"HyLogger inspection: {summary['hole_id']}")
    print("=" * (21 + len(summary["hole_id"])))
    if summary["processed"]:
        print(f"Processed datasets: {len(summary['processed'])}")
        for dataset in summary["processed"]:
            depth = ""
            if dataset.get("depth_min_m") is not None:
                depth = f" | depth {dataset['depth_min_m']:.2f}-{dataset['depth_max_m']:.2f} m"
            print(
                f"  {dataset['path']} | {dataset['rows']:,} rows x "
                f"{dataset['columns']} columns{depth}"
            )
            for item in dataset.get("top_categories", [])[:6]:
                print(f"    {item['column']}: {item['value']} ({item['pct']:.1f}%)")
    else:
        print("Processed datasets: not downloaded")

    raw = summary.get("raw")
    if raw:
        print(f"Raw archive: {raw.get('archive') or 'not registered'}")
        if raw.get("archive_size_bytes"):
            print(f"Raw size: {human_bytes(raw['archive_size_bytes'])}")
        if raw.get("sections"):
            for name, section in raw["sections"].items():
                print(
                    f"  {name.upper()}: {section['rows']:,} readings x "
                    f"{section['bands']:,} bands; {section['scalar_columns']} scalar columns"
                )
    else:
        print("Raw package: not downloaded")

    if summary.get("prepared"):
        sources = ", ".join(summary.get("prepared_sources", {})) or "legacy output"
        print(f"Prepared for analysis: yes ({sources})")
    else:
        print("Prepared for analysis: no")
    if summary["next_commands"]:
        print("\nSuggested next commands:")
        for command in summary["next_commands"]:
            print(f"  {command}")


def write_html_report(
    config: AppConfig, summary: dict[str, Any], *, open_browser: bool = False
) -> Path:
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = config.reports_dir / f"{summary['hole_id']}.html"
    processed_cards = "".join(_processed_card(item) for item in summary["processed"])
    if not processed_cards:
        processed_cards = "<p class='empty'>No processed logs have been downloaded.</p>"
    raw = summary.get("raw")
    raw_content = _raw_card(raw) if raw else "<p class='empty'>No raw package is registered.</p>"
    commands = "".join(
        f"<code>{html.escape(command)}</code>" for command in summary["next_commands"]
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HyLogger inspection - {html.escape(summary["hole_id"])}</title>
<style>
:root {{ color-scheme: light; --ink:#17202a; --muted:#5d6d7e; --line:#d5d8dc; --blue:#176b87; --bg:#f7f9fa; }}
body {{ margin:0; font-family:Inter,Segoe UI,Arial,sans-serif; color:var(--ink); background:var(--bg); }}
main {{ max-width:1100px; margin:0 auto; padding:40px 24px 80px; }}
h1 {{ margin:0 0 6px; font-size:34px; }} h2 {{ margin-top:36px; }}
.subtitle,.empty {{ color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }}
.card {{ background:white; border:1px solid var(--line); border-radius:12px; padding:18px; box-shadow:0 2px 8px #17202a0d; overflow:auto; }}
.metric {{ font-size:26px; font-weight:700; color:var(--blue); }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }} th,td {{ text-align:left; border-bottom:1px solid var(--line); padding:8px 6px; }}
code {{ display:block; background:#17202a; color:#ecf0f1; border-radius:6px; padding:10px 12px; margin:8px 0; overflow:auto; }}
.bar {{ height:8px; background:#d6eaf8; border-radius:4px; }} .bar span {{ display:block; height:100%; background:var(--blue); border-radius:4px; }}
</style>
</head>
<body><main>
<h1>{html.escape(summary["hole_id"])}</h1>
<p class="subtitle">Local HyLogger inspection generated {html.escape(summary["inspected_at"])}</p>
<h2>Processed TSG logs</h2><div class="grid">{processed_cards}</div>
<h2>Raw package</h2><div class="grid">{raw_content}</div>
<h2>Suggested next commands</h2>{commands or '<p class="empty">This hole is ready for analysis.</p>'}
</main></body></html>"""
    atomic_write_text(path, document)
    if open_browser:
        webbrowser.open(path.as_uri())
    return path


def _summarise_frame(frame: pd.DataFrame, path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "rows": len(frame),
        "columns": len(frame.columns),
        "top_categories": [],
    }
    depth_column = next(
        (column for column in ("depth_m", "start_depth", "startdepth", "depth") if column in frame),
        None,
    )
    if depth_column:
        depth = pd.to_numeric(frame[depth_column], errors="coerce")
        result.update(
            {
                "depth_column": depth_column,
                "depth_min_m": float(depth.min()) if depth.notna().any() else None,
                "depth_max_m": float(depth.max()) if depth.notna().any() else None,
                "duplicate_depths": int(depth.duplicated().sum()),
                "backward_steps": int((depth.diff() < 0).sum()),
            }
        )
    category_columns = [
        column
        for column in frame.columns
        if column.startswith(("min1", "mineral_1", "grp1", "group_1"))
    ]
    top = []
    for column in category_columns[:6]:
        counts = frame[column].dropna().astype(str).value_counts()
        if not counts.empty:
            top.append(
                {
                    "column": column,
                    "value": counts.index[0],
                    "count": int(counts.iloc[0]),
                    "pct": float(counts.iloc[0] / max(1, frame[column].notna().sum()) * 100),
                }
            )
    result["top_categories"] = top
    return result


def _summarise_package(package_dir: Path) -> dict[str, Any]:
    from pytsg import parse_tsg

    package = parse_tsg.read_package(package_dir, read_cras_file=False)
    sections: dict[str, Any] = {}
    for name in ("nir", "tir", "mir"):
        section = getattr(package, name, None)
        if section is None or not hasattr(section, "spectra"):
            continue
        sections[name] = {
            "rows": int(section.spectra.shape[0]),
            "bands": int(section.spectra.shape[1]),
            "scalar_columns": int(section.scalars.shape[1]),
            "wavelength_min": float(section.wavelength.min()),
            "wavelength_max": float(section.wavelength.max()),
        }
    return sections


def _processed_card(item: dict[str, Any]) -> str:
    categories = "".join(
        f"<tr><td>{html.escape(str(row['column']))}</td><td>{html.escape(str(row['value']))}</td>"
        f"<td>{row['pct']:.1f}%<div class='bar'><span style='width:{min(100, row['pct']):.1f}%'></span></div></td></tr>"
        for row in item.get("top_categories", [])
    )
    depth = "unknown"
    if item.get("depth_min_m") is not None:
        depth = f"{item['depth_min_m']:.2f} to {item['depth_max_m']:.2f} m"
    return f"""<section class="card"><div class="metric">{item["rows"]:,}</div><p>rows, {item["columns"]} columns</p>
<p><strong>Depth:</strong> {depth}</p>
<table><thead><tr><th>Log</th><th>Top value</th><th>Share</th></tr></thead><tbody>{categories}</tbody></table></section>"""


def _raw_card(raw: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{name.upper()}</td><td>{value['rows']:,}</td><td>{value['bands']}</td>"
        f"<td>{value['scalar_columns']}</td></tr>"
        for name, value in raw.get("sections", {}).items()
    )
    size = human_bytes(raw.get("archive_size_bytes"))
    return f"""<section class="card"><div class="metric">{html.escape(size)}</div><p>raw archive</p>
<table><thead><tr><th>Region</th><th>Readings</th><th>Bands</th><th>Scalars</th></tr></thead><tbody>{rows}</tbody></table></section>"""
