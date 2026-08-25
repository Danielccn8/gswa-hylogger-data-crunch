from __future__ import annotations

import re
from pathlib import Path

import yaml

from .config import AppConfig
from .errors import SelectionError
from .util import utc_now

VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def selection_path(config: AppConfig, name: str) -> Path:
    normalised = name.lower().strip()
    if not VALID_NAME.fullmatch(normalised):
        raise SelectionError("Selection names may contain lowercase letters, numbers, '-' and '_'.")
    return config.selections_dir / f"{normalised}.yaml"


def create_selection(
    config: AppConfig,
    name: str,
    holes: list[str] | None = None,
    description: str = "",
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    path = selection_path(config, name)
    if path.exists() and not overwrite:
        raise SelectionError(f"Selection already exists: {name}")
    now = utc_now()
    payload: dict[str, object] = {
        "name": name.lower(),
        "description": description,
        "created_at": now,
        "updated_at": now,
        "holes": _normalise_holes(holes or []),
    }
    _write(path, payload)
    return payload


def load_selection(config: AppConfig, name: str) -> dict[str, object]:
    path = selection_path(config, name)
    if not path.exists():
        raise SelectionError(f"Selection not found: {name}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    holes = payload.get("holes", [])
    if not isinstance(holes, list):
        raise SelectionError(f"Selection {name!r} has an invalid 'holes' field.")
    payload["holes"] = _normalise_holes(holes)
    payload.setdefault("name", name.lower())
    payload.setdefault("description", "")
    return payload


def add_holes(config: AppConfig, name: str, holes: list[str]) -> dict[str, object]:
    payload = load_selection(config, name)
    payload["holes"] = _normalise_holes([*payload["holes"], *holes])
    payload["updated_at"] = utc_now()
    _write(selection_path(config, name), payload)
    return payload


def remove_holes(config: AppConfig, name: str, holes: list[str]) -> dict[str, object]:
    payload = load_selection(config, name)
    remove = set(_normalise_holes(holes))
    payload["holes"] = [hole for hole in payload["holes"] if hole not in remove]
    payload["updated_at"] = utc_now()
    _write(selection_path(config, name), payload)
    return payload


def list_selections(config: AppConfig) -> list[dict[str, object]]:
    output = []
    for path in sorted(config.selections_dir.glob("*.yaml")):
        try:
            payload = load_selection(config, path.stem)
        except SelectionError:
            continue
        output.append(
            {
                "name": payload["name"],
                "holes": len(payload["holes"]),
                "description": payload.get("description", ""),
            }
        )
    return output


def resolve_holes(config: AppConfig, hole: str | None, selection: str | None) -> list[str]:
    if bool(hole) == bool(selection):
        raise SelectionError("Provide exactly one HOLE_ID or --selection NAME.")
    return [hole.upper()] if hole else list(load_selection(config, selection or "")["holes"])


def _normalise_holes(holes: list[object]) -> list[str]:
    return sorted({str(hole).strip().upper() for hole in holes if str(hole).strip()})


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    temporary.replace(path)
