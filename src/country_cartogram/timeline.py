"""Continue 2D country cartograms through an ordered series of observations."""
from __future__ import annotations

import argparse
import csv
import hashlib
from html import escape
import json
import math
from pathlib import Path

import numpy as np
from shapely.affinity import translate
from shapely.geometry import shape

from .configuration import country_color, load_config
from .geometry import polygon_parts
from .pipeline import ROOT, load_catalog, read_values, run, add_geometry


def read_timeline(path, time_column='year', value_column='value', min_periods=2):
    """Read a long CSV with integer periods and a stable, positive cohort."""
    periods, seen = {}, set()
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {'country_code', time_column, value_column}.issubset(reader.fieldnames):
            raise ValueError(f'Timeline CSV needs country_code, {time_column}, and {value_column}')
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError('Duplicate CSV column names')
        fields = [key for key in reader.fieldnames if key != time_column]
        for line, source in enumerate(reader, 2):
            try:
                year = int(source[time_column])
                value = float(source[value_column])
            except (ValueError, TypeError):
                raise ValueError(f'Line {line}: period must be an integer and quantity must be numeric') from None
            code = (source.get('country_code') or '').strip().upper()
            if not code or (year, code) in seen:
                raise ValueError(f'Line {line}: blank or duplicate country in period {year}')
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'Line {line}: timeline quantities must be finite and positive; use a consistent positive cohort')
            seen.add((year, code))
            periods.setdefault(year, []).append({key: source[key] for key in fields})
    if len(periods) < min_periods:
        raise ValueError('A timeline needs at least two periods; use --initial-layout for a single update')
    cohort = None
    for year, rows in sorted(periods.items()):
        codes = {r['country_code'].strip().upper() for r in rows}
        if cohort is None:
            cohort = codes
        elif codes != cohort:
            raise ValueError(f'Country coverage changes in {year}; use a consistent positive cohort')
    return fields, dict(sorted(periods.items()))


def svg_path(geometry):
    """One reusable local outline per country, including holes and islands."""
    pieces = []
    for part in polygon_parts(geometry):
        for ring in [part.exterior, *part.interiors]:
            points = list(ring.coords)[:-1]
            pieces.append('M' + 'L'.join(f'{x:.10g},{y:.10g}' for x, y in points) + 'Z')
    return ''.join(pieces)


def write_viewer(base, frames, years, endpoints, config, normalization, path):
    countries, local_bounds, base_areas = [], [], []
    for row in base['countries']:
        local = translate(shape(row['geometry']), *-np.asarray(row['position']))
        local_bounds.append(local.bounds)
        base_areas.append(local.area)
        countries.append({'code': row['country_code'], 'name': row['name'], 'cluster': row['cluster'],
                          'value': row['value'], 'color': country_color(row, config), 'path': svg_path(local)})
    local_bounds = np.asarray(local_bounds)
    limits = []
    for frame in frames:
        scale = np.asarray(frame['scales'])[:, None]
        positions = np.asarray(frame['positions'])
        bounds = local_bounds * scale + np.tile(positions, (1, 2))
        limits.append([bounds[:, 0].min(), bounds[:, 1].min(), bounds[:, 2].max(), bounds[:, 3].max()])
    bounds = np.asarray(limits)
    x0, y0 = bounds[:, :2].min(axis=0)
    x1, y1 = bounds[:, 2:].max(axis=0)
    pad = (x1 - x0) * .035
    viewbox = f'{x0-pad:.10g} {-y1-pad:.10g} {x1-x0+2*pad:.10g} {y1-y0+2*pad:.10g}'
    paths = ''.join(f'<path data-index="{i}" fill="{c["color"]}" fill-rule="evenodd" d="{c["path"]}"><title>{escape(c["name"])}</title></path>'
                    for i, c in enumerate(countries))
    groups = {row['cluster']: config['colors'].get(row['cluster'], country_color(row, config)) for row in base['countries']}
    legend = ''.join(f'<span><i style="background:{escape(color)}"></i>{escape(group)}</span>' for group, color in sorted(groups.items()))
    payload = {'countries': [{k: v for k, v in c.items() if k != 'path'} for c in countries],
               'frames': frames, 'years': years, 'endpoints': endpoints, 'normalization': normalization,
               'baseAreas': base_areas, 'totalArea': sum(base_areas),
               'format': {key: config[key] for key in ('divisor', 'prefix', 'suffix', 'decimals')}}
    data = json.dumps(payload, separators=(',', ':'), allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    reading = ('Areas use one fixed value-to-area scale across all periods.' if normalization == 'fixed'
               else 'Each period has the same total land area. Areas represent shares of that period’s total.')
    html = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>''' + escape(config['title']) + ''' — timeline</title><style>
body{margin:0;background:#f5f8fc;color:#18314a;font:16px/1.5 system-ui,sans-serif}main{max-width:1450px;margin:auto;padding:28px 4vw}
h1{font-size:clamp(25px,4vw,38px);margin:0 0 8px;letter-spacing:-.03em}p{color:#536b82}.eyebrow{font-size:12px;letter-spacing:.14em;color:#536b82}
.card{border:1px solid #d9e3ed;border-radius:14px;background:white;padding:18px}svg{width:100%;max-height:68vh;display:block}path{stroke:none}path:hover{filter:brightness(1.15)}
.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}button,select{padding:8px 15px;border:1px solid #b4c6d8;border-radius:7px;background:white;color:#18314a;cursor:pointer}
button.primary{background:#18314a;color:white}input[type=range]{width:100%;accent-color:#287fba;margin:18px 0}#stamp{font-size:23px;font-weight:700;flex:1}#info{min-height:24px;color:#536b82}
.legend{display:flex;justify-content:center;gap:20px;flex-wrap:wrap;font-size:13px;margin:12px 0}.legend i{display:inline-block;width:12px;height:12px;margin-right:6px;border-radius:2px}
footer{font-size:13px;color:#536b82}noscript{display:block;padding:20px;background:#fff4cc}
</style></head><body><main><div class="eyebrow">COUNTRY CARTOGRAM / TIME SERIES</div><h1>''' + escape(config['title']) + '''</h1>
<p>''' + escape(reading) + ''' Each new period continues from the preceding map.</p><div class="card">
<div class="controls"><output id="stamp" aria-live="polite"></output><button id="previous">Previous period</button><button id="play" class="primary">Play</button><button id="next">Next period</button>
<label>Speed <select id="speed"><option value="4">Slow</option><option value="8" selected>Normal</option><option value="16">Fast</option></select></label></div>
<input id="scrub" type="range" min="0" value="0" step="1" aria-label="Timeline frame">
<svg viewBox="''' + viewbox + '''" role="img" aria-label="Country quantities over time"><g transform="scale(1,-1)">''' + paths + '''</g></svg>
<div class="legend">''' + legend + '''</div><div id="info">Hover over a country to inspect its quantity.</div></div>
<p>Use the slider to inspect resizing and settling, or jump directly to a period. The camera and country colors stay fixed.</p>
<footer><p>''' + escape(config['source_note']) + '''</p><p>Intermediate frames are simulation states between observations, not additional observations. All settled maps pass the area and separation checks.</p></footer>
<noscript>Enable JavaScript to use the timeline. Individual period maps are also included as separate files.</noscript></main>
<script id="timeline-data" type="application/json">''' + data + '''</script><script>
'use strict';
const data=JSON.parse(document.getElementById('timeline-data').textContent);
const paths=Array.from(document.querySelectorAll('path[data-index]'));
const slider=document.getElementById('scrub'),stamp=document.getElementById('stamp'),play=document.getElementById('play'),info=document.getElementById('info');
slider.max=data.frames.length-1;
let index=0,playing=false,hovered=null,last=0,request=null;
function details(){
 if(hovered===null){info.textContent='Hover over a country to inspect its quantity.';return;}
 const c=data.countries[hovered],f=data.frames[index],scale=f.scales[hovered];
 let quantity=c.value*scale*scale;
 let label;
 if(data.normalization==='shares'){
   quantity=100*data.baseAreas[hovered]*scale*scale/data.totalArea;
   label=quantity.toLocaleString(undefined,{maximumFractionDigits:3})+'% of total';
 }else{
   label=data.format.prefix+(quantity/data.format.divisor).toLocaleString(undefined,{maximumFractionDigits:data.format.decimals})+data.format.suffix;
 }
 info.textContent=c.name+' ('+c.code+') · '+label+(f.phase==='settled'?'':' · transition');
}
function draw(i){
 index=Math.max(0,Math.min(data.frames.length-1,i));const f=data.frames[index];
 paths.forEach((p,j)=>p.setAttribute('transform','translate('+f.positions[j][0]+','+f.positions[j][1]+') scale('+f.scales[j]+')'));
 stamp.textContent=f.phase==='settled'?String(f.year):f.phase==='settling'?f.year+' · settling':f.from+' → '+f.year+' · '+Math.round(f.progress*100)+'%';
 slider.value=index;details();
}
function stop(){playing=false;play.textContent='Play';if(request!==null)cancelAnimationFrame(request);request=null;}
function tick(now){
 if(!playing)return;
 if(now-last>=1000/Number(document.getElementById('speed').value)){
   last=now;if(index===data.frames.length-1){stop();return;}draw(index+1);
 }request=requestAnimationFrame(tick);
}
play.addEventListener('click',()=>{if(playing){stop();return;}if(index===data.frames.length-1)draw(0);playing=true;last=0;play.textContent='Pause';request=requestAnimationFrame(tick);});
slider.addEventListener('input',()=>{stop();draw(Number(slider.value));});
document.getElementById('previous').addEventListener('click',()=>{stop();const choices=data.endpoints.filter(i=>i<index);draw(choices.length?choices[choices.length-1]:0);});
document.getElementById('next').addEventListener('click',()=>{stop();draw(data.endpoints.find(i=>i>index)??data.frames.length-1);});
paths.forEach((path,i)=>{path.addEventListener('mouseenter',()=>{hovered=i;details();});path.addEventListener('mouseleave',()=>{hovered=null;details();});});
draw(0);
</script></body></html>'''
    Path(path).write_text(html, encoding='utf-8')
    return [float(x0-pad), float(y0-pad), float(x1+pad), float(y1+pad)]


def write_snapshot_gif(layouts, years, config, bounds, path):
    """Compact slideshow of audited period endpoints; HTML has solver frames."""
    import matplotlib.pyplot as plt
    from PIL import Image
    images = []
    for layout, year in zip(layouts, years):
        fig, ax = plt.subplots(figsize=(12, 7), dpi=90, facecolor=config['background'])
        ax.set_facecolor(config['background'])
        for row in layout['countries']:
            add_geometry(ax, shape(row['geometry']), country_color(row, config))
        ax.set(xlim=(bounds[0], bounds[2]), ylim=(bounds[1], bounds[3]), aspect='equal')
        ax.axis('off')
        fig.suptitle(f"{config['title']} · {year}", fontsize=19, color=config['ink'])
        fig.text(.5, .035, 'Each period starts from the preceding settled map', ha='center', color='#536b82')
        fig.canvas.draw()
        images.append(Image.fromarray(np.asarray(fig.canvas.buffer_rgba()).copy()).convert('RGB'))
        plt.close(fig)
    images[0].save(path, save_all=True, append_images=images[1:], duration=1400, loop=0)


def run_timeline(csv_path, *, output=Path('output/timeline'), config_path=None,
                  time_column='year', value_column='value', normalization='fixed',
                  boundaries=ROOT/'data/countries.geojson', formats=('svg', 'png'), gif=False, dpi=160,
                  initial_layout=None, initial_time='Previous map'):
    if normalization not in ('fixed', 'shares'):
        raise ValueError('normalization must be fixed or shares')
    config = load_config(config_path)
    if config['layout_mode'] != 'growth':
        raise ValueError('Timelines require growth mode')
    fields, periods = read_timeline(csv_path, time_column, value_column, min_periods=1 if initial_layout else 2)
    output = Path(output)
    inputs = output / 'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(boundaries)
    totals, normalized = {}, {}
    for year, rows in periods.items():
        with (inputs/f'{year}.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        normalized[year], coverage = read_values(inputs/f'{year}.csv', catalog, value_column)
        totals[year] = coverage['sum_of_values']
    first_rows = next(iter(normalized.values()))
    grouping = [(r['country_code'], r['cluster'], r['parent']) for r in first_rows]
    for year, rows in normalized.items():
        if [(r['country_code'], r['cluster'], r['parent']) for r in rows] != grouping:
            raise ValueError(f'Country groups or parents change in {year}; use a consistent cohort and grouping')
    # Anchor the scale to the first observation. Appending future periods must
    # not retroactively change all earlier maps or their coordinate frame.
    area_scale = config['total_area'] / next(iter(totals.values()))
    frames, endpoints, reports, layouts = [], [], [], []
    base, previous_path, previous_year = None, None, None
    base_areas = None
    display_periods = list(periods)
    if initial_layout:
        base = json.loads(Path(initial_layout).read_text(encoding='utf-8'))
        # Match the pipeline's code ordering even for externally reordered JSON.
        base['countries'].sort(key=lambda r: r['country_code'])
        base_areas = np.array([shape(row['geometry']).area for row in base['countries']])
        values = np.array([row['value'] for row in base['countries']])
        if len(base_areas) != len(first_rows) or {r['country_code'] for r in base['countries']} != {r['country_code'] for r in first_rows}:
            raise ValueError('Checkpoint country coverage differs from the time series')
        if np.any(~np.isfinite(values)) or np.any(values <= 0) or np.any(~np.isfinite(base_areas)) or np.any(base_areas <= 0):
            raise ValueError('Checkpoint quantities and areas must be finite and positive')
        area_scale = float(base_areas.sum() / values.sum())
        if not np.allclose(base_areas / values, area_scale, rtol=1e-8, atol=0):
            raise ValueError('Checkpoint areas are not proportional to its values')
        if normalization == 'shares' and not math.isclose(float(base_areas.sum()), config['total_area'], rel_tol=1e-8):
            raise ValueError('Shares mode requires the checkpoint total area to match total_area')
        frames.append({'year': initial_time, 'from': initial_time, 'phase': 'settled', 'progress': 1.,
                       'positions': [r['position'] for r in base['countries']], 'scales': [1.] * len(base_areas)})
        endpoints.append(0)
        if gif:
            layouts.append(base)
        previous_path, previous_year = Path(initial_layout), initial_time
        display_periods.insert(0, initial_time)
    for year in periods:
        period_config = {**config, 'title': f"{config['title']} · {year}"}
        if normalization == 'fixed':
            period_config['total_area'] = area_scale * totals[year]
        config_file = inputs/f'{year}.json'
        config_file.write_text(json.dumps(period_config, indent=2) + '\n', encoding='utf-8')

        def capture(frame):
            # Store only transforms. Each country coastline is emitted once.
            frames.append({'year': year, 'from': previous_year, 'phase': frame['phase'],
                           'progress': frame['progress'], 'positions': frame['positions'].tolist(),
                           'scales': np.sqrt(frame['areas'] / base_areas).tolist()})

        stem = output/f'{year}'
        report = run(inputs/f'{year}.csv', config_path=config_file, output=stem,
                     value_column=value_column, boundaries=boundaries, formats=formats, dpi=dpi,
                     initial_layout=previous_path, frame_sink=capture if previous_path else None)
        layout_path = stem.with_suffix('.layout.json')
        layout = json.loads(layout_path.read_text(encoding='utf-8'))
        if base is None:
            base = layout
            base_areas = np.array([shape(row['geometry']).area for row in base['countries']])
            frames.append({'year': year, 'from': year, 'phase': 'settled', 'progress': 1.,
                           'positions': [row['position'] for row in layout['countries']],
                           'scales': [1.] * len(base_areas)})
        endpoints.append(len(frames) - 1)
        if gif:
            layouts.append(layout)
        reports.append({'year': year, 'total_value': totals[year],
                        'area_scale': report['config']['total_area'] / totals[year],
                        'layout': layout_path.name, 'packing': report['packing'],
                        'max_relative_area_error': report['max_relative_area_error'],
                        'minimum_country_distance': report['minimum_country_distance'],
                        'overlap_pairs': report['overlap_pairs'],
                        'continuation': report.get('continuation')})
        previous_path, previous_year = layout_path, year
    bounds = write_viewer(base, frames, display_periods, endpoints, config, normalization, output/'timeline.html')
    if gif:
        write_snapshot_gif(layouts, display_periods, config, bounds, output/'timeline.gif')
    manifest = {'schema_version': 1, 'normalization': normalization,
                'fixed_area_scale': area_scale if normalization == 'fixed' else None,
                'input_sha256': hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
                'time_column': time_column, 'value_column': value_column,
                'country_count': len(first_rows), 'frame_count': len(frames),
                'period_count': len(periods), 'periods': reports,
                'initial_layout_sha256': hashlib.sha256(Path(initial_layout).read_bytes()).hexdigest() if initial_layout else None,
                'html_bytes': (output/'timeline.html').stat().st_size,
                'note': 'Intermediate frames are real solver states, not additional observations; endpoint maps are audited.'}
    (output/'timeline.audit.json').write_text(json.dumps(manifest, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(f'Saved {output}/timeline.html | {len(periods)} periods, {len(frames)} solver frames', flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--time-column', default='year')
    parser.add_argument('--value-column', default='value')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--out', type=Path, default=Path('output/timeline'), help='Output directory')
    parser.add_argument('--normalization', choices=['fixed', 'shares'], default='fixed')
    parser.add_argument('--boundaries', type=Path, default=ROOT/'data/countries.geojson')
    parser.add_argument('--formats', nargs='+', choices=['svg', 'png', 'pdf'], default=['svg', 'png'])
    parser.add_argument('--gif', action='store_true', help='Also export a slideshow of audited period endpoints')
    parser.add_argument('--dpi', type=int, default=160)
    parser.add_argument('--initial-layout', type=Path, help='Continue this series from an existing .layout.json')
    parser.add_argument('--initial-time', default='Previous map', help='Label of the checkpoint observation')
    args = parser.parse_args()
    try:
        if args.dpi < 50:
            raise ValueError('dpi must be at least 50')
        run_timeline(args.csv, output=args.out, config_path=args.config,
                     time_column=args.time_column, value_column=args.value_column,
                     normalization=args.normalization, boundaries=args.boundaries,
                     formats=args.formats, gif=args.gif, dpi=args.dpi,
                     initial_layout=args.initial_layout, initial_time=args.initial_time)
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
