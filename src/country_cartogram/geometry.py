"""Planar shape approximation and part placement. Distances use chart units."""

from __future__ import annotations
import math
import numpy as np
import shapely
from shapely.geometry import MultiPolygon, shape
from shapely.affinity import translate, scale


def polygon_parts(geom):
    if geom.geom_type == "Polygon":
        return [geom] if geom.area > 0 else []
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in polygon_parts(g)]
    return []


def approximate_part(part, error_budget, tolerance):
    """Largest tested simplification within a symmetric-difference area budget.

    Restore area and centroid before measuring error. This is done once, before
    island packing, hull construction, growth states, and all period updates.
    """
    if tolerance == 0:
        return part, 0.0
    if error_budget == 0:
        result = part.simplify(tolerance, preserve_topology=True)
        return result, result.symmetric_difference(part).area / part.area
    x0, y0, x1, y1 = part.bounds
    low, high = 0.0, max(tolerance, max(x1 - x0, y1 - y0) * 0.15)
    best, best_error = part, 0.0
    center = part.centroid
    for _ in range(12):
        trial = (low + high) / 2
        candidate = part.simplify(trial, preserve_topology=True)
        candidate = scale(
            candidate,
            math.sqrt(part.area / candidate.area),
            math.sqrt(part.area / candidate.area),
            origin=center,
        )
        candidate = translate(
            candidate, center.x - candidate.centroid.x, center.y - candidate.centroid.y
        )
        error = candidate.symmetric_difference(part).area / part.area
        if candidate.is_valid and error <= error_budget:
            best, best_error, low = candidate, error, trial
        else:
            high = trial
    return best, best_error


def prepare_region(
    geojson,
    target_area,
    *,
    min_part_fraction=0.001,
    max_parts=24,
    simplify=0.0015,
    shape_error=0.02,
):
    """Simplify and uniformly scale landmasses; keep their source offsets.

    Landmasses are positioned independently by the simulation. Dropped parts
    are recorded, and retained area is normalized to the region quantity.
    """
    source = polygon_parts(shape(geojson))
    if not source:
        raise ValueError("Region has no polygon geometry")
    parts = source
    parts.sort(key=lambda p: -p.area)
    original_area = sum(p.area for p in parts)
    main_anchor = np.array(parts[0].centroid.coords[0])
    retained = [p for p in parts if p.area / original_area >= min_part_fraction][
        :max_parts
    ]
    if not retained:
        retained = parts[:1]
    retained_area = sum(p.area for p in retained)
    tol = math.sqrt(original_area) * simplify
    source_vertices = sum(len(shapely.get_coordinates(p)) for p in retained)
    approximations = [approximate_part(p, shape_error, tol) for p in retained]
    retained = [p for p, error in approximations]
    factor = math.sqrt(target_area / sum(p.area for p in retained))
    main = retained[0]
    main_center = np.array(main.centroid.coords[0])
    local_parts = []
    part_info = []
    for part in retained:
        center = np.array(part.centroid.coords[0])
        local = scale(translate(part, *-center), factor, factor, origin=(0, 0))
        offset = (center - main_center) * factor
        placed = translate(local, *offset)
        local_parts.append(placed)
        part_info.append(
            {
                "source_center": center.tolist(),
                "offset": offset.tolist(),
                "area": placed.area,
            }
        )
    result = MultiPolygon(local_parts)
    return (
        result,
        main_anchor,
        {
            "source_part_count": len(parts),
            "retained_part_count": len(retained),
            "retained_source_area_fraction": retained_area / original_area,
            "scale_factor": factor,
            "parts": part_info,
            "source_outline_vertices": source_vertices,
            "prepared_outline_vertices": sum(
                len(shapely.get_coordinates(p)) for p in retained
            ),
            "shape_error_budget": shape_error,
            "max_shape_relative_error": max(error for p, error in approximations),
        },
    )
