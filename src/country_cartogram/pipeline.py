#!/usr/bin/env python3
"""Create a 2D region cartogram from CSV, with reusable geometry and audits."""

from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import warnings

import numpy as np
import shapely
from shapely.geometry import mapping
import matplotlib

from .rendering import render
from .geometry import prepare_region, polygon_parts
from .maps import DEFAULT_BOUNDARIES, load_map, add_map_arguments
from .values import read_values
from .physics import layout_physics

from .configuration import load_config


def audit(geometries, rows, config):
    areas = np.array([g.area for g in geometries])
    values = np.array([r["value"] for r in rows])
    target = values / math.fsum(values) * config["total_area"]
    area_error = float(np.max(np.abs(areas / target - 1)))
    minimum = math.inf
    group_minimum = math.inf
    closest_group_pair = None
    overlaps = []
    for i, a in enumerate(geometries):
        if not a.is_valid:
            raise RuntimeError(f"Invalid output geometry: {rows[i]['id']}")
        parts = polygon_parts(a)
        for k, part in enumerate(parts):
            for other in parts[:k]:
                if part.distance(other) < config["region_gap"] - 1e-5:
                    raise RuntimeError(
                        f"Landmasses overlap or have insufficient gap: {rows[i]['id']}"
                    )
        for j in range(i):
            distance = a.distance(geometries[j])
            minimum = min(minimum, distance)
            if rows[i]["group"] != rows[j]["group"]:
                if distance < group_minimum:
                    group_minimum = distance
                    closest_group_pair = [
                        rows[i]["id"],
                        rows[j]["id"],
                    ]
            if a.intersection(geometries[j]).area > 1e-9:
                overlaps.append([rows[i]["id"], rows[j]["id"]])
    if overlaps or area_error > 1e-8:
        raise RuntimeError(
            f"Geometry audit failed: area error {area_error}, overlaps {overlaps[:5]}"
        )
    gap_required = config["region_gap"]
    if minimum < gap_required - 1e-5:
        raise RuntimeError(f"Minimum gap {minimum} below requested {gap_required}")
    if group_minimum < config["group_gap"] - 1e-5:
        raise RuntimeError(
            f"Minimum group gap {group_minimum} ({closest_group_pair}) below requested {config['group_gap']}"
        )
    return {
        "max_relative_area_error": area_error,
        "minimum_region_distance": minimum if math.isfinite(minimum) else None,
        "minimum_group_distance": group_minimum
        if math.isfinite(group_minimum)
        else None,
        "overlap_pairs": overlaps,
        "all_geometries_valid": True,
    }


def run(
    csv_path,
    *,
    config_path=None,
    output=Path("output/cartogram"),
    boundaries=DEFAULT_BOUNDARIES,
    formats=("png", "svg", "pdf"),
    dpi=180,
    value_column="value",
    animate=False,
    volume_column=None,
    config_3d=None,
    preview_3d=False,
    html_js="inline",
    initial_layout=None,
    frame_sink=None,
    collision_cache=None,
    source_crs=None,
    projection=None,
    _source=None,
):
    """Solve a region layout, write exports, and return the numerical audit.

    Paths accept strings or Path objects. ``output`` is a filename stem;
    existing files at that stem are replaced. Use ``formats=()`` to save only
    the layout, metrics and audit. Keep these three files together for reuse.
    ``initial_layout`` continues compatible saved geometry; ``volume_column``
    adds an experimental 3D export. Internal frame/cache arguments support
    run_timeline(); ordinary callers can leave them at their defaults.
    """
    config = load_config(config_path, source_crs=source_crs, projection=projection)
    base_map = _source or load_map(
        boundaries, source_crs=config["source_crs"], projection=config["projection"]
    )
    catalog = base_map.catalog
    rows, coverage = read_values(csv_path, catalog, value_column)
    if volume_column:
        from .extrusion import export_3d, load_3d_config
        from .values import read_quantity_pairs

        # Validate both quantities before simulation.
        load_3d_config(config_3d)
        read_quantity_pairs(csv_path, value_column, volume_column)
        if html_js not in ("inline", "cdn"):
            raise ValueError("html_js must be inline or cdn")
    elif config_3d or preview_3d:
        raise ValueError("3D configuration / preview requires volume_column")
    if formats and len({r["group"] for r in rows}) > 12:
        raise ValueError(
            "Static figures support at most 12 groups; use a viewer-only timeline for more groups"
        )
    boundaries_hash = base_map.sha256
    initial_state = None
    if initial_layout:
        from .continuation import prepare_continuation

        geometries, anchors, metadata, initial_state = prepare_continuation(
            initial_layout, rows, config, boundaries_hash
        )
    else:
        geometries, anchors, metadata = [], [], []
        for row in rows:
            target = row["value"] / coverage["sum_of_values"] * config["total_area"]
            geom, anchor, detail = prepare_region(
                base_map.projection.geometry(catalog[row["id"]]["geometry"]),
                target,
                **{
                    k: config[k]
                    for k in [
                        "min_part_fraction",
                        "max_parts",
                        "simplify",
                        "shape_error",
                    ]
                },
            )
            geometries.append(geom)
            anchors.append(anchor)
            metadata.append(detail)
    print(
        f"Packing {len(rows)} regions in {len({r['group'] for r in rows})} groups...",
        flush=True,
    )
    placed, positions, packing, frames = layout_physics(
        geometries,
        np.array(anchors),
        rows,
        metadata,
        config,
        capture=animate,
        initial_state=initial_state,
        frame_sink=frame_sink,
        collision_cache=collision_cache,
    )
    if not packing["equilibrium_converged"]:
        warnings.warn(
            "Settling reached its iteration limit; see equilibrium diagnostics in the audit"
        )
    diagnostics = audit(placed, rows, config)
    diagnostics["shape_approximation"] = {
        "stage": "before simulation",
        "relative_error_budget": config["shape_error"],
        "source_outline_vertices": sum(
            m.get("source_outline_vertices", 0) for m in metadata
        ),
        "prepared_outline_vertices": sum(
            m.get("prepared_outline_vertices", 0) for m in metadata
        ),
        "max_relative_symmetric_difference": max(
            m.get("max_shape_relative_error", 0) for m in metadata
        ),
    }
    for geometry, position, detail in zip(placed, positions, metadata):
        for part, info in zip(polygon_parts(geometry), detail["parts"]):
            info["offset"] = (np.asarray(part.centroid.coords[0]) - position).tolist()
            info["area"] = part.area
    stem = Path(output)
    stem.parent.mkdir(parents=True, exist_ok=True)
    omitted_labels = (
        render(placed, rows, config, stem, formats, dpi, boundary_label=base_map.label)
        if formats
        else []
    )
    if frames:
        from .evolution import render_evolution

        render_evolution(
            frames,
            rows,
            config,
            stem,
            initial_label="Previous layout" if initial_state else "Map start",
        )
    diagnostics.update(
        {
            "coverage": coverage,
            "packing": packing,
            "omitted_labels": omitted_labels,
            "config": config,
            "coordinate_system": "arbitrary cartogram chart units; NOT longitude/latitude",
            "input_sha256": hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
            "boundaries_sha256": boundaries_hash,
            "map": {"label": base_map.label, **base_map.projection.settings},
            "versions": {
                "python": sys.version.split()[0],
                "numpy": np.__version__,
                "shapely": shapely.__version__,
                "matplotlib": matplotlib.__version__,
            },
        }
    )
    if initial_state:
        diagnostics["continuation"] = {
            "initial_layout_sha256": initial_state["layout_sha256"],
            "reused_region_count": len(rows),
        }
    stem.with_suffix(".audit.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    with stem.with_suffix(".metrics.csv").open("w", newline="") as f:
        fields = [
            "id",
            "name",
            "group",
            "parent",
            "value",
            "share",
            "value_status",
            "value_note",
            "rendered_area",
            "relative_area_error",
            "retained_source_area_fraction",
            "retained_part_count",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row, geom, detail in zip(rows, placed, metadata):
            share = row["value"] / coverage["sum_of_values"]
            writer.writerow(
                {
                    **{k: row[k] for k in fields[:5]},
                    "share": share,
                    "value_status": row["value_status"],
                    "value_note": row["value_note"],
                    "rendered_area": geom.area,
                    "relative_area_error": geom.area / (share * config["total_area"])
                    - 1,
                    **{k: detail[k] for k in fields[-2:]},
                }
            )
    layout = {
        "schema_version": 4,
        "coordinate_system": diagnostics["coordinate_system"],
        "regions": [
            {
                **row,
                "geometry": mapping(geom),
                "position": position.tolist(),
                "preprocessing": detail,
            }
            for row, geom, position, detail in zip(rows, placed, positions, metadata)
        ],
    }
    from .continuation import GEOMETRY_KEYS

    anchor_factor = 1 if initial_state else packing["initial_map_scale"]
    layout["simulation_state"] = {
        "map_scale": packing["initial_map_scale"],
        "map_anchors": {
            r["id"]: (np.asarray(a) * anchor_factor).tolist()
            for r, a in zip(rows, anchors)
        },
        "geometry_config": {key: config[key] for key in GEOMETRY_KEYS},
        "boundaries_sha256": boundaries_hash,
    }
    stem.with_suffix(".layout.json").write_text(
        json.dumps(layout, separators=(",", ":")) + "\n"
    )
    if volume_column:
        diagnostics["extrusion"] = export_3d(
            stem.with_suffix(".layout.json"),
            csv_path,
            volume_column=volume_column,
            area_column=value_column,
            output=stem,
            config_path=config_3d,
            html_js=html_js,
            preview=preview_3d,
        )
        stem.with_suffix(".audit.json").write_text(
            json.dumps(diagnostics, indent=2) + "\n"
        )
    print(
        f"Saved {stem}.* | max area error={diagnostics['max_relative_area_error']:.2e} | overlaps=0",
        flush=True,
    )
    return diagnostics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv", type=Path, help="CSV with id and non-negative quantities"
    )
    parser.add_argument(
        "--config", type=Path, help="JSON overrides for style and layout settings"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/cartogram"),
        help="Output filename stem, without extension",
    )
    add_map_arguments(parser)
    parser.add_argument(
        "--formats",
        nargs="+",
        choices=["png", "svg", "pdf"],
        default=["png", "svg", "pdf"],
    )
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument(
        "--value-column",
        default="value",
        help="Column controlling region area (default: value)",
    )
    parser.add_argument(
        "--animate",
        action="store_true",
        help="Also export a GIF and three-stage overview (growth mode)",
    )
    three = parser.add_argument_group(
        "Experimental 3D",
        "Height encodes second quantity / first quantity; narrow spikes can be hard to read.",
    )
    three.add_argument(
        "--volume-column",
        help="Second quantity: export 3D with height = volume-column / value-column",
    )
    three.add_argument(
        "--config-3d",
        type=Path,
        help="3D titles, variable labels, and common height_scale",
    )
    three.add_argument(
        "--preview-3d", action="store_true", help="Also export a static 3D PNG"
    )
    three.add_argument(
        "--html-js",
        choices=["inline", "cdn"],
        default="inline",
        help="3D HTML: offline (default) or smaller CDN export",
    )
    parser.add_argument(
        "--initial-layout",
        type=Path,
        help="Continue from a previous .layout.json instead of geographic initialization",
    )
    args = parser.parse_args()
    try:
        if args.dpi < 50:
            raise ValueError("dpi must be at least 50")
        run(
            args.csv,
            config_path=args.config,
            output=args.out,
            boundaries=args.boundaries,
            source_crs=args.source_crs,
            projection=args.projection,
            formats=args.formats,
            dpi=args.dpi,
            value_column=args.value_column,
            animate=args.animate,
            volume_column=args.volume_column,
            config_3d=args.config_3d,
            preview_3d=args.preview_3d,
            html_js=args.html_js,
            initial_layout=args.initial_layout,
        )
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
