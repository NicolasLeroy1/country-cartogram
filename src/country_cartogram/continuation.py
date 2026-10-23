"""Prepare a previous cartogram as the starting state of another simulation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from shapely.affinity import scale, translate
from shapely.geometry import shape

from .geometry import polygon_parts

GEOMETRY_KEYS = (
    "min_part_fraction",
    "max_parts",
    "simplify",
    "satellite_distance",
    "shape_error",
)


def prepare_continuation(path, rows, config, boundaries_sha256):
    """Reuse positions, exact retained outlines, and the original anchor frame."""
    path = Path(path)
    previous = json.loads(path.read_text(encoding="utf-8"))
    old_rows = previous.get("countries", [])
    old = {r["country_code"]: r for r in old_rows}
    codes = {r["country_code"] for r in rows}
    if not old or len(old) != len(old_rows):
        raise ValueError("Previous layout must contain unique country records")
    if codes != set(old):
        raise ValueError(
            "Continuation requires the same positive country coverage; "
            f"added={sorted(codes - set(old))}, removed={sorted(set(old) - codes)}. "
            "Use a consistent country cohort or start a new layout."
        )
    state = previous.get("simulation_state")
    if state is None:
        # Backward compatibility with v0.1/v0.2 layouts and their companion audit.
        audit_path = path.with_name(
            path.name.removesuffix(".layout.json") + ".audit.json"
        )
        if not audit_path.exists():
            raise ValueError(
                "Older layouts require their companion .audit.json for continuation"
            )
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit["packing"].get("method") != "geographic_growth_rigid_body_physics":
            raise ValueError(
                "Continuation requires a growth-mode layout, not a legacy packing"
            )
        geographic_scale = audit["packing"]["initial_geographic_scale"]
        state = {
            "geographic_scale": geographic_scale,
            "geographic_anchors": {
                code: (
                    np.asarray(row["preprocessing"]["parts"][0]["source_center"])
                    * geographic_scale
                ).tolist()
                for code, row in old.items()
            },
            "geometry_config": {
                k: audit["config"].get(k, 0 if k == "shape_error" else None)
                for k in GEOMETRY_KEYS
            },
            "boundaries_sha256": audit["boundaries_sha256"],
        }
    if state["boundaries_sha256"] != boundaries_sha256:
        raise ValueError(
            "Boundary dataset changed; start a new layout instead of continuing"
        )
    changed = [
        key
        for key in GEOMETRY_KEYS
        if config[key]
        != state["geometry_config"].get(key, 0 if key == "shape_error" else None)
    ]
    if changed:
        raise ValueError(f"Outline settings changed ({changed}); start a new layout")
    geographic_scale = state["geographic_scale"]
    if (
        not isinstance(geographic_scale, (int, float))
        or not math.isfinite(geographic_scale)
        or geographic_scale <= 0
    ):
        raise ValueError("Previous layout has an invalid geographic scale")
    geometries, anchors, metadata, positions, initial_areas = [], [], [], [], []
    total = math.fsum(row["value"] for row in rows)
    for row in rows:
        code = row["country_code"]
        prior = old[code]
        if (row["cluster"], row.get("parent", "")) != (
            prior["cluster"],
            prior.get("parent", ""),
        ):
            raise ValueError(f"{code}: cluster or parent changed; start a new layout")
        geometry = shape(prior["geometry"])
        position = np.asarray(prior["position"], dtype=float)
        anchor = np.asarray(state["geographic_anchors"][code], dtype=float)
        if (
            geometry.geom_type not in ("Polygon", "MultiPolygon")
            or not geometry.is_valid
            or geometry.is_empty
            or geometry.has_z
            or not math.isfinite(geometry.area)
            or geometry.area <= 0
            or position.shape != (2,)
            or anchor.shape != (2,)
            or not np.all(np.isfinite(position))
            or not np.all(np.isfinite(anchor))
        ):
            raise ValueError(
                f"{code}: previous geometry, position, or anchor is invalid"
            )
        detail = copy.deepcopy(prior["preprocessing"])
        if len(detail["parts"]) != len(polygon_parts(geometry)):
            raise ValueError(
                f"{code}: previous component metadata do not match its polygons"
            )
        target = row["value"] / total * config["total_area"]
        factor = math.sqrt(target / geometry.area)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError(f"{code}: invalid continuation scale")
        final = scale(translate(geometry, *-position), factor, factor, origin=(0, 0))
        detail["scale_factor"] *= factor
        for part in detail["parts"]:
            part["offset"] = (np.asarray(part["offset"]) * factor).tolist()
            part["area"] *= factor * factor
        geometries.append(final)
        metadata.append(detail)
        positions.append(position)
        initial_areas.append(geometry.area)
        anchors.append(anchor)
    initial = {
        "positions": np.asarray(positions),
        "areas": np.asarray(initial_areas),
        "geographic_scale": geographic_scale,
        "anchors": np.asarray(anchors),
        "layout_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return geometries, np.asarray(anchors), metadata, initial
