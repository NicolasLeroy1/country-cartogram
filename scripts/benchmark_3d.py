"""Measure 3D export from an existing layout, without rerunning physics."""
import argparse
import json
from pathlib import Path
import platform
import statistics

from country_cartogram.extrusion import export_3d

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('layout', type=Path)
parser.add_argument('csv', type=Path)
parser.add_argument('--area-column', default='value')
parser.add_argument('--volume-column', required=True)
parser.add_argument('--config', type=Path)
parser.add_argument('--repeats', type=int, default=3)
parser.add_argument('--out', type=Path, default=Path('output/benchmark'))
args = parser.parse_args()
if args.repeats < 1:
    parser.error('repeats must be positive')
reports = [export_3d(args.layout, args.csv, area_column=args.area_column,
                     volume_column=args.volume_column, config_path=args.config,
                     output=args.out, html_js='cdn') for _ in range(args.repeats)]
result = {
    'description': '3D mesh generation, audit, and CDN HTML export; no 2D physics or static PNG',
    'python': platform.python_version(), 'platform': platform.platform(),
    'versions': reports[0]['versions'], 'country_count': reports[0]['country_count'],
    'mesh_vertices': reports[0]['mesh_vertex_count'], 'mesh_triangles': reports[0]['mesh_triangle_count'],
    'html_bytes': reports[0]['html_bytes'],
    'runs_seconds': [r['timings_seconds'] for r in reports],
    'median_seconds': statistics.median(r['timings_seconds']['total_before_audit_write'] for r in reports),
    'max_relative_volume_error': max(r['max_relative_volume_error'] for r in reports),
}
args.out.with_name(args.out.name + '.benchmark.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
