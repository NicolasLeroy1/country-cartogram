#!/usr/bin/env python3
"""Re-render a saved layout without rerunning the packing simulation."""
import argparse
import json
from pathlib import Path
import shutil
from shapely.geometry import shape
from .pipeline import DEFAULTS, render

STYLE_KEYS = {'title', 'subtitle', 'source_note', 'prefix', 'divisor', 'suffix', 'decimals',
              'labels', 'label_countries', 'label_offsets', 'label_aliases',
              'background', 'ink', 'colors'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('layout', type=Path)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--dpi', type=int, default=180)
    parser.add_argument('--formats', nargs='+', choices=['png','svg','pdf'], default=['png','svg','pdf'])
    args = parser.parse_args()
    if not args.layout.name.endswith('.layout.json'):
        parser.error('Expected a .layout.json file produced by cartogram.py')
    source_stem = args.layout.with_name(args.layout.name[:-len('.layout.json')])
    audit = json.loads(source_stem.with_suffix('.audit.json').read_text())
    overrides = json.loads(args.config.read_text())
    bad = set(overrides) - STYLE_KEYS
    if bad:
        parser.error(f'Geometry settings require repacking with cartogram.py: {sorted(bad)}')
    config = {**DEFAULTS, **audit['config']}
    for key, value in overrides.items():
        if key == 'colors':
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    if config['divisor'] <= 0 or args.dpi < 50:
        parser.error('divisor must be positive and dpi at least 50')
    rows = json.loads(args.layout.read_text())['countries']
    args.out.parent.mkdir(parents=True, exist_ok=True)
    omitted = render([shape(r['geometry']) for r in rows], rows, config, args.out, args.formats, args.dpi)
    audit['config'] = config
    audit['omitted_labels'] = omitted
    args.out.with_suffix('.audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    for suffix in ('.layout.json', '.metrics.csv'):
        source, target = source_stem.with_suffix(suffix), args.out.with_suffix(suffix)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    print(f'Restyled {args.out}.*; omitted labels: {omitted}')


if __name__ == '__main__':
    main()
