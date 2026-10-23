"""Measure mesh/HTML export from a saved layout; write one benchmark JSON."""

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import platform
import statistics
import tempfile

from country_cartogram.extrusion import export_3d


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--area-column", default="value")
    parser.add_argument("--volume-column", required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--html-js", choices=["inline", "cdn"], default="inline")
    parser.add_argument("--out", type=Path, default=Path("output/benchmark"))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    with (
        tempfile.TemporaryDirectory(prefix="cartogram-benchmark-") as directory,
        redirect_stdout(io.StringIO()),
    ):
        reports = [
            export_3d(
                args.layout,
                args.csv,
                area_column=args.area_column,
                volume_column=args.volume_column,
                config_path=args.config,
                output=Path(directory) / "map",
                html_js=args.html_js,
            )
            for _ in range(args.repeats)
        ]
    first = reports[0]
    result = {
        "description": "Mesh generation, audits and HTML export; excludes physics, PNGs and browser rendering",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "versions": first["versions"],
        "region_count": first["region_count"],
        "layout_sha256": first["layout_sha256"],
        "input_sha256": first["input_sha256"],
        "html_js": args.html_js,
        "html_bytes": first["html_bytes"],
        "mesh_vertices": first["mesh_vertex_count"],
        "mesh_triangles": first["mesh_triangle_count"],
        "runs_seconds": [r["timings_seconds"] for r in reports],
        "median_seconds": statistics.median(
            r["timings_seconds"]["total_before_audit_write"] for r in reports
        ),
        "max_relative_volume_error": max(
            r["max_relative_volume_error"] for r in reports
        ),
    }
    path = args.out.with_name(args.out.name + ".benchmark.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
