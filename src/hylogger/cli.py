from __future__ import annotations

import argparse
import importlib.metadata
import platform
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__
from .catalog import load_catalog, raw_record, refresh_catalog
from .config import AppConfig
from .errors import HyLoggerError
from .inspect import inspect_hole, print_inspection, write_html_report
from .manifest import Manifest
from .prepare import combine_feature_matrix, feature_file, prepare_hole
from .processed import fetch_processed_hole, inventory_hole
from .raw import (
    check_raw_availability,
    download_raw_hole,
    import_raw_archive,
    parse_components,
    probe_raw,
)
from .selections import (
    add_holes,
    create_selection,
    list_selections,
    load_selection,
    remove_holes,
    resolve_hole_list,
    resolve_holes,
)
from .util import atomic_write_frame, atomic_write_json, human_bytes, print_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hylogger",
        description="Acquire, inspect, and prepare GSWA HyLogger data.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--data-dir", type=Path, help="Override HYLOGGER_DATA_DIR for this command."
    )
    parser.add_argument("--config", type=Path, help="Use a different project.toml file.")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser(
        "doctor", help="Check the local environment and optional services."
    )
    doctor.add_argument("--online", action="store_true", help="Also probe the public raw service.")
    doctor.set_defaults(handler=_doctor)

    initialise = commands.add_parser("init", help="Create the configured local data directories.")
    initialise.set_defaults(handler=_init)

    catalog = commands.add_parser(
        "catalog", help="Refresh and search the combined NVCL/raw catalog."
    )
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_refresh = catalog_commands.add_parser("refresh", help="Refresh both catalog providers.")
    catalog_refresh.set_defaults(handler=_catalog_refresh)
    catalog_search = catalog_commands.add_parser(
        "search", help="Search the local catalog snapshot."
    )
    catalog_search.add_argument("--contains", help="Case-insensitive hole ID substring.")
    catalog_search.add_argument("--operator", help="Case-insensitive operator substring.")
    catalog_search.add_argument("--project", help="Case-insensitive project substring.")
    catalog_search.add_argument("--raw", action="store_true", help="Require a raw ZIP package.")
    catalog_search.add_argument(
        "--processed", action="store_true", help="Require a hole present in the NVCL API."
    )
    catalog_search.add_argument("--limit", type=int, default=25)
    catalog_search.set_defaults(handler=_catalog_search)
    catalog_show = catalog_commands.add_parser("show", help="Show one catalog record.")
    catalog_show.add_argument("hole_id")
    catalog_show.set_defaults(handler=_catalog_show)

    selection = commands.add_parser("selection", help="Manage version-controlled hole cohorts.")
    selection_commands = selection.add_subparsers(dest="selection_command", required=True)
    selection_create = selection_commands.add_parser("create", help="Create a selection YAML file.")
    selection_create.add_argument("name")
    selection_create.add_argument("--holes", nargs="*", default=[])
    selection_create.add_argument("--description", default="")
    selection_create.set_defaults(handler=_selection_create)
    selection_add = selection_commands.add_parser("add", help="Add hole IDs to a selection.")
    selection_add.add_argument("name")
    selection_add.add_argument("holes", nargs="+")
    selection_add.set_defaults(handler=_selection_add)
    selection_remove = selection_commands.add_parser(
        "remove", help="Remove hole IDs from a selection."
    )
    selection_remove.add_argument("name")
    selection_remove.add_argument("holes", nargs="+")
    selection_remove.set_defaults(handler=_selection_remove)
    selection_show = selection_commands.add_parser("show", help="Show a selection.")
    selection_show.add_argument("name")
    selection_show.set_defaults(handler=_selection_show)
    selection_list = selection_commands.add_parser("list", help="List selections.")
    selection_list.set_defaults(handler=_selection_list)

    processed = commands.add_parser("processed", help="Preview or download processed TSG logs.")
    processed_commands = processed.add_subparsers(dest="processed_command", required=True)
    preview = processed_commands.add_parser(
        "preview", help="List datasets and scalar logs for a hole."
    )
    preview.add_argument("hole_id")
    preview.add_argument("--contains", help="Only show log names containing this text.")
    preview.set_defaults(handler=_processed_preview)
    processed_fetch = processed_commands.add_parser("fetch", help="Download processed scalar logs.")
    processed_fetch.add_argument("hole_id", nargs="?")
    processed_fetch.add_argument("--selection")
    processed_fetch.add_argument("--profile", default="clustering")
    processed_fetch.add_argument("--all-logs", action="store_true")
    processed_fetch.add_argument(
        "--log", dest="logs", action="append", help="Exact log name; repeat for multiple logs."
    )
    processed_fetch.set_defaults(handler=_processed_fetch)

    raw = commands.add_parser("raw", help="Probe, download, resume, or import raw ZIP packages.")
    raw_commands = raw.add_subparsers(dest="raw_command", required=True)
    raw_probe = raw_commands.add_parser("probe", help="Show raw URL, size, and resume support.")
    raw_probe.add_argument("hole_id")
    raw_probe.set_defaults(handler=_raw_probe)
    raw_check = raw_commands.add_parser(
        "check", help="Check raw package availability for several holes at once."
    )
    raw_check.add_argument("holes", nargs="*", help="Hole IDs to check.")
    raw_check.add_argument("--selection", help="Check every hole in a saved selection instead.")
    raw_check.add_argument(
        "--probe",
        action="store_true",
        help="Also request each URL, which confirms availability against a stale catalog.",
    )
    raw_check.add_argument("--csv", type=Path, help="Also write the result table to this CSV file.")
    raw_check.set_defaults(handler=_raw_check)
    raw_fetch = raw_commands.add_parser(
        "fetch", help="Download and optionally extract raw packages."
    )
    raw_fetch.add_argument("hole_id", nargs="?")
    raw_fetch.add_argument("--selection")
    raw_fetch.add_argument(
        "--components",
        help="Comma-separated: spectra, images, cras, lidar, docs, all, or none.",
    )
    raw_fetch.add_argument("--dry-run", action="store_true", help="Show size and target only.")
    raw_fetch.add_argument("--force", action="store_true", help="Redownload an existing archive.")
    raw_fetch.set_defaults(handler=_raw_fetch)
    raw_import = raw_commands.add_parser("import", help="Register an existing raw ZIP package.")
    raw_import.add_argument("source", type=Path)
    raw_import.add_argument("--hole-id")
    raw_import.add_argument("--mode", choices=("reference", "copy"), default="reference")
    raw_import.add_argument("--components", default="spectra")
    raw_import.add_argument(
        "--package-dir",
        type=Path,
        help="Register an existing extracted package instead of extracting another copy.",
    )
    raw_import.set_defaults(handler=_raw_import)

    inspect_parser = commands.add_parser("inspect", help="Summarise local processed and raw data.")
    inspect_parser.add_argument("hole_id")
    inspect_parser.add_argument("--report", action="store_true", help="Write a static HTML report.")
    inspect_parser.add_argument(
        "--open", action="store_true", help="Open the generated HTML report."
    )
    inspect_parser.set_defaults(handler=_inspect)

    prepare_parser = commands.add_parser(
        "prepare", help="Create clustering-ready samples and features."
    )
    prepare_parser.add_argument("hole_id", nargs="?")
    prepare_parser.add_argument("--selection")
    prepare_parser.add_argument(
        "--source",
        choices=("auto", "processed", "raw"),
        default="auto",
        help="Use only this source. auto prefers extracted raw data (legacy behaviour).",
    )
    prepare_parser.set_defaults(handler=_prepare)

    status = commands.add_parser("status", help="Show local acquisition and preparation state.")
    status.add_argument("--selection")
    status.add_argument("--hole-id")
    status.set_defaults(handler=_status)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = AppConfig.load(data_dir=args.data_dir, config_path=args.config)
        exit_code = int(args.handler(config, args) or 0)
    except HyLoggerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        exit_code = 2
    except KeyboardInterrupt:
        print("Interrupted. Partial raw downloads were kept for resumption.", file=sys.stderr)
        exit_code = 130
    raise SystemExit(exit_code)


def _doctor(config: AppConfig, args: argparse.Namespace) -> int:
    config.ensure_layout()
    Manifest(config.manifest_path)
    checks = [
        {"check": "Python", "status": "ok", "details": platform.python_version()},
        {"check": "Repository", "status": "ok", "details": str(config.repo_root)},
        {"check": "Data directory", "status": "ok", "details": str(config.data_dir)},
        {"check": "Configuration", "status": "ok", "details": str(config.config_path)},
    ]
    for distribution in ("nvcl-kit", "pytsg", "pandas", "pyarrow", "requests", "PyYAML"):
        try:
            version = importlib.metadata.version(distribution)
            checks.append({"check": distribution, "status": "ok", "details": version})
        except importlib.metadata.PackageNotFoundError:
            checks.append(
                {"check": distribution, "status": "missing", "details": "run pip install"}
            )
    if args.online:
        try:
            result = probe_raw(config, "05GJD001")
            checks.append(
                {
                    "check": "THREDDS raw",
                    "status": "ok",
                    "details": f"05GJD001 is {human_bytes(result['size_bytes'])}",
                }
            )
        except Exception as exc:
            checks.append({"check": "THREDDS raw", "status": "unavailable", "details": str(exc)})
    print_table(checks, [("check", "Check"), ("status", "Status"), ("details", "Details")])
    return 1 if any(row["status"] == "missing" for row in checks) else 0


def _init(config: AppConfig, _args: argparse.Namespace) -> int:
    config.ensure_layout()
    Manifest(config.manifest_path)
    print(f"Initialised local workspace: {config.data_dir}")
    print(f"Selections: {config.selections_dir}")
    print(f"Manifest: {config.manifest_path}")
    return 0


def _catalog_refresh(config: AppConfig, _args: argparse.Namespace) -> int:
    result = refresh_catalog(config)
    print(
        f"Catalog ready: {result['catalog_rows']:,} unique holes | "
        f"{result['nvcl_rows']:,} in NVCL | {result['raw_rows']:,} raw packages"
    )
    for warning in result["warnings"]:
        print(f"WARNING: {warning}")
    return 0


def _catalog_search(config: AppConfig, args: argparse.Namespace) -> int:
    frame = load_catalog(config)
    if args.contains:
        frame = frame[
            frame["hole_id"].astype(str).str.contains(args.contains, case=False, na=False)
        ]
    if args.operator:
        frame = frame[
            frame["operator"].astype(str).str.contains(args.operator, case=False, na=False)
        ]
    if args.project:
        frame = frame[frame["project"].astype(str).str.contains(args.project, case=False, na=False)]
    if args.raw:
        frame = frame[frame["raw_available"]]
    if args.processed:
        frame = frame[frame["nvcl_available"]]
    rows = []
    for record in frame.head(max(1, args.limit)).to_dict("records"):
        record["raw_size"] = human_bytes(record.get("raw_size_bytes"))
        rows.append(record)
    print_table(
        rows,
        [
            ("hole_id", "Hole ID"),
            ("operator", "Operator"),
            ("project", "Project"),
            ("length_m", "Length m"),
            ("nvcl_available", "Processed"),
            ("raw_available", "Raw"),
            ("raw_size", "Raw size"),
        ],
    )
    print(f"\nMatches: {len(frame):,}; showing {min(len(frame), max(1, args.limit)):,}")
    return 0


def _catalog_show(config: AppConfig, args: argparse.Namespace) -> int:
    record = raw_record(config, args.hole_id)
    if not record:
        raise HyLoggerError(f"Hole not found in local catalog: {args.hole_id}")
    for key, value in record.items():
        if key == "raw_size_bytes" and value is not None and not pd.isna(value):
            value = f"{int(value):,} ({human_bytes(value)})"
        print(f"{key}: {value}")
    return 0


def _selection_create(config: AppConfig, args: argparse.Namespace) -> int:
    payload = create_selection(config, args.name, args.holes, args.description, overwrite=False)
    print(f"Created selection {payload['name']!r} with {len(payload['holes'])} holes.")
    return 0


def _selection_add(config: AppConfig, args: argparse.Namespace) -> int:
    payload = add_holes(config, args.name, args.holes)
    print(f"Selection {args.name!r} now contains {len(payload['holes'])} holes.")
    return 0


def _selection_remove(config: AppConfig, args: argparse.Namespace) -> int:
    payload = remove_holes(config, args.name, args.holes)
    print(f"Selection {args.name!r} now contains {len(payload['holes'])} holes.")
    return 0


def _selection_show(config: AppConfig, args: argparse.Namespace) -> int:
    payload = load_selection(config, args.name)
    print(f"Selection: {payload['name']}")
    print(f"Description: {payload.get('description') or '-'}")
    print(f"Holes ({len(payload['holes'])}):")
    for hole_id in payload["holes"]:
        print(f"  {hole_id}")
    return 0


def _selection_list(config: AppConfig, _args: argparse.Namespace) -> int:
    print_table(
        list_selections(config),
        [("name", "Selection"), ("holes", "Holes"), ("description", "Description")],
    )
    return 0


def _processed_preview(config: AppConfig, args: argparse.Namespace) -> int:
    inventory = inventory_hole(config, args.hole_id)
    rows = []
    for dataset in inventory["datasets"]:
        for log in dataset["logs"]:
            if args.contains and args.contains.casefold() not in str(log["log_name"]).casefold():
                continue
            rows.append({"dataset_id": dataset["dataset_id"], **log})
    print(
        f"{inventory['hole_id']}: {inventory['dataset_count']} dataset(s), "
        f"{len(rows)} displayed logs"
    )
    print_table(
        rows,
        [
            ("dataset_id", "Dataset"),
            ("log_name", "Log name"),
            ("spectral_region", "Region"),
            ("processing_level", "Level"),
            ("log_type", "Type"),
        ],
    )
    return 0


def _processed_fetch(config: AppConfig, args: argparse.Namespace) -> int:
    holes = resolve_holes(config, args.hole_id, args.selection)
    failures = []
    for index, hole_id in enumerate(holes, 1):
        print(f"[{index}/{len(holes)}] Fetching processed logs for {hole_id}")
        try:
            outputs = fetch_processed_hole(
                config,
                hole_id,
                profile=args.profile,
                all_logs=args.all_logs,
                names=args.logs,
            )
            for output in outputs:
                print(
                    f"  dataset {output['dataset_id']}: {output['logs']} logs, "
                    f"{output['rows']:,} rows -> {output['path']}"
                )
        except Exception as exc:
            failures.append((hole_id, str(exc)))
            print(f"  FAILED: {exc}")
    if failures:
        raise HyLoggerError(
            f"Processed download failed for {len(failures)} of {len(holes)} holes. "
            "Completed holes were kept; run the same command to retry."
        )
    return 0


def _raw_probe(config: AppConfig, args: argparse.Namespace) -> int:
    result = probe_raw(config, args.hole_id)
    for key, value in result.items():
        if key.endswith("size_bytes"):
            value = f"{value:,} ({human_bytes(value)})" if value else "unknown"
        print(f"{key}: {value}")
    return 0


def _raw_check(config: AppConfig, args: argparse.Namespace) -> int:
    holes = resolve_hole_list(config, args.holes, args.selection)
    rows = check_raw_availability(config, holes, probe=args.probe)
    table = pd.DataFrame(rows)
    for row in rows:
        row["size"] = human_bytes(row["raw_size_bytes"]) if row["raw_size_bytes"] else "-"
        row["http"] = row["http_status"] or row["probe_error"] or "-"
    columns = [
        ("hole_id", "Hole ID"),
        ("processed_available", "Processed"),
        ("status", "Raw"),
        ("size", "Raw size"),
    ]
    if args.probe:
        columns.append(("http", "HTTP"))
    print_table(rows, columns)

    available = [row for row in rows if row["status"] == "available"]
    missing = [row["hole_id"] for row in rows if row["status"] == "missing"]
    unknown = [row["hole_id"] for row in rows if row["status"] == "unknown"]
    print(f"\nRaw packages available: {len(available)} of {len(rows)}")
    if missing:
        print(f"No raw package: {', '.join(missing)}")
    if unknown:
        print(f"Not in the catalog snapshot: {', '.join(unknown)}")
        print("Run 'hylogger catalog refresh', then repeat this command with --probe.")
    if available:
        width = max(len(row["hole_id"]) for row in available)
        print("\nDownload URLs:")
        for row in available:
            print(f"  {row['hole_id'].ljust(width)}  {row['raw_url'] or '-'}")
    if args.csv:
        atomic_write_frame(table, args.csv)
        print(f"Wrote {args.csv}")
    return 1 if missing or unknown else 0


def _raw_fetch(config: AppConfig, args: argparse.Namespace) -> int:
    holes = resolve_holes(config, args.hole_id, args.selection)
    default = ["spectra"]
    components = parse_components(args.components, default)
    failures = []
    for index, hole_id in enumerate(holes, 1):
        print(f"[{index}/{len(holes)}] Raw package {hole_id}")
        try:
            result = download_raw_hole(
                config,
                hole_id,
                components=components,
                dry_run=args.dry_run,
                force=args.force,
            )
            if args.dry_run:
                print(f"  URL: {result['url']}")
                print(
                    f"  Size: {human_bytes(result['size_bytes'] or result['catalog_size_bytes'])}"
                )
                print(f"  Target: {result['archive']}")
                print(f"  Components: {', '.join(result['components']) or 'none'}")
            else:
                print(f"  Archive: {result['archive']}")
                print(f"  Size: {human_bytes(result['size_bytes'])}")
                print(f"  SHA-256: {result['sha256']}")
                if result.get("verification"):
                    print(f"  pytsg verified: {result['verification'].get('pytsg_verified')}")
        except Exception as exc:
            failures.append((hole_id, str(exc)))
            print(f"  FAILED: {exc}")
    if failures:
        raise HyLoggerError(
            f"Raw operation failed for {len(failures)} of {len(holes)} holes. "
            "Partial .part files were kept for resumption."
        )
    return 0


def _raw_import(config: AppConfig, args: argparse.Namespace) -> int:
    result = import_raw_archive(
        config,
        args.source,
        hole_id=args.hole_id,
        mode=args.mode,
        components=parse_components(args.components),
        package_dir=args.package_dir,
    )
    print(f"Registered raw package for {result['hole_id']}: {result['archive']}")
    print(f"Size: {human_bytes(result['size_bytes'])}")
    print(f"SHA-256: {result['sha256']}")
    if result.get("verification"):
        print(f"pytsg verified: {result['verification'].get('pytsg_verified')}")
    return 0


def _inspect(config: AppConfig, args: argparse.Namespace) -> int:
    summary = inspect_hole(config, args.hole_id)
    print_inspection(summary)
    if args.report or args.open:
        path = write_html_report(config, summary, open_browser=args.open)
        print(f"\nHTML report: {path}")
    return 0


def _prepare(config: AppConfig, args: argparse.Namespace) -> int:
    holes = resolve_holes(config, args.hole_id, args.selection)
    if not holes:
        raise HyLoggerError("The selection is empty. Add holes before preparing it.")
    if args.selection:
        args.selection = args.selection.lower()
        for source in ("processed", "raw") if args.source == "auto" else (args.source,):
            path = feature_file(config, f"selection_{args.selection}", source=source)
            atomic_write_json(path.with_suffix(".status.json"), {"status": "preparing"})
    failures = []
    sources = set()
    for index, hole_id in enumerate(holes, 1):
        print(f"[{index}/{len(holes)}] Preparing {hole_id}")
        try:
            result = prepare_hole(config, hole_id, source=args.source)
            sources.add(result["source"])
            print(f"  source: {result['source']}")
            print(f"  features: {result['feature_path']}")
        except Exception as exc:
            failures.append((hole_id, str(exc)))
            print(f"  FAILED: {exc}")
    if failures:
        raise HyLoggerError(
            f"Preparation failed for {len(failures)} of {len(holes)} holes. "
            "Successful outputs were kept; no selection matrix was updated. "
            "Do not use an older matrix until this command succeeds."
        )
    if args.selection:
        if len(sources) != 1:
            raise HyLoggerError(
                "auto selected a mixture of raw and processed data. No selection matrix "
                "was updated. Re-run with --source processed or --source raw."
            )
        path = combine_feature_matrix(config, holes, args.selection, source=sources.pop())
        print(f"Selection feature matrix: {path}")
    return 0


def _status(config: AppConfig, args: argparse.Namespace) -> int:
    hole_filter = args.hole_id.upper() if args.hole_id else None
    if args.selection:
        holes = set(load_selection(config, args.selection)["holes"])
        records = [row for row in Manifest(config.manifest_path).find() if row["hole_id"] in holes]
    else:
        records = Manifest(config.manifest_path).find(hole_id=hole_filter)
    rows: list[dict[str, Any]] = []
    for record in records:
        rows.append(
            {
                "hole_id": record["hole_id"],
                "kind": record["kind"],
                "dataset_id": record.get("dataset_id"),
                "status": record["status"],
                "size": human_bytes(record.get("size_bytes")),
                "updated": record["updated_at"],
            }
        )
    print_table(
        rows,
        [
            ("hole_id", "Hole ID"),
            ("kind", "Kind"),
            ("dataset_id", "Dataset"),
            ("status", "Status"),
            ("size", "Size"),
            ("updated", "Updated"),
        ],
    )
    return 0


if __name__ == "__main__":
    main()
