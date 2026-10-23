"""Prepare a previous cartogram as the starting state of another simulation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from shapely.affinity import scale, translate
from shapely.geometry import MultiPolygon, shape

from .geometry import polygon_parts

GEOMETRY_KEYS = (
    "min_part_fraction",
    "max_parts",
    "simplify",
    "shape_error",
    "source_crs",
    "projection",
)


def prepare_continuation(path, rows, config, boundaries_sha256):
    """Reuse positions, exact retained outlines, and the original anchor frame."""
    path = Path(path)
    previous = json.loads(path.read_text(encoding="utf-8"))
    old_rows = previous.get("regions", [])
    old = {r["id"]: r for r in old_rows}
    codes = {r["id"] for r in rows}
    if not old or len(old) != len(old_rows):
        raise ValueError("Previous layout must contain unique region records")
    if codes != set(old):
        raise ValueError(
            "Continuation requires the same positive region coverage; "
            f"added={sorted(codes - set(old))}, removed={sorted(set(old) - codes)}. "
            "Use a consistent region cohort or start a new layout."
        )
    state = previous["simulation_state"]
    if state["boundaries_sha256"] != boundaries_sha256:
        raise ValueError(
            "Boundary dataset changed; start a new layout instead of continuing"
        )
    changed = [
        key for key in GEOMETRY_KEYS if config[key] != state["geometry_config"][key]
    ]
    if changed:
        raise ValueError(f"Outline settings changed ({changed}); start a new layout")
    map_scale = state["map_scale"]
    if (
        not isinstance(map_scale, (int, float))
        or not math.isfinite(map_scale)
        or map_scale <= 0
    ):
        raise ValueError("Previous layout has an invalid geographic scale")
    geometries, anchors, metadata, positions, initial_areas, part_positions = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    total = math.fsum(row["value"] for row in rows)
    for row in rows:
        code = row["id"]
        prior = old[code]
        if (row["group"], row.get("parent", "")) != (
            prior["group"],
            prior.get("parent", ""),
        ):
            raise ValueError(f"{code}: group or parent changed; start a new layout")
        geometry = shape(prior["geometry"])
        position = np.asarray(prior["position"], dtype=float)
        anchor = np.asarray(state["map_anchors"][code], dtype=float)
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
        parts = polygon_parts(geometry)
        centers = [np.array(part.centroid.coords[0]) for part in parts]
        final = MultiPolygon(
            [
                translate(
                    scale(translate(part, *-center), factor, factor, origin=(0, 0)),
                    *(center - position),
                )
                for part, center in zip(parts, centers)
            ]
        )
        part_positions.append(np.asarray(centers))
        detail["scale_factor"] *= factor
        for part, center in zip(detail["parts"], centers):
            part["offset"] = (center - position).tolist()
            part["area"] *= factor * factor
        geometries.append(final)
        metadata.append(detail)
        positions.append(position)
        initial_areas.append(geometry.area)
        anchors.append(anchor)
    initial = {
        "positions": np.asarray(positions),
        "part_positions": np.concatenate(part_positions),
        "areas": np.asarray(initial_areas),
        "map_scale": map_scale,
        "anchors": np.asarray(anchors),
        "layout_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return geometries, np.asarray(anchors), metadata, initial
