"""Temporal continuity, stable anchors, and time-series normalization."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from shapely.geometry import MultiPolygon, box, mapping, shape
from shapely.affinity import translate

from country_cartogram.configuration import load_config
from country_cartogram.continuation import GEOMETRY_KEYS, prepare_continuation
from country_cartogram.physics import layout_physics
from country_cartogram.timeline import read_timeline


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.config.update(total_area=8, country_gap=.1, geographic_pull=0, cluster_pull=0)
        self.rows = [{'country_code': c, 'name': c, 'cluster': 'Example', 'parent': '', 'value': v}
                     for c, v in [('AAA', 4.1), ('BBB', 3.9)]]
        self.old = {'countries': [], 'simulation_state': {
            'geographic_scale': 1., 'geographic_anchors': {'AAA': [0, 0], 'BBB': [10, 0]},
            'geometry_config': {k: self.config[k] for k in GEOMETRY_KEYS}, 'boundaries_sha256': 'test'}}
        for code, position in [('AAA', [100, 100]), ('BBB', [120, 100])]:
            # Main island plus satellite. Starting positions deliberately differ
            # from geographic anchors to catch accidental reinitialization.
            parts = [box(-1, -1, 1, .5), box(3, 0, 4, 1)]
            geometry = translate(MultiPolygon(parts), *position)
            self.old['countries'].append({'country_code': code, 'name': code, 'cluster': 'Example',
                'parent': '', 'value': 4., 'position': position, 'geometry': mapping(geometry),
                'preprocessing': {'scale_factor': 1., 'retained_source_area_fraction': 1.,
                                  'retained_part_count': 2, 'parts': [
                    {'source_center': [0, 0], 'offset': [0, 0], 'area': 3.},
                    {'source_center': [80, 30], 'offset': [3.5, .5], 'area': 1.}]}})
        self.path = self.root/'previous.layout.json'
        self.path.write_text(json.dumps(self.old))

    def tearDown(self):
        self.temp.cleanup()

    def test_starts_at_previous_shapes_and_positions_and_adapts_growth(self):
        geometry, anchors, metadata, initial = prepare_continuation(self.path, self.rows, self.config, 'test')
        compact_frames = []
        placed, positions, report, frames = layout_physics(
            geometry, anchors, self.rows, metadata, self.config,
            initial_state=initial, capture=True, frame_sink=compact_frames.append)
        self.assertEqual(report['method'], 'continued_growth_rigid_body_physics')
        self.assertEqual(report['growth_steps'], 4)
        np.testing.assert_array_equal(frames[0]['positions'], [[100, 100], [120, 100]])
        for prior, first in zip(self.old['countries'], frames[0]['geometries']):
            self.assertLess(shape(prior['geometry']).symmetric_difference(first).area, 1e-10)
        np.testing.assert_allclose([g.area for g in placed], [4.1, 3.9], atol=1e-10)
        np.testing.assert_array_equal(positions, [[100, 100], [120, 100]])
        self.assertNotIn('geometries', compact_frames[0])
        np.testing.assert_allclose(compact_frames[0]['areas'], [4, 4])
        np.testing.assert_array_equal(initial['anchors'], [[0, 0], [10, 0]])

    def test_rejects_incompatible_coverage_geometry_and_groups(self):
        bad_config = {**self.config, 'simplify': .1}
        bad_rows = copy.deepcopy(self.rows); bad_rows[0]['cluster'] = 'Different'
        for rows, config, sha in ((self.rows[:1], self.config, 'test'),
                                  (self.rows, bad_config, 'test'),
                                  (bad_rows, self.config, 'test'),
                                  (self.rows, self.config, 'changed')):
            with self.subTest(rows=rows, sha=sha), self.assertRaises(ValueError):
                prepare_continuation(self.path, rows, config, sha)

    def test_timeline_validates_before_simulation(self):
        path = self.root/'series.csv'
        for content in ('2020,AAA,1\n2021,BBB,2', '2020,AAA,0\n2021,AAA,2',
                        '2020,AAA,1\n2020,AAA,2\n2021,AAA,3', '2020,AAA,nan\n2021,AAA,2'):
            path.write_text('year,country_code,value\n' + content + '\n')
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_timeline(path)

    def test_shares_mode_keeps_total_area_and_resizes_constant_values(self):
        from country_cartogram.timeline import run_timeline
        path = self.root/'shares.csv'
        path.write_text('year,country_code,value\n2020,FRA,3\n2020,DEU,5\n2020,ESP,2\n'
                        '2021,FRA,3\n2021,DEU,7\n2021,ESP,2\n')
        cfg = self.root/'shares.json'
        cfg.write_text(json.dumps({'total_area': 100, 'country_gap': .1, 'growth_steps': 12, 'labels': 0}))
        report = run_timeline(path, output=self.root/'shares', config_path=cfg,
                              normalization='shares', formats=())
        self.assertIsNone(report['fixed_area_scale'])
        areas = []
        for year in (2020, 2021):
            layout = json.loads((self.root/f'shares/{year}.layout.json').read_text())
            values = {r['country_code']: shape(r['geometry']).area for r in layout['countries']}
            self.assertAlmostEqual(sum(values.values()), 100)
            areas.append(values['FRA'])
        np.testing.assert_allclose(areas, [30, 25], atol=1e-8)

    def test_installed_timeline_preserves_absolute_scale_and_chains_states(self):
        (self.root/'series.csv').write_text(
            'year,country_code,value\n2020,FRA,3\n2020,DEU,5\n2020,ESP,2\n'
            '2021,FRA,3.1\n2021,DEU,5.1\n2021,ESP,2.1\n'
            '2022,FRA,3.2\n2022,DEU,5.2\n2022,ESP,2.2\n')
        (self.root/'config.json').write_text(json.dumps({'total_area': 100, 'country_gap': .1,
                                                        'growth_steps': 12, 'labels': 0}))
        result = subprocess.run([
            sys.executable, '-m', 'country_cartogram.timeline', 'series.csv', '--config', 'config.json',
            '--out', 'result', '--formats', 'svg', '--gif',
        ], cwd=self.root, text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        root = self.root/'result'
        manifest = json.loads((root/'timeline.audit.json').read_text())
        self.assertEqual(manifest['period_count'], 3)
        self.assertGreater(manifest['frame_count'], 3)
        np.testing.assert_allclose([p['area_scale'] for p in manifest['periods']], [10]*3)
        original = json.loads((root/'2020.layout.json').read_text())
        for year in (2021, 2022):
            layout = json.loads((root/f'{year}.layout.json').read_text())
            self.assertEqual(layout['simulation_state'], original['simulation_state'])
            audit = json.loads((root/f'{year}.audit.json').read_text())
            self.assertEqual(audit['continuation']['reused_country_count'], 3)
            self.assertEqual(audit['overlap_pairs'], [])
            self.assertLess(audit['max_relative_area_error'], 1e-8)
        html = (root/'timeline.html').read_text()
        self.assertEqual(html.count('<path data-index='), 3)
        self.assertNotIn('<script src=', html)
        self.assertIn('requestAnimationFrame', html)
        self.assertGreater((root/'timeline.gif').stat().st_size, 0)
        # A separate invocation must continue from the last checkpoint without
        # normalizing everything again or restarting its geographic simulation.
        (self.root/'later.csv').write_text('year,country_code,value\n2023,FRA,3.3\n2023,DEU,5.3\n2023,ESP,2.3\n')
        resumed = subprocess.run([
            sys.executable, '-m', 'country_cartogram.timeline', 'later.csv', '--config', 'config.json',
            '--out', 'resumed', '--formats', 'svg', '--initial-layout', 'result/2022.layout.json',
            '--initial-time', '2022',
        ], cwd=self.root, text=True, capture_output=True, timeout=120)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        audit = json.loads((self.root/'resumed/timeline.audit.json').read_text())
        self.assertAlmostEqual(audit['fixed_area_scale'], 10)
        self.assertIsNotNone(audit['initial_layout_sha256'])
        self.assertEqual(audit['periods'][0]['packing']['method'], 'continued_growth_rigid_body_physics')


if __name__ == '__main__':
    unittest.main()
