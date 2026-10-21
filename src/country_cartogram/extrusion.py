"""Extrude an audited 2D layout: area = A, height = B/A, volume = B."""
from __future__ import annotations

import argparse
import csv
import hashlib
from html import escape
import json
import math
from pathlib import Path
import time

import numpy as np
import shapely
from shapely.geometry import shape
from shapely.geometry.polygon import orient

from .configuration import DEFAULTS, country_color, load_config


DEFAULTS_3D = {
    'title': 'A second dimension of the world',
    'area_label': 'First variable',
    'volume_label': 'Second variable',
    'height_label': 'Second / first variable',
    'source_note': 'User-supplied quantities',
    # One common factor, never a separate normalization for each country.
    'height_scale': None,
}


def load_3d_config(path=None):
    config = dict(DEFAULTS_3D)
    if path is not None:
        supplied = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(supplied, dict):
            raise ValueError('3D configuration must be a JSON object')
        unknown = set(supplied) - set(config)
        if unknown:
            raise ValueError(f'Unknown 3D configuration keys: {sorted(unknown)}')
        config.update(supplied)
    for key in ('title', 'area_label', 'volume_label', 'height_label', 'source_note'):
        if not isinstance(config[key], str):
            raise ValueError(f'{key} must be a string')
    scale = config['height_scale']
    if scale is not None and (isinstance(scale, bool) or not isinstance(scale, (int, float))
                              or not math.isfinite(scale) or scale <= 0):
        raise ValueError('height_scale must be null (automatic) or finite and positive')
    return config


def require_plotly():
    try:
        import plotly.graph_objects as go
    except ImportError as exc:
        raise ValueError('3D HTML export needs Plotly. Install with: pip install "country-cartogram[3d]"') from exc
    return go


def read_quantity_pairs(path, area_column, volume_column):
    """Read explicit A and B quantities; missing is never silently zero."""
    if area_column == volume_column:
        raise ValueError('Area and volume columns must be different')
    pairs = {}
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        required = {'country_code', area_column, volume_column}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f'CSV needs columns: {sorted(required)}')
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError('CSV has duplicate column names')
        for line, source in enumerate(reader, 2):
            code = (source.get('country_code') or '').strip().upper()
            if not code or code in pairs:
                raise ValueError(f'Line {line}: blank or duplicate country_code {code!r}')
            values = []
            for column in (area_column, volume_column):
                try:
                    value = float(source[column])
                except (ValueError, TypeError):
                    raise ValueError(f'Line {line}: {code} needs a numeric {column}; blanks are not zero') from None
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f'Line {line}: {column} must be finite and non-negative')
                values.append(value)
            a, b = values
            if a == 0 and b > 0:
                raise ValueError(f'{code}: positive volume with zero area cannot be represented')
            if a > 0 and not math.isfinite(b / a):
                raise ValueError(f'{code}: second / first ratio overflows')
            pairs[code] = (a, b)
    if not any(a > 0 for a, _ in pairs.values()):
        raise ValueError('At least one strictly positive area quantity is required')
    return pairs


def extrude_polygon(geometry, height):
    """Closed, outward-oriented triangle mesh; retain holes and all parts.

    Zero height deliberately has just a roof: it is visible but has zero volume.
    Constrained triangulation prevents bridges across concavities and holes.
    """
    if geometry.geom_type not in ('Polygon', 'MultiPolygon') or geometry.is_empty or not geometry.is_valid:
        raise ValueError('Extrusion requires a nonempty valid Polygon or MultiPolygon')
    if geometry.has_z or not math.isfinite(height) or height < 0:
        raise ValueError('Use 2D footprints and finite non-negative heights')
    # GEOS triangulates the complete footprint in compiled code. Vectorize
    # orientation and vertex deduplication; no Python loop per triangle.
    triangles = shapely.constrained_delaunay_triangles(geometry)
    coordinates = shapely.get_coordinates(triangles).reshape(-1, 4, 2)[:, :3].copy()
    u, v = coordinates[:, 1] - coordinates[:, 0], coordinates[:, 2] - coordinates[:, 0]
    reverse = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0] < 0
    coordinates[reverse] = coordinates[reverse, ::-1]
    xy, inverse = np.unique(coordinates.reshape(-1, 2), axis=0, return_inverse=True)
    roof = inverse.reshape(-1, 3)
    n = len(xy)
    bottom_vertices = np.column_stack((xy, np.zeros(n)))
    if height == 0:
        return bottom_vertices, roof, roof
    vertices = np.vstack((bottom_vertices, np.column_stack((xy, np.full(n, height)))))
    indices = {tuple(point): i for i, point in enumerate(xy)}
    boundary_edges = []
    parts = [geometry] if geometry.geom_type == 'Polygon' else geometry.geoms
    for part in parts:
        part = orient(part, sign=1)
        for ring in [part.exterior, *part.interiors]:
            boundary = [indices[point] for point in ring.coords]
            boundary_edges.extend(zip(boundary, boundary[1:]))
    edges = np.asarray(boundary_edges)
    a, b = edges[:, 0], edges[:, 1]
    faces = np.vstack((roof + n, roof[:, ::-1],
                       np.column_stack((a, b, b + n)),
                       np.column_stack((a, b + n, a + n))))
    return vertices, faces, roof + n


def audit_mesh(vertices, faces, roof_faces, geometry, height):
    """Independently measure the emitted triangles, not just A times height."""
    roof = vertices[roof_faces]
    edges1, edges2 = roof[:, 1] - roof[:, 0], roof[:, 2] - roof[:, 0]
    roof_area = float(np.linalg.norm(np.cross(edges1, edges2), axis=1).sum() / 2)
    area_error = abs(roof_area / geometry.area - 1)
    closed = None
    volume = 0.0
    if height > 0:
        edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
        edges.sort(axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        closed = bool(np.all(counts == 2))
        # Center coordinates before the signed-tetrahedra sum for small islands
        # far from the origin. Outward winding gives positive signed volume.
        triangles = (vertices - vertices.mean(axis=0))[faces]
        volume = float(np.einsum('ij,ij->i', triangles[:, 0],
                       np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6)
    expected = geometry.area * height
    volume_error = abs(volume / expected - 1) if expected > 0 else abs(volume)
    if not math.isfinite(volume) or area_error > 1e-8 or volume_error > 1e-7 or closed is False:
        raise RuntimeError(f'3D mesh audit failed: roof error={area_error}, volume error={volume_error}, closed={closed}')
    return {'mesh_footprint_area': roof_area, 'mesh_volume': volume,
            'mesh_relative_area_error': area_error, 'mesh_relative_volume_error': volume_error,
            'closed_mesh': closed, 'triangle_count': len(faces)}


def build_scene(layout, pairs, *, config=None, style=None):
    """Return meshes and a report. Does not move or rescale any footprint."""
    config = {**DEFAULTS_3D, **(config or {})}
    style = style or DEFAULTS
    rows = layout.get('countries', [])
    codes = [row['country_code'] for row in rows]
    if not rows or len(codes) != len(set(codes)):
        raise ValueError('Layout needs nonempty, unique country records')
    positive = {code for code, (a, _) in pairs.items() if a > 0}
    if set(codes) != positive:
        raise ValueError('CSV/layout coverage mismatch: '
                         f'missing quantities={sorted(set(codes) - positive)}, '
                         f'positive quantities absent from layout={sorted(positive - set(codes))}')
    geometries = []
    for row in rows:
        a, b = pairs[row['country_code']]
        if not math.isfinite(row['value']) or not math.isclose(row['value'], a, rel_tol=1e-12, abs_tol=0):
            raise ValueError(f"{row['country_code']}: area quantity differs from saved layout; repack first")
        geometry = shape(row['geometry'])
        if (geometry.geom_type not in ('Polygon', 'MultiPolygon') or geometry.is_empty
                or not geometry.is_valid or geometry.has_z or not math.isfinite(geometry.area)
                or geometry.area <= 0):
            raise ValueError(f"{row['country_code']}: invalid 2D footprint")
        geometries.append(geometry)
    try:
        area_scale = math.fsum(g.area for g in geometries) / math.fsum(pairs[c][0] for c in codes)
    except OverflowError:
        raise ValueError('Sum of quantities is too large') from None
    if not math.isfinite(area_scale) or area_scale <= 0:
        raise ValueError('Area normalization must be finite and positive')
    bounds = np.asarray([g.bounds for g in geometries])
    extent = [float(bounds[:, 0].min()), float(bounds[:, 1].min()),
              float(bounds[:, 2].max()), float(bounds[:, 3].max())]
    span = max(extent[2] - extent[0], extent[3] - extent[1])
    max_ratio = max(pairs[c][1] / pairs[c][0] for c in codes)
    height_scale = config['height_scale']
    if height_scale is None:
        height_scale = span * .25 / max_ratio if max_ratio > 0 else 1.0
    if not math.isfinite(height_scale) or height_scale <= 0:
        raise ValueError('Height scale must be finite and positive')
    volume_scale = area_scale * height_scale
    if not math.isfinite(volume_scale) or volume_scale <= 0:
        raise ValueError('Volume normalization overflows or underflows; change input units or height_scale')
    countries = []
    for row, geometry in zip(rows, geometries):
        code = row['country_code']
        a, b = pairs[code]
        ratio = b / a
        height = height_scale * ratio
        if not math.isfinite(height) or (b > 0 and height <= 0):
            raise ValueError(f'{code}: height overflows or underflows; change units or height_scale')
        area_error = abs(geometry.area / (area_scale * a) - 1)
        if area_error > 1e-8:
            raise ValueError(f'{code}: footprint areas are not proportional to the first variable')
        vertices, faces, roofs = extrude_polygon(geometry, height)
        measurements = audit_mesh(vertices, faces, roofs, geometry, height)
        expected_volume = volume_scale * b
        if b > 0 and (not math.isfinite(expected_volume) or expected_volume <= 0):
            raise ValueError(f'{code}: volume overflows or underflows; change units or height_scale')
        proportional_error = abs(measurements['mesh_volume'] / expected_volume - 1) if b else abs(measurements['mesh_volume'])
        if proportional_error > 2e-7:
            raise RuntimeError(f'{code}: mesh volume is not proportional to the second variable')
        countries.append({
            'country_code': code, 'name': row['name'], 'cluster': row['cluster'],
            'color': country_color(row, style), 'area_value': a, 'volume_value': b,
            'ratio': ratio, 'height': height, 'footprint_area': geometry.area,
            'relative_area_error': area_error, 'relative_volume_error': proportional_error,
            **measurements, 'vertices': vertices, 'faces': faces,
        })
    report = {
        'schema_version': 1, 'country_count': len(countries),
        'coordinate_system': 'arbitrary cartogram chart units; NOT longitude/latitude',
        'encoding': {'area': 'area_scale * first variable',
                     'height': 'height_scale * second variable / first variable',
                     'volume': 'area_scale * height_scale * second variable'},
        'area_scale': area_scale, 'height_scale': height_scale, 'volume_scale': volume_scale,
        'height_scale_mode': 'automatic' if config['height_scale'] is None else 'explicit',
        'extent': extent, 'max_height': max(c['height'] for c in countries),
        'max_relative_area_error': max(c['relative_area_error'] for c in countries),
        'max_relative_volume_error': max(c['relative_volume_error'] for c in countries),
        'max_mesh_relative_volume_error': max(c['mesh_relative_volume_error'] for c in countries),
        'all_positive_height_meshes_closed': all(c['closed_mesh'] for c in countries if c['height'] > 0),
        'zero_height_country_codes': [c['country_code'] for c in countries if c['height'] == 0],
        'zero_area_zero_volume_country_codes': sorted(c for c, (a, _) in pairs.items() if a == 0),
        'mesh_vertex_count': sum(len(c['vertices']) for c in countries),
        'mesh_triangle_count': sum(len(c['faces']) for c in countries),
        'config': config, 'versions': {'shapely': shapely.__version__, 'numpy': np.__version__},
    }
    return countries, report


def make_figure(countries, report):
    go = require_plotly()
    config = report['config']
    traces, seen = [], set()
    for country in countries:
        v, f = country['vertices'], country['faces']
        group = country['cluster']
        hover = '<b>' + escape(country['name']) + ' (' + escape(country['country_code']) + ')</b><br>'
        for label, key in [('area_label', 'area_value'), ('height_label', 'ratio'), ('volume_label', 'volume_value')]:
            hover += escape(config[label]) + ': ' + f'{country[key]:,.4g}' + '<br>'
        traces.append(go.Mesh3d(
            x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
            color=country['color'], name=escape(group), legendgroup=escape(group),
            showlegend=group not in seen, flatshading=True, hovertemplate=hover + '<extra></extra>',
            lighting={'ambient': .65, 'diffuse': .7, 'specular': .08, 'roughness': .9},
            lightposition={'x': -1000, 'y': -2000, 'z': 3000},
        ))
        seen.add(group)
    x0, y0, x1, y1 = report['extent']
    dx, dy = x1 - x0, y1 - y0
    zmax = max(report['max_height'] * 1.06, max(dx, dy) * .015)
    ticks = np.linspace(0, report['max_height'], 5) if report['max_height'] else [0]
    camera = {'eye': {'x': .35, 'y': -1.65, 'z': 1.45},
              'up': {'x': 0, 'y': 0, 'z': 1}, 'projection': {'type': 'orthographic'}}
    top = {'eye': {'x': 0, 'y': 0, 'z': 2.5},
           'up': {'x': 0, 'y': 1, 'z': 0}, 'projection': {'type': 'orthographic'}}
    figure = go.Figure(traces)
    figure.update_layout(
        paper_bgcolor='#f5f8fc', font={'family': 'Arial, sans-serif', 'color': '#18314a'},
        margin={'l': 8, 'r': 8, 't': 48, 'b': 12}, height=700,
        legend={'orientation': 'h', 'x': 0, 'y': 1.07, 'groupclick': 'togglegroup'},
        scene={
            'xaxis': {'visible': False, 'range': [x0 - dx * .03, x1 + dx * .03]},
            'yaxis': {'visible': False, 'range': [y0 - dy * .03, y1 + dy * .03]},
            'zaxis': {'title': {'text': escape(config['height_label'])}, 'range': [0, zmax],
                      'tickvals': list(ticks),
                      'ticktext': [f'{t / report["height_scale"]:,.3g}' for t in ticks],
                      'showbackground': False, 'gridcolor': '#d6e0eb', 'zerolinecolor': '#a2b4c7'},
            'aspectmode': 'manual', 'aspectratio': {'x': 1, 'y': dy / dx, 'z': zmax / dx},
            'camera': camera, 'dragmode': 'orbit', 'bgcolor': '#f5f8fc',
        },
        updatemenus=[{'type': 'buttons', 'direction': 'left', 'x': 0, 'y': 0,
                     'xanchor': 'left', 'yanchor': 'bottom', 'buttons': [
                         {'label': '3D view', 'method': 'relayout', 'args': [{'scene.camera': camera}]},
                         {'label': 'Top view', 'method': 'relayout', 'args': [{'scene.camera': top}]},
                     ]}],
    )
    return figure


def write_html(countries, report, path, *, html_js='inline'):
    """Self-contained by default. CDN is an explicit smaller-file opt-in."""
    if html_js not in ('inline', 'cdn'):
        raise ValueError('html_js must be inline or cdn')
    figure = make_figure(countries, report)
    plot = figure.to_html(full_html=False, include_plotlyjs=True if html_js == 'inline' else 'cdn',
                          div_id='cartogram-3d', config={'responsive': True, 'displaylogo': False,
                                                       'toImageButtonOptions': {'filename': path.stem, 'scale': 2}})
    cfg = report['config']
    cells = ''.join('<tr><th scope="row">' + escape(c['name']) + '</th><td>' + escape(c['country_code']) +
                    '</td>' + ''.join(f'<td>{c[k]:,.6g}</td>' for k in ('area_value', 'ratio', 'volume_value')) + '</tr>'
                    for c in sorted(countries, key=lambda c: c['name']))
    headings = ''.join('<th scope="col">' + escape(cfg[k]) + '</th>'
                       for k in ('area_label', 'height_label', 'volume_label'))
    cards = ''.join('<div><small>' + role + '</small><strong>' + escape(cfg[key]) + '</strong></div>'
                    for role, key in [('FOOTPRINT AREA', 'area_label'), ('HEIGHT · SECOND ÷ FIRST', 'height_label'),
                                      ('VOLUME', 'volume_label')])
    html = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + escape(cfg['title']) + '''</title>
<style>body{margin:0;background:#f5f8fc;color:#18314a;font:16px/1.55 system-ui,sans-serif}
main{max-width:1440px;margin:auto;padding:30px 4vw}h1{font-size:clamp(26px,4vw,40px);margin:0 0 8px;letter-spacing:-.035em}
.eyebrow{font-size:12px;letter-spacing:.14em;color:#536f88}p{color:#536b82}section.encoding{display:flex;gap:14px;flex-wrap:wrap;margin:22px 0}
.encoding div{flex:1;min-width:210px;background:white;border:1px solid #dbe4ee;border-radius:12px;padding:15px 20px}
small,strong{display:block}small{font-size:10px;letter-spacing:.09em;color:#536b82}strong{font-size:18px}
.plot-container{border:1px solid #dbe4ee;border-radius:14px;overflow:hidden}details{margin:26px 0;background:white;border-radius:10px;padding:16px}
summary{cursor:pointer;font-weight:650}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:right;border-bottom:1px solid #e4eaf1;padding:8px}th:first-child{text-align:left}
.table-wrap{overflow:auto;max-height:460px}footer{font-size:13px;color:#536b82}code{color:#18314a}
</style></head><body><main><div class="eyebrow">COUNTRY CARTOGRAM / TWO VARIABLES</div><h1>''' + escape(cfg['title']) + '''</h1>
<p>One footprint, one height, two quantities. Area × height gives volume.</p><section class="encoding">''' + cards + '''</section>
<p>Drag to rotate · scroll to zoom · hover for values · click a continent to hide/show it. Use Top view to inspect footprints.</p>
<div class="plot-container">''' + plot + '''</div><details><summary>Country values — ''' + str(len(countries)) + ''' countries / territories</summary>
<div class="table-wrap"><table><thead><tr><th scope="col">Country</th><th scope="col">Code</th>''' + headings + '''</tr></thead><tbody>''' + cells + '''</tbody></table></div></details>
<footer><p>''' + escape(cfg['source_note']) + '''</p><p>Linear heights share one global scale. No log transform, clipping, or minimum height is applied.
Zero second-variable values remain flat. Taller shapes can hide others; rotate the view or inspect the table.</p>
<p>''' + ('Self-contained HTML; no network connection required.' if html_js == 'inline' else 'This compact export loads Plotly from its versioned CDN; an internet connection is required.') + '''</p></footer>
</main></body></html>'''
    Path(path).write_text(html, encoding='utf-8')


def write_preview(countries, report, path, dpi=160):
    """Static orthographic view of the exact same meshes, without a browser."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    cfg = report['config']
    fig = plt.figure(figsize=(16, 10), facecolor='#f5f8fc')
    fig.text(.04, .95, cfg['title'], fontsize=27, weight='bold', color='#18314a')
    fig.text(.04, .91, f"Area: {cfg['area_label']}   |   Height: {cfg['height_label']}   |   Volume: {cfg['volume_label']}",
             fontsize=12, color='#536b82')
    ax = fig.add_axes([.01, .17, .96, .75], projection='3d', facecolor='#f5f8fc')
    for country in countries:
        triangles = country['vertices'][country['faces']]
        ax.add_collection3d(Poly3DCollection(triangles, facecolors=country['color'],
                                            edgecolors=country['color'], linewidths=0, antialiased=False,
                                            shade=True, zsort='average'))
    x0, y0, x1, y1 = report['extent']
    dx, dy = x1 - x0, y1 - y0
    zmax = max(report['max_height'] * 1.05, max(dx, dy) * .02)
    ax.set(xlim=(x0, x1), ylim=(y0, y1), zlim=(0, zmax))
    ax.set_box_aspect((dx, dy, zmax), zoom=1.35)
    ax.set_proj_type('ortho')
    ax.view_init(elev=49, azim=-80)
    ax.set_xticks([]); ax.set_yticks([])
    ax.zaxis.set_major_locator(MaxNLocator(4))
    ax.zaxis.set_major_formatter(FuncFormatter(lambda z, _: f'{z / report["height_scale"]:,.3g}'))
    ax.set_zlabel(cfg['height_label'], fontsize=9, labelpad=8)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.fill = False
        axis.pane.set_edgecolor('#f5f8fc')
        axis.line.set_color('#c9d6e3')
    ax.grid(False)
    groups = {c['cluster']: c['color'] for c in countries}
    fig.legend(handles=[Patch(color=color, label=group) for group, color in sorted(groups.items())],
               loc='lower center', bbox_to_anchor=(.5, .075), ncol=min(len(groups), 6), frameon=False, fontsize=10)
    fig.text(.04, .04, cfg['source_note'], fontsize=8, color='#536b82')
    fig.text(.04, .02, 'Linear height = second / first. Rotate and inspect individual values in the companion HTML.',
             fontsize=9, color='#536b82')
    fig.savefig(path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)


def export_3d(layout_path, csv_path, *, volume_column, area_column='value', output=None,
              config_path=None, style_path=None, html_js='inline', preview=False):
    """Reuse a saved layout with matching first-variable values, plus a new B."""
    started = time.perf_counter()
    require_plotly()
    config = load_3d_config(config_path)
    layout_path, csv_path = Path(layout_path), Path(csv_path)
    layout = json.loads(layout_path.read_text(encoding='utf-8'))
    pairs = read_quantity_pairs(csv_path, area_column, volume_column)
    if style_path:
        style = load_config(style_path)
    else:
        # Inherit colors from the companion 2D audit when present.
        name = layout_path.name.removesuffix('.layout.json')
        audit_path = layout_path.with_name(name + '.audit.json')
        style = {**DEFAULTS, **(json.loads(audit_path.read_text())['config'] if audit_path.exists() else {})}
    loaded = time.perf_counter()
    countries, report = build_scene(layout, pairs, config=config, style=style)
    meshed = time.perf_counter()
    import plotly
    report['versions']['plotly'] = plotly.__version__
    report.update({'area_column': area_column, 'volume_column': volume_column,
                   'layout_sha256': hashlib.sha256(layout_path.read_bytes()).hexdigest(),
                   'input_sha256': hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                   'html_js': html_js})
    stem = Path(output) if output is not None else layout_path.with_name(layout_path.name.removesuffix('.layout.json'))
    stem.parent.mkdir(parents=True, exist_ok=True)
    # Append suffixes: output names containing dots are kept intact.
    path = lambda suffix: stem.parent / (stem.name + suffix)
    write_html(countries, report, path('.3d.html'), html_js=html_js)
    html_done = time.perf_counter()
    if preview:
        write_preview(countries, report, path('.3d.png'))
    preview_done = time.perf_counter()
    fields = [k for k in countries[0] if k not in ('vertices', 'faces')]
    with path('.3d.metrics.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: c[k] for k in fields} for c in countries)
    report['html_bytes'] = path('.3d.html').stat().st_size
    report['timings_seconds'] = {'load': loaded - started, 'mesh_and_audit': meshed - loaded,
                                'html': html_done - meshed, 'preview': preview_done - html_done,
                                'total_before_audit_write': time.perf_counter() - started}
    path('.3d.audit.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(f"Saved {stem}.3d.* | max volume error={report['max_relative_volume_error']:.2e}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('layout', type=Path, help='Saved .layout.json from the 2D pipeline')
    parser.add_argument('csv', type=Path, help='CSV with first and second quantities keyed by country_code')
    parser.add_argument('--area-column', default='value')
    parser.add_argument('--volume-column', required=True)
    parser.add_argument('--config', type=Path, help='3D titles, labels, source, and common height_scale')
    parser.add_argument('--style', type=Path, help='Optional 2D configuration for cluster colors')
    parser.add_argument('--out', type=Path, help='Output stem; defaults to the layout stem')
    parser.add_argument('--html-js', choices=['inline', 'cdn'], default='inline')
    parser.add_argument('--preview', action='store_true', help='Also render an orthographic PNG')
    args = parser.parse_args()
    try:
        export_3d(args.layout, args.csv, volume_column=args.volume_column, area_column=args.area_column,
                  output=args.out, config_path=args.config, style_path=args.style,
                  html_js=args.html_js, preview=args.preview)
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
