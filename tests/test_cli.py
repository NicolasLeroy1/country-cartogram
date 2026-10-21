"""Exercise the installed package from an unrelated working directory."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstalledPipelineTests(unittest.TestCase):
    def test_command_exports_audited_geometry_and_animation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'values.csv').write_text('country_code,value\nFRA,3\nDEU,5\nESP,2\n')
            (root / 'config.json').write_text(json.dumps({
                'total_area': 100, 'country_gap': .1, 'growth_steps': 12,
                'labels': 0, 'title': 'CLI integration test',
            }))
            result = subprocess.run([
                sys.executable, '-m', 'country_cartogram', 'values.csv',
                '--config', 'config.json', '--out', 'result/map',
                '--formats', 'png', 'svg', 'pdf', '--dpi', '60', '--animate',
            ], cwd=root, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for suffix in ['png', 'svg', 'pdf', 'growth.gif', 'growth.png',
                           'audit.json', 'layout.json', 'metrics.csv']:
                self.assertGreater((root / f'result/map.{suffix}').stat().st_size, 0)
            audit = json.loads((root / 'result/map.audit.json').read_text())
            self.assertEqual(audit['overlap_pairs'], [])
            self.assertLess(audit['max_relative_area_error'], 1e-8)
            self.assertGreaterEqual(audit['minimum_country_distance'], .1 - 1e-5)
            layout = json.loads((root / 'result/map.layout.json').read_text())
            self.assertEqual({r['country_code'] for r in layout['countries']}, {'FRA','DEU','ESP'})

    def test_installed_command_rejects_invalid_quantities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bad.csv').write_text('country_code,value\nFRA,-3\n')
            result = subprocess.run([sys.executable, '-m', 'country_cartogram', 'bad.csv'],
                                    cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2)
            self.assertIn('non-negative', result.stderr)
            self.assertFalse((root / 'output').exists())


if __name__ == '__main__':
    unittest.main()
