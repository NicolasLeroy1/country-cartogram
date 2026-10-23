"""Continue 2D region cartograms through an ordered series of observations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from bisect import bisect_left
from pathlib import Path

import numpy as np
from shapely.geometry import shape

from .configuration import region_color, load_config
from .pipeline import run
from .maps import DEFAULT_BOUNDARIES, load_map, add_map_arguments
from .values import read_table, read_values, read_quantity_pairs
from .rendering import add_geometry
from .geometry import polygon_parts


def fill_missing_periods(periods, value_column="value", method="interpolate"):
    """Keep a fixed cohort, estimating gaps from each region's known values.

    Interpolate interior gaps, or hold the previous value in carry mode. Outside
    the observed range hold the nearest value. Supplied positive values and
    their status are preserved. This assumes consistent territorial units;
    mergers/splits must be reconciled by the caller before using this function.
    """
    if method not in {"interpolate", "carry"}:
        raise ValueError("Missing-value method must be interpolate or carry")
    known, templates = {}, {}
    for year, rows in sorted(periods.items()):
        for row in rows:
            code = row["id"].strip().upper()
            templates.setdefault(code, row)
            raw = row.get(value_column)
            if raw is None or str(raw).strip() == "":
                continue
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"{code}, {year}: known quantities must be finite and positive"
                )
            if year in known.setdefault(code, {}):
                raise ValueError(f"Duplicate observation: {code}, {year}")
            known[code][year] = row
    unknown = sorted(set(templates) - set(known))
    if unknown:
        raise ValueError(
            f"Cannot estimate regions with no known positive value: {unknown}"
        )
    completed = {year: [] for year in sorted(periods)}
    for code in sorted(known):
        years = sorted(known[code])
        for year in completed:
            if year in known[code]:
                row = {
                    "value_status": "observed",
                    "value_note": "",
                    **known[code][year],
                }
            else:
                right = bisect_left(years, year)
                before = years[max(0, right - 1)]
                after = years[min(right, len(years) - 1)]
                left_value = float(known[code][before][value_column])
                right_value = float(known[code][after][value_column])
                if method == "interpolate" and before < year < after:
                    value = left_value + (right_value - left_value) * (
                        year - before
                    ) / (after - before)
                    status, note = (
                        "interpolated",
                        f"Linear estimate between {before} and {after}",
                    )
                else:
                    origin = after if year < years[0] else before
                    value = float(known[code][origin][value_column])
                    status = "carried_backward" if year < origin else "carried_forward"
                    note = f"{origin} value held {'backward' if year < origin else 'forward'}"
                template_year = after if year < years[0] else before
                row = {
                    **known[code][template_year],
                    value_column: format(value, ".15g"),
                    "value_status": status,
                    "value_note": note,
                }
            row["id"] = code
            completed[year].append(row)
    return completed


def read_timeline(
    path,
    time_column="year",
    value_column="value",
    missing_values="error",
):
    """Read integer periods, optionally filling missing inputs before any solve."""
    periods, seen = {}, set()
    if missing_values not in {"error", "interpolate", "carry"}:
        raise ValueError("missing_values must be error, interpolate, or carry")
    fields, sources = read_table(path, {"id", time_column, value_column})
    fields = [key for key in fields if key != time_column]
    for line, source in enumerate(sources, 2):
        try:
            year = int(source[time_column])
            raw = source[value_column]
            value = (
                None
                if missing_values != "error" and not (raw or "").strip()
                else float(raw)
            )
        except (ValueError, TypeError):
            raise ValueError(
                f"Line {line}: period must be an integer and quantity must be numeric"
            ) from None
        code = (source.get("id") or "").strip().upper()
        if not code or (year, code) in seen:
            raise ValueError(f"Line {line}: blank or duplicate region in period {year}")
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(
                f"Line {line}: timeline quantities must be finite and positive; use a consistent positive cohort"
            )
        seen.add((year, code))
        periods.setdefault(year, []).append({key: source[key] for key in fields})
    if not periods:
        raise ValueError("At least one period is required")
    if missing_values != "error":
        periods = fill_missing_periods(periods, value_column, missing_values)
        fields = list(dict.fromkeys([*fields, "value_status", "value_note"]))
    cohort = None
    for year, rows in sorted(periods.items()):
        codes = {r["id"].strip().upper() for r in rows}
        if cohort is None:
            cohort = codes
        elif codes != cohort:
            raise ValueError(
                f"Region coverage changes in {year}; use a consistent positive cohort"
            )
    return fields, dict(sorted(periods.items()))


def write_snapshot_gif(layouts, years, config, bounds, path):
    """Compact slideshow of audited period endpoints; HTML has solver frames."""
    import matplotlib.pyplot as plt
    from PIL import Image

    images = []
    for layout, year in zip(layouts, years):
        fig, ax = plt.subplots(figsize=(12, 7), dpi=90, facecolor=config["background"])
        ax.set_facecolor(config["background"])
        for row in layout["regions"]:
            add_geometry(ax, shape(row["geometry"]), region_color(row, config))
        ax.set(xlim=(bounds[0], bounds[2]), ylim=(bounds[1], bounds[3]), aspect="equal")
        ax.axis("off")
        fig.suptitle(f"{config['title']} · {year}", fontsize=19, color=config["ink"])
        fig.text(
            0.5,
            0.035,
            "Each period starts from the preceding settled map",
            ha="center",
            color="#536b82",
        )
        fig.canvas.draw()
        images.append(
            Image.fromarray(np.asarray(fig.canvas.buffer_rgba()).copy()).convert("RGB")
        )
        plt.close(fig)
    images[0].save(path, save_all=True, append_images=images[1:], duration=1400, loop=0)


def run_timeline(
    csv_path,
    *,
    output=Path("output/timeline"),
    config_path=None,
    time_column="year",
    value_column="value",
    normalization="fixed",
    boundaries=DEFAULT_BOUNDARIES,
    formats=("svg", "png"),
    gif=False,
    dpi=160,
    initial_layout=None,
    initial_time="Previous map",
    volume_column=None,
    config_3d=None,
    html_js="inline",
    initial_values=None,
    missing_values="error",
    source_crs=None,
    projection=None,
):
    """Continue layouts across integer periods and return the series audit.

    ``output`` is a directory, unlike pipeline.run()'s filename stem.
    ``formats=()`` keeps the HTML viewer, checkpoints, metrics and audits while
    skipping static figures. Fixed normalization preserves area per input unit;
    shares normalization preserves total map area. Missing-value filling is
    opt-in and happens before simulation, retaining status and method notes.
    The completed series must have a stable positive cohort. A volume column enables
    experimental 3D playback; the default is a flat map.
    """
    if normalization not in ("fixed", "shares"):
        raise ValueError("normalization must be fixed or shares")
    if html_js not in ("inline", "cdn"):
        raise ValueError("html_js must be inline or cdn")
    if (config_3d or initial_values) and not volume_column:
        raise ValueError("3D configuration / initial values require volume_column")
    if volume_column and missing_values != "error":
        raise ValueError(
            "Automatic missing-value filling supports 2D timelines; prepare both quantities explicitly for 3D"
        )
    if initial_layout and volume_column and not initial_values:
        raise ValueError("A 3D checkpoint needs initial_values with both quantities")
    from .extrusion import load_3d_config

    scene_config = load_3d_config(config_3d)
    config = load_config(config_path, source_crs=source_crs, projection=projection)
    if config_3d is None:
        scene_config.update(title=config["title"], source_note=config["source_note"])
    fields, periods = read_timeline(
        csv_path,
        time_column,
        value_column,
        missing_values=missing_values,
    )
    output = Path(output)
    inputs = output / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    source = load_map(
        boundaries, source_crs=config["source_crs"], projection=config["projection"]
    )
    catalog = source.catalog
    totals, normalized, pairs_by_year = {}, {}, {}
    collision_cache = {}
    for year, rows in periods.items():
        with (inputs / f"{year}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        normalized[year], coverage = read_values(
            inputs / f"{year}.csv", catalog, value_column
        )
        totals[year] = coverage["sum_of_values"]
        if volume_column:
            pairs_by_year[year] = read_quantity_pairs(
                inputs / f"{year}.csv", value_column, volume_column
            )
        else:
            pairs_by_year[year] = {r["id"]: (r["value"], 0.0) for r in normalized[year]}
    first_rows = next(iter(normalized.values()))
    grouping = [(r["id"], r["group"], r["parent"]) for r in first_rows]
    for year, rows in normalized.items():
        if [(r["id"], r["group"], r["parent"]) for r in rows] != grouping:
            raise ValueError(
                f"Region groups or parents change in {year}; use a consistent cohort and grouping"
            )
    # Anchor the scale to the first observation. Appending future periods must
    # not retroactively change all earlier maps or their coordinate frame.
    area_scale = config["total_area"] / next(iter(totals.values()))
    frames, endpoints, reports, layouts = [], [], [], []
    base, previous_path, previous_year = None, None, None
    base_areas = None
    display_periods = list(periods)
    if initial_layout:
        base = json.loads(Path(initial_layout).read_text(encoding="utf-8"))
        # Match the pipeline's code ordering even for externally reordered JSON.
        base["regions"].sort(key=lambda r: r["id"])
        if str(initial_time) in {str(year) for year in periods}:
            raise ValueError("Checkpoint time must differ from the input periods")
        if volume_column:
            from .extrusion import build_scene

            initial_pairs = read_quantity_pairs(
                initial_values, value_column, volume_column
            )
            build_scene(base, initial_pairs, config=scene_config, style=config)
            pairs_by_year[initial_time] = initial_pairs
        else:
            pairs_by_year[initial_time] = {
                r["id"]: (r["value"], 0.0) for r in base["regions"]
            }
        base_areas = np.array([shape(row["geometry"]).area for row in base["regions"]])
        values = np.array([row["value"] for row in base["regions"]])
        if len(base_areas) != len(first_rows) or {r["id"] for r in base["regions"]} != {
            r["id"] for r in first_rows
        }:
            raise ValueError("Checkpoint region coverage differs from the time series")
        if (
            np.any(~np.isfinite(values))
            or np.any(values <= 0)
            or np.any(~np.isfinite(base_areas))
            or np.any(base_areas <= 0)
        ):
            raise ValueError(
                "Checkpoint quantities and areas must be finite and positive"
            )
        area_scale = float(base_areas.sum() / values.sum())
        if not np.allclose(base_areas / values, area_scale, rtol=1e-8, atol=0):
            raise ValueError("Checkpoint areas are not proportional to its values")
        if normalization == "shares" and not math.isclose(
            float(base_areas.sum()), config["total_area"], rel_tol=1e-8
        ):
            raise ValueError(
                "Shares mode requires the checkpoint total area to match total_area"
            )
        frames.append(
            {
                "year": initial_time,
                "from": initial_time,
                "phase": "settled",
                "progress": 1.0,
                "positions": [r["position"] for r in base["regions"]],
                "part_positions": [
                    list(part.centroid.coords[0])
                    for r in base["regions"]
                    for part in polygon_parts(shape(r["geometry"]))
                ],
                "scales": [1.0] * len(base_areas),
            }
        )
        endpoints.append(0)
        if gif:
            layouts.append(base)
        previous_path, previous_year = Path(initial_layout), initial_time
        display_periods.insert(0, initial_time)
    for year in periods:
        period_config = {**config, "title": f"{config['title']} · {year}"}
        if normalization == "fixed":
            period_config["total_area"] = area_scale * totals[year]
        config_file = inputs / f"{year}.json"
        config_file.write_text(
            json.dumps(period_config, indent=2) + "\n", encoding="utf-8"
        )

        def capture(frame):
            # Store only transforms. Each region coastline is emitted once.
            if frame["progress"] == 0:
                # The preceding endpoint already contains this exact state.
                return
            frames.append(
                {
                    "year": year,
                    "from": previous_year,
                    "phase": frame["phase"],
                    "progress": frame["progress"],
                    "positions": frame["positions"].tolist(),
                    "part_positions": frame["part_positions"].tolist(),
                    "scales": np.sqrt(frame["areas"] / base_areas).tolist(),
                }
            )

        stem = output / f"{year}"
        report = run(
            inputs / f"{year}.csv",
            config_path=config_file,
            output=stem,
            value_column=value_column,
            boundaries=boundaries,
            formats=formats,
            dpi=dpi,
            initial_layout=previous_path,
            frame_sink=capture if previous_path else None,
            collision_cache=collision_cache,
            _source=source,
        )
        layout_path = stem.with_suffix(".layout.json")
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        if base is None:
            base = layout
            base_areas = np.array(
                [shape(row["geometry"]).area for row in base["regions"]]
            )
            frames.append(
                {
                    "year": year,
                    "from": year,
                    "phase": "settled",
                    "progress": 1.0,
                    "positions": [row["position"] for row in layout["regions"]],
                    "part_positions": [
                        list(part.centroid.coords[0])
                        for row in layout["regions"]
                        for part in polygon_parts(shape(row["geometry"]))
                    ],
                    "scales": [1.0] * len(base_areas),
                }
            )
        endpoints.append(len(frames) - 1)
        if gif:
            layouts.append(layout)
        reports.append(
            {
                "year": year,
                "total_value": totals[year],
                "area_scale": report["config"]["total_area"] / totals[year],
                "layout": layout_path.name,
                "packing": report["packing"],
                "shape_approximation": report["shape_approximation"],
                "max_relative_area_error": report["max_relative_area_error"],
                "minimum_region_distance": report["minimum_region_distance"],
                "minimum_group_distance": report["minimum_group_distance"],
                "overlap_pairs": report["overlap_pairs"],
                "continuation": report.get("continuation"),
            }
        )
        previous_path, previous_year = layout_path, year
    from .viewer import write_timeline_viewer

    quantities = {
        year: [pairs[row["id"]] for row in base["regions"]]
        for year, pairs in pairs_by_year.items()
    }
    bounds, viewer_report = write_timeline_viewer(
        base,
        frames,
        endpoints,
        config,
        normalization,
        output / "timeline.html",
        quantities=quantities,
        config_3d=scene_config,
        html_js=html_js,
        has_volume=bool(volume_column),
        value_statuses={
            **(
                {
                    initial_time: [
                        r.get("value_status", "observed") for r in base["regions"]
                    ]
                }
                if initial_layout
                else {}
            ),
            **{year: [r["value_status"] for r in normalized[year]] for year in periods},
        },
        value_notes={
            **(
                {initial_time: [r.get("value_note", "") for r in base["regions"]]}
                if initial_layout
                else {}
            ),
            **{year: [r["value_note"] for r in normalized[year]] for year in periods},
        },
    )
    if gif:
        write_snapshot_gif(
            layouts, display_periods, config, bounds, output / "timeline.gif"
        )
    manifest = {
        "schema_version": 1,
        "normalization": normalization,
        "map": {"label": source.label, **source.projection.settings},
        "fixed_area_scale": area_scale if normalization == "fixed" else None,
        "input_sha256": hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
        "time_column": time_column,
        "value_column": value_column,
        "volume_column": volume_column,
        "missing_values": missing_values,
        "estimated_counts": {
            year: sum(r["value_status"] != "observed" for r in normalized[year])
            for year in periods
        },
        "viewer": viewer_report,
        "region_count": len(first_rows),
        "frame_count": len(frames),
        "period_count": len(periods),
        "periods": reports,
        "initial_layout_sha256": hashlib.sha256(
            Path(initial_layout).read_bytes()
        ).hexdigest()
        if initial_layout
        else None,
        "html_bytes": (output / "timeline.html").stat().st_size,
        "note": "Annual endpoint maps use prepared inputs, including flagged estimates. Playback follows captured solver states between endpoints; those intermediate states are not extra observations.",
    }
    (output / "timeline.audit.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        f"Saved {output}/timeline.html | {len(periods)} periods, {len(frames)} solver frames",
        flush=True,
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Long CSV: year, id, value")
    parser.add_argument("--time-column", default="year")
    parser.add_argument("--value-column", default="value")
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--missing-values",
        choices=["error", "interpolate", "carry"],
        default="error",
        help="Fill missing regions/values on a fixed territorial basis and mark estimates grey (default: error)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("output/timeline"), help="Output directory"
    )
    parser.add_argument(
        "--normalization",
        choices=["fixed", "shares"],
        default="fixed",
        help="fixed: same area per unit; shares: same total map area (default: fixed)",
    )
    add_map_arguments(parser)
    parser.add_argument(
        "--formats", nargs="+", choices=["svg", "png", "pdf"], default=["svg", "png"]
    )
    parser.add_argument(
        "--viewer-only",
        action="store_true",
        help="Skip per-period figures; retain checkpoints and audits",
    )
    three = parser.add_argument_group("Experimental 3D")
    three.add_argument(
        "--volume-column", help="Second total quantity; height = second / first"
    )
    three.add_argument(
        "--config-3d",
        type=Path,
        help="3D labels, common height scale, and material",
    )
    parser.add_argument("--html-js", choices=["inline", "cdn"], default="inline")
    three.add_argument(
        "--initial-values",
        type=Path,
        help="Paired quantities for a 3D initial checkpoint",
    )
    parser.add_argument(
        "--gif",
        action="store_true",
        help="Also export a slideshow of audited period endpoints",
    )
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument(
        "--initial-layout",
        type=Path,
        help="Continue this series from an existing .layout.json",
    )
    parser.add_argument(
        "--initial-time",
        default="Previous map",
        help="Label of the checkpoint observation",
    )
    args = parser.parse_args()
    try:
        if args.dpi < 50:
            raise ValueError("dpi must be at least 50")
        run_timeline(
            args.csv,
            output=args.out,
            config_path=args.config,
            time_column=args.time_column,
            value_column=args.value_column,
            normalization=args.normalization,
            boundaries=args.boundaries,
            source_crs=args.source_crs,
            projection=args.projection,
            formats=() if args.viewer_only else args.formats,
            gif=args.gif,
            dpi=args.dpi,
            volume_column=args.volume_column,
            config_3d=args.config_3d,
            html_js=args.html_js,
            initial_values=args.initial_values,
            initial_layout=args.initial_layout,
            initial_time=args.initial_time,
            missing_values=args.missing_values,
        )
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
