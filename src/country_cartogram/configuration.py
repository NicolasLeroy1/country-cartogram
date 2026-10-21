"""Shared visual and solver configuration, without loading renderers."""
import colorsys
import hashlib
import json
import math
from pathlib import Path
from matplotlib.colors import to_rgb, to_hex

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

