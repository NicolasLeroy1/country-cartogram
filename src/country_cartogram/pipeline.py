#!/usr/bin/env python3
"""CSV -> shape-preserving clustered cartogram -> PNG / SVG / PDF + audit files."""
from __future__ import annotations
import argparse
import colorsys
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import textwrap
import warnings

import numpy as np
import shapely
from shapely.geometry import mapping, box
from shapely.ops import polylabel
from shapely.geometry.polygon import orient
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.colors import to_rgb, to_hex

from .geometry import prepare_country, layout_countries, polygon_parts
from .physics import layout_physics as layout_progressive

ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    'title': 'The world, resized',
    'subtitle': 'Country area represents quantity; color represents cluster',
    'source_note': 'User-supplied quantities',
    'prefix': '', 'divisor': 1, 'suffix': '', 'decimals': 1,
    'total_area': 10000, 'country_gap': .4, 'cluster_gap': 2.5,
    'geographic_spread': .72, 'geographic_pull': .002, 'cluster_pull': .006,
    'layout_mode': 'growth', 'growth_steps': 80, 'relax_per_step': 20,
    'settle_steps': 6000, 'position_tolerance': .002,
    'physics_dt': .04, 'contact_iterations': 50, 'collision_tolerance': .12,
    'collision_mode': 'hull',
    'damping': .7, 'iterations': 250, 'territory_distance': 12, 'parent_pull': .04,
    'satellite_distance': 1.4, 'min_part_fraction': .001, 'max_parts': 24,
    'simplify': .0015, 'labels': 14, 'label_countries': [], 'label_offsets': {},
    'label_aliases': {'GBR': 'UK'},
    'background': '#edf8fa', 'ink': '#10243a',
    'colors': {'North America': '#1679b7', 'South America': '#38a36d',
               'Europe': '#8b4cb0', 'Africa': '#edc54f',
               'Asia': '#e55261', 'Oceania': '#f29b45'},
    'cluster_anchors': {'North America': [-105, 42], 'South America': [-62, -18],
                        'Europe': [12, 52], 'Africa': [18, 0],
                        'Asia': [105, 32], 'Oceania': [140, -28]},
}


def load_config(path=None):
    config = json.loads(json.dumps(DEFAULTS))
    if path:
        supplied = json.loads(Path(path).read_text())
        unknown = set(supplied) - set(config)
        if unknown:
            raise ValueError(f'Unknown configuration keys: {sorted(unknown)}')
        for key, value in supplied.items():
            if key in ('colors', 'cluster_anchors'):
                config[key].update(value)
            else:
                config[key] = value
    for key in ('total_area', 'divisor', 'geographic_spread', 'satellite_distance', 'territory_distance', 'position_tolerance', 'physics_dt'):
        if not isinstance(config[key], (float, int)) or not math.isfinite(config[key]) or config[key] <= 0:
            raise ValueError(f'{key} must be finite and positive')
    for key in ('country_gap', 'cluster_gap', 'geographic_pull', 'cluster_pull', 'parent_pull', 'simplify', 'min_part_fraction', 'collision_tolerance'):
        if not isinstance(config[key], (float, int)) or not math.isfinite(config[key]) or config[key] < 0:
            raise ValueError(f'{key} must be finite and non-negative')
    for key in ('iterations', 'max_parts', 'labels', 'decimals', 'growth_steps', 'relax_per_step', 'settle_steps', 'contact_iterations'):
        if not isinstance(config[key], int) or config[key] < (0 if key in ('labels','decimals') else 1):
            raise ValueError(f'{key} has an invalid integer value')
    if config['layout_mode'] not in ('growth', 'legacy'):
        raise ValueError('layout_mode must be growth or legacy')
    if config['collision_mode'] not in ('hull', 'decomposed'):
        raise ValueError('collision_mode must be hull or decomposed')
    if not 0 <= config['damping'] < 1 or not 0 <= config['min_part_fraction'] < 1:
        raise ValueError('damping and min_part_fraction must be in [0, 1)')
    for color in [config['background'], config['ink'], *config['colors'].values()]:
        to_rgb(color)
    return config


def load_catalog(boundaries):
    collection = json.loads(Path(boundaries).read_text())
    catalog = {}
    for feature in collection['features']:
        p = feature['properties']
        if p['CONTINENT'] in ('Antarctica', 'Seven seas (open ocean)'):
            continue
        code = p['ISO_A3_EH'] if p['ISO_A3_EH'] != '-99' else p['ADM0_A3']
        # Natural Earth gives Ashmore/Cartier the same ISO as Australia.
        if p['ADM0_A3'] in ('ATC', 'IOA'):
            code = p['ADM0_A3']
        if code in catalog:
            raise ValueError(f'Duplicate boundary country_code: {code}')
        names = {'USA': 'United States', 'CHN': 'China', 'RUS': 'Russia',
                 'KOR': 'South Korea', 'PRK': 'North Korea', 'GBR': 'United Kingdom',
                 'COD': 'DR Congo', 'COG': 'Congo', 'TWN': 'Taiwan'}
        catalog[code] = {'country_code': code, 'name': names.get(code, p['NAME_EN']),
                         'cluster': p['CONTINENT'], 'parent': '',
                         'geometry': feature['geometry'], 'properties': p}
    admin_to_code = {r['properties']['ADMIN']: code for code, r in catalog.items()}
    for code, row in catalog.items():
        parent = admin_to_code.get(row['properties']['SOVEREIGNT'], '')
        if parent and parent != code:
            row['parent'] = parent
    return catalog


def read_values(path, catalog, value_column='value'):
    rows, zero_codes = [], []
    seen = set()
    with Path(path).open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {'country_code', value_column}.issubset(reader.fieldnames):
            raise ValueError(f'CSV needs country_code and {value_column} columns')
        for line, source in enumerate(reader, 2):
            code = (source.get('country_code') or '').strip().upper()
            if code not in catalog:
                raise ValueError(f'Line {line}: unknown country_code {code!r}; see data/country_catalog.csv')
            if code in seen:
                raise ValueError(f'Line {line}: duplicate country_code {code}')
            seen.add(code)
            try:
                value = float(source[value_column])
            except (TypeError, ValueError):
                raise ValueError(f'Line {line}: {code} needs a numeric {value_column}; blanks are not zero') from None
            if not math.isfinite(value) or value < 0:
                raise ValueError(f'Line {line}: {code} needs a finite non-negative value')
            if value == 0:
                zero_codes.append(code)
                continue
            base = catalog[code]
            explicit_cluster = (source.get('cluster') or '').strip()
            # A blank explicit parent disables the boundary metadata default.
            parent = (source.get('parent', base['parent']) or '').strip().upper()
            rows.append({'country_code': code, 'value': value,
                         'name': (source.get('name') or '').strip() or base['name'],
                         'cluster': explicit_cluster or base['cluster'],
                         'explicit_cluster': bool(explicit_cluster), 'parent': parent})
    if not rows:
        raise ValueError('At least one strictly positive quantity is required')
    by_code = {r['country_code']: r for r in rows}
    for row in rows:
        parent = row['parent']
        if parent and parent not in catalog:
            raise ValueError(f"Unknown parent {parent!r} for {row['country_code']}")
        if parent == row['country_code']:
            raise ValueError(f"Country {parent} cannot be its own parent")
        if parent and parent not in by_code:
            warnings.warn(f"{row['country_code']}: parent {parent} is absent; using its own geographic anchor")
            row['parent'] = ''
        if row['parent']:
            parent_row = by_code[row['parent']]
            if parent_row['parent']:
                raise ValueError('Parent links must be one level deep (no cycles or chains)')
            if row['explicit_cluster'] and row['cluster'] != parent_row['cluster']:
                raise ValueError(f"{row['country_code']} and parent {row['parent']} must share a cluster")
            row['cluster'] = parent_row['cluster']
    rows.sort(key=lambda row: row['country_code'])
    total = math.fsum(r['value'] for r in rows)
    if not math.isfinite(total):
        raise ValueError('Sum of input values is not finite')
    return rows, {'omitted_country_codes': sorted(set(catalog) - seen),
                  'zero_country_codes': zero_codes, 'sum_of_values': total}


def country_color(row, config):
    digest = int(hashlib.sha256(row['country_code'].encode()).hexdigest()[:8], 16)
    group = row['cluster']
    if group in config['colors']:
        base = to_rgb(config['colors'][group])
    else:
        hue = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) / 2**32
        base = colorsys.hls_to_rgb(hue, .52, .5)
    h, l, sat = colorsys.rgb_to_hls(*base)
    return to_hex(colorsys.hls_to_rgb(h, min(.75, max(.25, l + (digest / 2**32 - .5) * .17)), sat))


def add_geometry(ax, geom, color):
    for part in polygon_parts(geom):
        part = orient(part, sign=1)
        vertices, codes = [], []
        for ring in [part.exterior, *part.interiors]:
            xy = np.asarray(ring.coords)
            vertices.extend(xy)
            codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(xy) - 2) + [MplPath.CLOSEPOLY])
        ax.add_patch(PathPatch(MplPath(np.asarray(vertices), codes), facecolor=color,
                               edgecolor='none', linewidth=0))


def formatted(value, config):
    value /= config['divisor']
    return f"{config['prefix']}{value:,.{config['decimals']}f}{config['suffix']}"


def render(geometries, rows, config, stem, formats, dpi):
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'svg.fonttype': 'none',
                         'pdf.fonttype': 42, 'text.usetex': False})
    bg, ink = config['background'], config['ink']
    fig = plt.figure(figsize=(16, 10), facecolor=bg)
    fig.text(.045, .94, config['title'], fontsize=29, weight='bold', color=ink)
    fig.text(.046, .904, config['subtitle'], fontsize=13, color='#4d6576')
    fig.text(.954, .943, 'COUNTRY CARTOGRAM', fontsize=9, weight='bold', ha='right', color='#5a7585')
    ax = fig.add_axes([.035, .24, .93, .62], facecolor=bg)
    for g, row in zip(geometries, rows):
        add_geometry(ax, g, country_color(row, config))
    bounds = shapely.total_bounds(geometries)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    ax.set_xlim(bounds[0] - width * .055, bounds[2] + width * .055)
    ax.set_ylim(bounds[1] - height * .09, bounds[3] + height * .09)
    ax.set_aspect('equal'); ax.axis('off')
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    ranked = sorted(range(len(rows)), key=lambda i: (-rows[i]['value'], rows[i]['country_code']))
    selected = list(dict.fromkeys(ranked[:config['labels']] + [i for i, r in enumerate(rows)
                            if r['country_code'] in config['label_countries']]))
    label_boxes, omitted_labels = [], []
    all_geometry = shapely.union_all(geometries)
    for i in selected:
        row, geom = rows[i], geometries[i]
        part = max(polygon_parts(geom), key=lambda p: p.area)
        point = polylabel(part, tolerance=max(math.sqrt(part.area) * .01, .001))
        name = config['label_aliases'].get(row['country_code'], row['name'])
        label = '\n'.join(textwrap.wrap(name, 15)) + '\n' + formatted(row['value'], config)
        fontsize = min(17, max(8.5, 8.5 + 11 * math.sqrt(row['value'] / rows[ranked[0]]['value'])))
        offset = config['label_offsets'].get(row['country_code'])
        candidates = [(0, 0, fontsize, True), (0, 0, 9, True), (0, 0, 7.5, True)]
        if offset:
            candidates = [(offset[0], offset[1], 9, False)]
        else:
            unit = math.sqrt(part.area) * .6 + 3
            candidates += [(dx * unit * distance, dy * unit * distance, 8.5, False)
                           for distance in (1, 1.5, 2.2, 3)
                           for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1),
                                          (1, 1), (-1, 1), (1, -1), (-1, -1)]]
        placed = False
        for dx, dy, size, inside in candidates:
            rgb = to_rgb(country_color(row, config))
            text_color = 'white' if inside and np.dot(rgb, [.2126, .7152, .0722]) < .61 else ink
            text = ax.text(point.x + dx, point.y + dy, label, fontsize=size, color=text_color,
                           ha='center', va='center', weight='bold' if inside else 'normal',
                           linespacing=1.15, zorder=4)
            bb = text.get_window_extent(renderer).expanded(1.09, 1.09)
            corners = ax.transData.inverted().transform([[bb.x0, bb.y0], [bb.x1, bb.y1]])
            shape_box = box(*corners[0], *corners[1])
            valid = not any(bb.overlaps(other) for other in label_boxes)
            if inside:
                valid = valid and part.contains(shape_box)
            else:
                valid = valid and not all_geometry.intersects(shape_box)
            valid = valid and ax.bbox.contains(bb.x0, bb.y0) and ax.bbox.contains(bb.x1, bb.y1)
            if valid:
                if not inside:
                    ax.annotate('', xy=(point.x, point.y), xytext=(point.x + dx, point.y + dy),
                                arrowprops={'arrowstyle': '-', 'lw': .65, 'color': '#6e8795'}, zorder=2)
                label_boxes.append(bb); placed = True; break
            text.remove()
        if not placed:
            omitted_labels.append(row['country_code'])

    # Bottom panels: ranked quantities, region key, and reading guide.
    fig.add_artist(plt.Line2D([.045, .955], [.222, .222], transform=fig.transFigure, color='#c9dde3', lw=1))
    fig.text(.045, .192, 'LARGEST QUANTITIES', fontsize=10, weight='bold', color=ink)
    for rank, i in enumerate(ranked[:10]):
        col, line = divmod(rank, 5)
        x, y = .045 + col * .225, .165 - line * .025
        row = rows[i]
        fig.text(x, y, f'{rank + 1:02d}', fontsize=8.7, color='#758b97')
        fig.text(x + .021, y, row['name'], fontsize=9, color=ink)
        fig.text(x + .205, y, formatted(row['value'], config), fontsize=9, weight='bold', ha='right', color=ink)
    fig.text(.52, .192, 'CLUSTERS', fontsize=10, weight='bold', color=ink)
    groups = sorted({r['cluster'] for r in rows})
    for j, group in enumerate(groups[:12]):
        col, line = divmod(j, 6)
        x, y = .52 + col * .15, .162 - line * .02
        row = next(r for r in rows if r['cluster'] == group)
        fig.add_artist(Rectangle((x, y), .009, .011, transform=fig.transFigure,
                                 facecolor=config['colors'].get(group, country_color(row, config)), edgecolor='none'))
        fig.text(x + .014, y, group, fontsize=8.2, color=ink)
    fig.text(.79, .192, 'HOW TO READ', fontsize=10, weight='bold', color=ink)
    fig.text(.79, .162, 'Area = quantity\nShapes retain their outlines\nNearby hues = same cluster\nPositions are approximate',
             fontsize=9, color='#4d6576', linespacing=1.65, va='top')
    note = config['source_note'] + f" | {len(rows)} countries / territories shown."
    fig.text(.045, .028, '\n'.join(textwrap.wrap(note, 160)), fontsize=7.8, color='#68808c')
    fig.text(.955, .028, 'Natural Earth boundaries', ha='right', fontsize=7.8, color='#68808c')
    for fmt in formats:
        fig.savefig(stem.with_suffix('.' + fmt), dpi=dpi, facecolor=bg,
                    metadata={'Creator': 'Country Cartogram'} if fmt in ('svg', 'pdf') else None)
    plt.close(fig)
    return omitted_labels


def audit(geometries, rows, config):
    areas = np.array([g.area for g in geometries])
    values = np.array([r['value'] for r in rows])
    target = values / math.fsum(values) * config['total_area']
    area_error = float(np.max(np.abs(areas / target - 1)))
    minimum = math.inf
    overlaps = []
    for i, a in enumerate(geometries):
        if not a.is_valid:
            raise RuntimeError(f"Invalid output geometry: {rows[i]['country_code']}")
        for j in range(i):
            distance = a.distance(geometries[j])
            minimum = min(minimum, distance)
            if a.intersection(geometries[j]).area > 1e-9:
                overlaps.append([rows[i]['country_code'], rows[j]['country_code']])
    if overlaps or area_error > 1e-8:
        raise RuntimeError(f'Geometry audit failed: area error {area_error}, overlaps {overlaps[:5]}')
    gap_required = config['country_gap'] if config.get('layout_mode') == 'growth' else min(config['country_gap'], config['cluster_gap'])
    if minimum < gap_required - 1e-5:
        raise RuntimeError(f'Minimum gap {minimum} below requested {gap_required}')
    return {'max_relative_area_error': area_error,
            'minimum_country_distance': minimum if math.isfinite(minimum) else None,
            'overlap_pairs': overlaps, 'all_geometries_valid': True}


def run(csv_path, *, config_path=None, output=Path('output/cartogram'),
        boundaries=ROOT / 'data/countries.geojson', formats=('png', 'svg', 'pdf'), dpi=180,
        value_column='value', animate=False):
    config = load_config(config_path)
    catalog = load_catalog(boundaries)
    rows, coverage = read_values(csv_path, catalog, value_column)
    if len({r['cluster'] for r in rows}) > 12:
        raise ValueError('The figure template supports at most 12 clusters; split the figure or extend render()')
    geometries, anchors, metadata = [], [], []
    for row in rows:
        target = row['value'] / coverage['sum_of_values'] * config['total_area']
        geom, anchor, detail = prepare_country(catalog[row['country_code']]['geometry'], target,
                 **{k: config[k] for k in ['min_part_fraction', 'max_parts', 'simplify', 'satellite_distance']})
        geometries.append(geom); anchors.append(anchor); metadata.append(detail)
    print(f'Packing {len(rows)} countries in {len({r["cluster"] for r in rows})} clusters...', flush=True)
    frames = []
    if config['layout_mode'] == 'growth':
        placed, positions, packing, frames = layout_progressive(
            geometries, np.array(anchors), rows, metadata, config, capture=animate)
        if not packing['equilibrium_converged']:
            warnings.warn('Settling reached its iteration limit; see equilibrium diagnostics in the audit')
    else:
        placed, positions, packing = layout_countries(geometries, np.array(anchors), rows, config)
    diagnostics = audit(placed, rows, config)
    stem = Path(output)
    stem.parent.mkdir(parents=True, exist_ok=True)
    omitted_labels = render(placed, rows, config, stem, formats, dpi)
    if frames:
        from .evolution import render_evolution
        render_evolution(frames, rows, config, stem)
    diagnostics.update({'coverage': coverage, 'packing': packing, 'omitted_labels': omitted_labels,
                         'config': config, 'coordinate_system': 'arbitrary cartogram chart units; NOT longitude/latitude',
                         'input_sha256': hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
                         'boundaries_sha256': hashlib.sha256(Path(boundaries).read_bytes()).hexdigest(),
                         'versions': {'python': sys.version.split()[0], 'numpy': np.__version__,
                                      'shapely': shapely.__version__, 'matplotlib': matplotlib.__version__}})
    stem.with_suffix('.audit.json').write_text(json.dumps(diagnostics, indent=2) + '\n')
    with stem.with_suffix('.metrics.csv').open('w', newline='') as f:
        fields = ['country_code', 'name', 'cluster', 'parent', 'value', 'share', 'rendered_area',
                  'relative_area_error', 'retained_source_area_fraction', 'retained_part_count']
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row, geom, detail in zip(rows, placed, metadata):
            share = row['value'] / coverage['sum_of_values']
            writer.writerow({**{k: row[k] for k in fields[:5]}, 'share': share, 'rendered_area': geom.area,
                    'relative_area_error': geom.area / (share * config['total_area']) - 1,
                    **{k: detail[k] for k in fields[-2:]}})
    layout = {'coordinate_system': diagnostics['coordinate_system'], 'countries': [
        {**row, 'geometry': mapping(geom), 'position': position.tolist(), 'preprocessing': detail}
        for row, geom, position, detail in zip(rows, placed, positions, metadata)]}
    stem.with_suffix('.layout.json').write_text(json.dumps(layout, separators=(',', ':')) + '\n')
    print(f"Saved {stem}.* | max area error={diagnostics['max_relative_area_error']:.2e} | overlaps=0", flush=True)
    return diagnostics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--out', type=Path, default=Path('output/cartogram'), help='Output filename stem, without extension')
    parser.add_argument('--boundaries', type=Path, default=ROOT / 'data/countries.geojson')
    parser.add_argument('--formats', nargs='+', choices=['png', 'svg', 'pdf'], default=['png', 'svg', 'pdf'])
    parser.add_argument('--dpi', type=int, default=180)
    parser.add_argument('--value-column', default='value')
    parser.add_argument('--animate', action='store_true', help='Also export a GIF and three-stage overview (growth mode)')
    args = parser.parse_args()
    try:
        if args.dpi < 50:
            raise ValueError('dpi must be at least 50')
        run(args.csv, config_path=args.config, output=args.out, boundaries=args.boundaries,
            formats=args.formats, dpi=args.dpi, value_column=args.value_column, animate=args.animate)
    except (ValueError, RuntimeError, KeyError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
