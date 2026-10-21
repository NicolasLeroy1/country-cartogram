import csv
import tempfile
import unittest
from pathlib import Path
import numpy as np
from shapely.geometry import box, Polygon, mapping, MultiPolygon
from shapely.affinity import translate
from country_cartogram.geometry import prepare_country, pack_rigid
from country_cartogram.pipeline import read_values, audit, load_config
from country_cartogram.progressive import GrowthShapes, layout_progressive
from country_cartogram.physics import convex_pieces
import shapely


class GeometryTests(unittest.TestCase):
    def test_containment_collision_and_required_gap(self):
        # A small body wholly inside a large one defeats naive interval overlap.
        polygons = [box(-5, -5, 5, 5), box(-1, -1, 1, 1), box(-.5, -.5, .5, .5)]
        anchors = np.zeros((3, 2))
        positions, _ = pack_rigid(polygons, anchors, gap=.7, iterations=30)
        placed = [translate(g, *p) for g, p in zip(polygons, positions)]
        self.assertTrue(all(placed[i].distance(placed[j]) >= .7 - 1e-6
                            for i in range(3) for j in range(i)))
        np.testing.assert_allclose([g.area for g in placed], [100, 4, 1])

    def test_area_ratio_shape_and_satellite_direction(self):
        main = Polygon([(0, 40), (4, 40), (4, 44), (0, 44)])
        satellite = box(-60, 5, -59, 6)
        source = mapping(MultiPolygon([main, satellite]))
        a, anchor, meta = prepare_country(source, 120, simplify=0, min_part_fraction=0)
        b, _, _ = prepare_country(source, 480, simplify=0, min_part_fraction=0)
        self.assertAlmostEqual(a.area, 120, places=8)
        self.assertAlmostEqual(b.area / a.area, 4, places=8)
        self.assertAlmostEqual(b.geoms[0].length / a.geoms[0].length, 2, places=8)
        direction = np.array(meta['parts'][1]['source_center']) - np.array(meta['parts'][0]['source_center'])
        offset = np.array(meta['parts'][1]['offset'])
        self.assertAlmostEqual(float(np.dot(direction / np.linalg.norm(direction), offset / np.linalg.norm(offset))), 1)
        self.assertTrue(a.is_valid)

    def test_holes_and_determinism(self):
        shape_with_hole = Polygon([(0, 0), (8, 0), (8, 8), (0, 8)],
                                  [[(2, 2), (2, 3), (3, 3), (3, 2)]])
        g, _, _ = prepare_country(mapping(shape_with_hole), 80, simplify=0)
        self.assertEqual(len(g.geoms[0].interiors), 1)
        a, _ = pack_rigid([g, g], [[0, 0], [1, 1]], iterations=20)
        b, _ = pack_rigid([g, g], [[0, 0], [1, 1]], iterations=20)
        np.testing.assert_array_equal(a, b)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'values.csv'
        self.catalog = {code: {'name': code, 'cluster': 'Example', 'parent': ''} for code in ('AAA','BBB')}

    def tearDown(self):
        self.directory.cleanup()

    def read(self, content):
        self.path.write_text(content)
        return read_values(self.path, self.catalog)

    def test_invalid_values_and_keys(self):
        for bad in ('', '-1', 'nan', 'inf', 'words'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.read(f'country_code,value\nAAA,{bad}\n')
        for bad in ('XXX,2', 'AAA,1\nAAA,2'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.read('country_code,value\n' + bad + '\n')

    def test_missing_is_distinct_from_zero(self):
        rows, report = self.read('country_code,value\nAAA,2\n')
        self.assertEqual(report['omitted_country_codes'], ['BBB'])
        self.assertEqual(report['zero_country_codes'], [])
        rows, report = self.read('country_code,value\nAAA,2\nBBB,0\n')
        self.assertEqual(report['omitted_country_codes'], [])
        self.assertEqual(report['zero_country_codes'], ['BBB'])

    def test_parent_cycle_rejected(self):
        with self.assertRaises(ValueError):
            self.read('country_code,value,parent\nAAA,2,BBB\nBBB,3,AAA\n')


class GrowthTests(unittest.TestCase):
    def test_enclave_can_separate_with_coarse_contacts(self):
        # A shrinking host hole can trap a country with concave contact pieces.
        # The conservative component hull must instead permit it to move out.
        host = Polygon([(-5,-5), (5,-5), (5,5), (-5,5)],
                       [[(-1,-1), (-1,1), (1,1), (1,-1)]])
        shapes = [MultiPolygon([host]), MultiPolygon([box(-.5,-.5,.5,.5)])]
        metadata = [{'scale_factor': 1., 'parts': [{'source_center': [0.,0.], 'offset': [0.,0.]}]}
                    for _ in shapes]
        rows = [{'country_code': code, 'cluster': 'Example', 'value': g.area, 'parent': ''}
                for code, g in zip(('HOST', 'ENCLAVE'), shapes)]
        config = load_config()
        config.update(total_area=97, growth_steps=4, relax_per_step=20, settle_steps=1800)
        placed, positions, info, _ = layout_progressive(shapes, np.zeros((2,2)), rows, metadata, config)
        self.assertTrue(info['equilibrium_converged'])
        self.assertGreaterEqual(placed[0].distance(placed[1]), config['country_gap'] - 1e-5)
        np.testing.assert_allclose([g.area for g in placed], [96,1], atol=1e-10)
        np.testing.assert_allclose(np.average(positions, axis=0, weights=[96,1]), [0,0], atol=1e-8)

    def test_convex_partition_preserves_concavities_and_holes(self):
        concave = Polygon([(0, 0), (5, 0), (5, 1), (1, 1), (1, 5), (0, 5)])
        with_hole = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)],
                            [[(1, 1), (1, 2), (2, 2), (2, 1)]])
        for polygon in (concave, with_hole):
            restored = shapely.union_all([Polygon(p) for p in convex_pieces(polygon)])
            self.assertLess(restored.symmetric_difference(polygon).area, 1e-9)
        restored = shapely.union_all([Polygon(p) for p in convex_pieces(concave)])
        self.assertFalse(restored.intersects(box(2, 2, 2.5, 2.5)))

    def test_geographic_start_and_exact_end_sizes(self):
        source = mapping(MultiPolygon([box(0, 40, 4, 44), box(-60, 5, -59, 6)]))
        geometry, anchor, detail = prepare_country(source, 120, simplify=0, min_part_fraction=0)
        shapes = GrowthShapes([geometry], [detail], 120)
        start = shapes.at(0)[0]
        expected_offset = (np.array(detail['parts'][1]['source_center']) -
                           np.array(detail['parts'][0]['source_center'])) * shapes.geographic_scale
        actual_offset = np.array(start.geoms[1].centroid.coords[0]) - np.array(start.geoms[0].centroid.coords[0])
        np.testing.assert_allclose(actual_offset, expected_offset, atol=1e-9)
        self.assertTrue(shapes.at(1)[0].equals_exact(geometry, 1e-10))

    def test_cluster_attraction_active_during_growth(self):
        rows = [{'country_code': 'AAA', 'cluster': 'One'}, {'country_code': 'BBB', 'cluster': 'One'}]
        geometries, anchors, metadata = [], [], []
        for source in [mapping(box(-20, 10, -19, 11)), mapping(box(20, 10, 21, 11))]:
            g, a, m = prepare_country(source, 1, simplify=0)
            geometries.append(g); anchors.append(a); metadata.append(m)
        config = load_config()
        config.update(total_area=2, growth_steps=4, relax_per_step=1, settle_steps=1,
                      geographic_pull=0, cluster_pull=.01, country_gap=.1)
        placed, positions, report, frames = layout_progressive(geometries, np.array(anchors), rows,
                                                               metadata, config, capture=True)
        initial_distance = np.linalg.norm(frames[0]['positions'][1] - frames[0]['positions'][0])
        during_growth = np.linalg.norm(frames[2]['positions'][1] - frames[2]['positions'][0])
        self.assertLess(during_growth, initial_distance)
        np.testing.assert_allclose([p.area for p in placed], [1, 1], atol=1e-10)


if __name__ == '__main__':
    unittest.main()
