"""Plain-language reasons why two holes are close under a whole-hole view.

The distances themselves are computed elsewhere (Jensen-Shannon on cluster shares,
DTW on sequences, Wasserstein on raw spectra). These helpers turn the stored
fingerprints into the few facts a geologist would check first: what the two holes
share, where they differ most, and whether their zones come in the same order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def overlap(
    a: Mapping[str, float],
    b: Mapping[str, float],
    *,
    min_share: float = 0.02,
    top: int = 3,
    min_difference: float = 0.05,
) -> dict:
    """Shared share (sum of minima), the largest shared parts and the largest differences."""
    keys = set(a) | set(b)
    shared = {k: min(float(a.get(k, 0.0)), float(b.get(k, 0.0))) for k in keys}
    common = sorted(
        ((k, v) for k, v in shared.items() if v >= min_share), key=lambda kv: (-kv[1], kv[0])
    )[:top]
    differences = sorted(
        (
            (k, float(a.get(k, 0.0)), float(b.get(k, 0.0)))
            for k in keys
            if abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) >= min_difference
        ),
        key=lambda t: (-abs(t[1] - t[2]), t[0]),
    )[:2]
    return {
        "overlap": round(sum(shared.values()), 3),
        "shared": [[k, round(v, 3)] for k, v in common],
        "differences": [[k, round(x, 3), round(y, 3)] for k, x, y in differences],
    }


def sequence_summary(
    zones: Sequence[Mapping], *, min_length_m: float = 10.0, min_same: float = 0.5
) -> dict:
    """Order of matched zones down hole A and the share of matched metres in the same cluster."""
    order: list[str] = []
    metres = same = 0.0
    for zone in zones:
        length = float(zone["a_to"]) - float(zone["a_from"])
        share = float(zone.get("same_cluster_share") or 0.0)
        metres += length
        same += length * share
        if length < min_length_m or share < min_same:
            continue
        name = zone.get("cluster_name") or f"cluster {zone['cluster_id']}"
        if not order or order[-1] != name:
            order.append(name)
    return {"order": order, "same_share": round(same / metres, 3) if metres else None}


def mix_lines(sensor: str, clusters: dict, minerals: dict | None) -> list[str]:
    """Sentences for one sensor from `overlap` results on cluster and mineral shares."""
    lines = []
    if clusters["shared"]:
        parts = ", ".join(f"{name} {share:.0%}" for name, share in clusters["shared"][:2])
        share = clusters["overlap"]
        lines.append(f"{sensor}: {share:.0%} of metres in the same clusters ({parts})")
    if minerals and minerals["differences"]:
        parts = ", ".join(f"{name} {x:.0%} vs {y:.0%}" for name, x, y in minerals["differences"])
        lines.append(f"{sensor} differs in {parts}")
    return lines


def sequence_line(summary: dict) -> str | None:
    if not summary["order"]:
        return None
    share = summary["same_share"]
    tail = f" ({share:.0%} of matched metres in the same cluster)" if share is not None else ""
    return "Same order down the hole: " + " → ".join(summary["order"][:5]) + tail


def raw_lines(sensor: str, classes: dict | None, bands_a: dict, bands_b: dict) -> list[str]:
    lines = []
    if classes and classes["shared"]:
        lines.append(
            f"Raw {sensor}: {classes['overlap']:.0%} of metres in the same spectral classes"
        )
    position_a, position_b = bands_a.get("aloh_2200_pos"), bands_b.get("aloh_2200_pos")
    if position_a and position_b:
        alike = "similar" if abs(position_a - position_b) < 4 else "different"
        lines.append(
            f"Al-OH absorption {position_a:.0f} vs {position_b:.0f} nm "
            f"({alike} white-mica chemistry)"
        )
    return lines


def context_line(km: float | None, same_district: bool) -> str | None:
    if km is None:
        return None
    apart = "less than 1 km apart" if km < 1 else f"{km:,.0f} km apart"
    return apart + (" · same district" if same_district else "")
