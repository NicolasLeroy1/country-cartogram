import tempfile
import unittest
from pathlib import Path
import numpy as np
from shapely.geometry import box, Polygon, mapping, MultiPolygon
from shapely.affinity import translate
from country_cartogram.geometry import prepare_region
from country_cartogram.pipeline import audit
from country_cartogram.values import read_values
from country_cartogram.configuration import load_config
from country_cartogram.progressive import GrowthShapes
from country_cartogram.physics import layout_physics
from country_cartogram.physics import convex_pieces, contact_envelope
import shapely


class GeometryTests(unittest.TestCase):
    def pack(self, polygons, anchors, gap=0.35):
        rows = [
            {"id": str(i), "group": "One", "parent": "", "value": g.area}
            for i, g in enumerate(polygons)
        ]
        metadata = [
            {
                "scale_factor": 1.0,
                "parts": [{"source_center": [0, 0], "offset": [0, 0]}],
            }
            for g in polygons
        ]
        config = {
            **load_config(),
            "total_area": sum(g.area for g in polygons),
            "region_gap": gap,
            "anchor_pull": 0,
            "group_pull": 0,
            "growth_steps": 4,
        }
        _, positions, report, _ = layout_physics(
            polygons, np.asarray(anchors), rows, metadata, config
        )
        self.assertTrue(report["equilibrium_converged"])
        return positions

    def test_containment_collision_and_required_gap(self):
        # A small body wholly inside a large one defeats naive interval overlap.
        polygons = [box(-5, -5, 5, 5), box(-1, -1, 1, 1), box(-0.5, -0.5, 0.5, 0.5)]
        anchors = np.zeros((3, 2))
        positions = self.pack(polygons, anchors, gap=0.7)
        placed = [translate(g, *p) for g, p in zip(polygons, positions)]
        self.assertTrue(
            all(
                placed[i].distance(placed[j]) >= 0.7 - 1e-6
                for i in range(3)
                for j in range(i)
            )
        )
        np.testing.assert_allclose([g.area for g in placed], [100, 4, 1])

    def test_area_ratio_shape_and_satellite_direction(self):
        main = Polygon([(0, 40), (4, 40), (4, 44), (0, 44)])
        satellite = box(-60, 5, -59, 6)
        source = mapping(MultiPolygon([main, satellite]))
        a, anchor, meta = prepare_region(source, 120, simplify=0, min_part_fraction=0)
        b, _, _ = prepare_region(source, 480, simplify=0, min_part_fraction=0)
        self.assertAlmostEqual(a.area, 120, places=8)
        self.assertAlmostEqual(b.area / a.area, 4, places=8)
        self.assertAlmostEqual(b.geoms[0].length / a.geoms[0].length, 2, places=8)
        direction = np.array(meta["parts"][1]["source_center"]) - np.array(
            meta["parts"][0]["source_center"]
        )
        offset = np.array(meta["parts"][1]["offset"])
        self.assertAlmostEqual(
            float(
                np.dot(
                    direction / np.linalg.norm(direction),
                    offset / np.linalg.norm(offset),
                )
            ),
            1,
        )
        self.assertTrue(a.is_valid)

    def test_holes_and_determinism(self):
        shape_with_hole = Polygon(
            [(0, 0), (8, 0), (8, 8), (0, 8)], [[(2, 2), (2, 3), (3, 3), (3, 2)]]
        )
        g, _, _ = prepare_region(mapping(shape_with_hole), 80, simplify=0)
        self.assertEqual(len(g.geoms[0].interiors), 1)
        a = self.pack([g, g], [[0, 0], [1, 1]])
        b = self.pack([g, g], [[0, 0], [1, 1]])
        np.testing.assert_array_equal(a, b)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "values.csv"
        self.catalog = {
            code: {"name": code, "group": "Example", "parent": ""}
            for code in ("AAA", "BBB")
        }

    def tearDown(self):
        self.directory.cleanup()

    def read(self, content):
        self.path.write_text(content)
        return read_values(self.path, self.catalog)

    def test_invalid_values_and_keys(self):
        for bad in ("", "-1", "nan", "inf", "words"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.read(f"id,value\nAAA,{bad}\n")
        for bad in ("XXX,2", "AAA,1\nAAA,2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.read("id,value\n" + bad + "\n")

    def test_missing_is_distinct_from_zero(self):
        rows, report = self.read("id,value\nAAA,2\n")
        self.assertEqual(report["omitted_region_ids"], ["BBB"])
        self.assertEqual(report["zero_region_ids"], [])
        rows, report = self.read("id,value\nAAA,2\nBBB,0\n")
        self.assertEqual(report["omitted_region_ids"], [])
        self.assertEqual(report["zero_region_ids"], ["BBB"])

    def test_parent_cycle_rejected(self):
        with self.assertRaises(ValueError):
            self.read("id,value,parent\nAAA,2,BBB\nBBB,3,AAA\n")


class GrowthTests(unittest.TestCase):
    def test_contact_padding_contains_round_gap_at_all_growth_scales(self):
        for vertices in [
            np.array([[0, 0], [0.03, 0], [0.01, 0.004]]),
            np.array([[0, 0], [3, 0], [2, 1], [0, 2]]),
        ]:
            support, directions = contact_envelope(vertices)
            for factor in [0.1, 1, 10]:
                for padding in [0.1, 0.6]:
                    envelope = Polygon(support * factor + directions * padding)
                    needed = Polygon(vertices * factor).buffer(padding)
                    self.assertTrue(envelope.exterior.is_ccw)
                    self.assertLess(needed.difference(envelope).area, 1e-10)

    def test_cross_group_contacts_separate_groups_without_widening_internal_gaps(
        self,
    ):
        geometries = [MultiPolygon([box(-0.5, -0.5, 0.5, 0.5)]) for _ in range(3)]
        metadata = [
            {
                "scale_factor": 1.0,
                "parts": [{"source_center": [0, 0], "offset": [0, 0]}],
            }
            for _ in geometries
        ]
        rows = [
            {"id": code, "group": group, "value": 1, "parent": ""}
            for code, group in [("AAA", "West"), ("BBB", "West"), ("CCC", "East")]
        ]
        config = {
            **load_config(),
            "total_area": 3,
            "region_gap": 0.1,
            "group_gap": 0.8,
            "anchor_pull": 0.1,
            "group_pull": 0,
            "growth_steps": 4,
        }
        placed, positions, report, _ = layout_physics(
            geometries, np.array([[0, 0], [0.6, 0], [1.2, 0]]), rows, metadata, config
        )
        checked = audit(placed, rows, config)
        self.assertGreater(report["group_fixture_count"], 0)
        self.assertGreaterEqual(checked["minimum_group_distance"], 0.8 - 1e-5)
        self.assertLess(placed[0].distance(placed[1]), 0.8)
        np.testing.assert_allclose(np.mean(positions, axis=0), [0.6, 0], atol=1e-8)
        # Independent checks must reject a map with a sufficient country gap
        # but too little separation across groups.
        with self.assertRaisesRegex(RuntimeError, "group gap"):
            audit(
                [box(0, 0, 1, 1), box(1.2, 0, 2.2, 1), box(2.4, 0, 3.4, 1)],
                rows,
                config,
            )

    def test_enclave_can_separate_with_coarse_contacts(self):
        # A shrinking host hole can trap a country with concave contact pieces.
        # The conservative component hull must instead permit it to move out.
        host = Polygon(
            [(-5, -5), (5, -5), (5, 5), (-5, 5)], [[(-1, -1), (-1, 1), (1, 1), (1, -1)]]
        )
        shapes = [MultiPolygon([host]), MultiPolygon([box(-0.5, -0.5, 0.5, 0.5)])]
        metadata = [
            {
                "scale_factor": 1.0,
                "parts": [{"source_center": [0.0, 0.0], "offset": [0.0, 0.0]}],
            }
            for _ in shapes
        ]
        rows = [
            {"id": code, "group": "Example", "value": g.area, "parent": ""}
            for code, g in zip(("HOST", "ENCLAVE"), shapes)
        ]
        config = load_config()
        config.update(
            total_area=97, growth_steps=4, relax_per_step=20, settle_steps=1800
        )
        placed, positions, info, _ = layout_physics(
            shapes, np.zeros((2, 2)), rows, metadata, config
        )
        self.assertTrue(info["equilibrium_converged"])
        self.assertGreaterEqual(
            placed[0].distance(placed[1]), config["region_gap"] - 1e-5
        )
        np.testing.assert_allclose([g.area for g in placed], [96, 1], atol=1e-10)
        np.testing.assert_allclose(
            np.average(positions, axis=0, weights=[96, 1]), [0, 0], atol=1e-8
        )

    def test_convex_partition_preserves_concavities_and_holes(self):
        concave = Polygon([(0, 0), (5, 0), (5, 1), (1, 1), (1, 5), (0, 5)])
        with_hole = Polygon(
            [(0, 0), (5, 0), (5, 5), (0, 5)], [[(1, 1), (1, 2), (2, 2), (2, 1)]]
        )
        for polygon in (concave, with_hole):
            restored = shapely.union_all([Polygon(p) for p in convex_pieces(polygon)])
            self.assertLess(restored.symmetric_difference(polygon).area, 1e-9)
        restored = shapely.union_all([Polygon(p) for p in convex_pieces(concave)])
        self.assertFalse(restored.intersects(box(2, 2, 2.5, 2.5)))

    def test_geographic_start_and_exact_end_sizes(self):
        source = mapping(MultiPolygon([box(0, 40, 4, 44), box(-60, 5, -59, 6)]))
        geometry, anchor, detail = prepare_region(
            source, 120, simplify=0, min_part_fraction=0
        )
        shapes = GrowthShapes([geometry], [detail], 120)
        start = shapes.at(0)[0]
        expected_offset = (
            np.array(detail["parts"][1]["source_center"])
            - np.array(detail["parts"][0]["source_center"])
        ) * shapes.map_scale
        actual_offset = np.array(start.geoms[1].centroid.coords[0]) - np.array(
            start.geoms[0].centroid.coords[0]
        )
        np.testing.assert_allclose(actual_offset, expected_offset, atol=1e-9)
        self.assertTrue(shapes.at(1)[0].equals_exact(geometry, 1e-10))

    def test_group_attraction_active_during_growth(self):
        rows = [
            {"id": "AAA", "group": "One"},
            {"id": "BBB", "group": "One"},
        ]
        geometries, anchors, metadata = [], [], []
        for source in [mapping(box(-20, 10, -19, 11)), mapping(box(20, 10, 21, 11))]:
            g, a, m = prepare_region(source, 1, simplify=0)
            geometries.append(g)
            anchors.append(a)
            metadata.append(m)
        config = load_config()
        config.update(
            total_area=2,
            growth_steps=4,
            relax_per_step=1,
            settle_steps=1,
            anchor_pull=0,
            group_pull=0.01,
            region_gap=0.1,
        )
        placed, positions, report, frames = layout_physics(
            geometries, np.array(anchors), rows, metadata, config, capture=True
        )
        initial_distance = np.linalg.norm(
            frames[0]["positions"][1] - frames[0]["positions"][0]
        )
        during_growth = np.linalg.norm(
            frames[2]["positions"][1] - frames[2]["positions"][0]
        )
        self.assertLess(during_growth, initial_distance)
        np.testing.assert_allclose([p.area for p in placed], [1, 1], atol=1e-10)


if __name__ == "__main__":
    unittest.main()
