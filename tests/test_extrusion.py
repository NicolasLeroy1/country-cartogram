"""Numerical invariants of bivariate extrusion and installed CLI behavior."""

import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from shapely.geometry import Polygon, MultiPolygon, box, mapping
from shapely import union_all

from country_cartogram.values import read_quantity_pairs
from country_cartogram.extrusion import (
    audit_mesh,
    build_scene,
    extrude_polygon,
    load_3d_config,
)


def example_layout():
    # Both footprints have the same scale: two square units per person.
    return {
        "regions": [
            {
                "id": "AAA",
                "name": "First",
                "group": "Group",
                "value": 2,
                "geometry": mapping(box(0, 0, 2, 2)),
            },
            {
                "id": "BBB",
                "name": "Second",
                "group": "Group",
                "value": 4,
                "geometry": mapping(box(4, 0, 8, 2)),
            },
        ]
    }


class ExtrusionTests(unittest.TestCase):
    def test_mesh_preserves_concavity_holes_and_islands(self):
        mainland = Polygon(
            [(0, 0), (6, 0), (6, 2), (3, 2), (3, 6), (0, 6)],
            holes=[[(0.5, 0.5), (0.5, 1.5), (1.5, 1.5), (1.5, 0.5)]],
        )
        geom = MultiPolygon([mainland, box(10, 10, 11, 11)])
        vertices, faces, roofs = extrude_polygon(geom, 7)
        roof_polygons = [Polygon(vertices[face, :2]) for face in roofs]
        self.assertLess(union_all(roof_polygons).symmetric_difference(geom).area, 1e-10)
        self.assertTrue(all(geom.covers(p) for p in roof_polygons))
        report = audit_mesh(vertices, faces, roofs, geom, 7)
        self.assertTrue(report["closed_mesh"])
        self.assertAlmostEqual(report["mesh_volume"], geom.area * 7)

    def test_area_height_volume_relationship_and_common_scale(self):
        pairs = {"AAA": (2, 40), "BBB": (4, 40)}
        regions, report = build_scene(
            example_layout(), pairs, config={"height_scale": 0.5}
        )
        a, b = regions
        self.assertAlmostEqual(a["footprint_area"], b["footprint_area"] / 2)
        self.assertAlmostEqual(a["height"], b["height"] * 2)
        self.assertAlmostEqual(a["mesh_volume"], b["mesh_volume"])
        self.assertAlmostEqual(report["volume_scale"], 1)
        # A uniform change of height scale multiplies every volume equally.
        doubled, _ = build_scene(example_layout(), pairs, config={"height_scale": 1})
        for before, after in zip(regions, doubled):
            self.assertAlmostEqual(after["mesh_volume"], 2 * before["mesh_volume"])
            np.testing.assert_array_equal(
                before["vertices"][:, :2], after["vertices"][:, :2]
            )

    def test_zero_second_quantity_is_a_flat_roof(self):
        regions, report = build_scene(example_layout(), {"AAA": (2, 0), "BBB": (4, 0)})
        self.assertEqual(report["max_height"], 0)
        for c in regions:
            self.assertEqual(c["mesh_volume"], 0)
            self.assertEqual(c["height"], 0)
            self.assertTrue(np.all(c["vertices"][:, 2] == 0))
        self.assertEqual(report["zero_height_region_ids"], ["AAA", "BBB"])

    def test_saved_layout_requires_matching_denominator_and_coverage(self):
        for pairs in (
            {"AAA": (3, 40), "BBB": (4, 40)},
            {"AAA": (2, 40)},
            {"AAA": (2, 40), "BBB": (4, 40), "CCC": (1, 20)},
        ):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                build_scene(example_layout(), pairs)
        layout = example_layout()
        layout["regions"][1]["geometry"] = mapping(box(4, 0, 8, 3))
        with self.assertRaisesRegex(ValueError, "not proportional"):
            build_scene(layout, {"AAA": (2, 40), "BBB": (4, 40)})

    def test_input_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            for data in (
                "AAA,0,1",
                "AAA,2,-1",
                "AAA,2,nan",
                "AAA,2,inf",
                "AAA,2,",
                "AAA,2,1\nAAA,2,3",
                "AAA,1e-300,1e300",
            ):
                path.write_text("id,population,gdp\n" + data + "\n")
                with self.subTest(data=data), self.assertRaises(ValueError):
                    read_quantity_pairs(path, "population", "gdp")
            path.write_text("id,population,gdp\nAAA,2,0\nBBB,0,0\n")
            self.assertEqual(
                read_quantity_pairs(path, "population", "gdp"),
                {"AAA": (2, 0), "BBB": (0, 0)},
            )
            with self.assertRaises(ValueError):
                read_quantity_pairs(path, "population", "population")
            for scale in (0, -1, float("inf"), True):
                path.write_text(json.dumps({"height_scale": scale}))
                with self.assertRaises(ValueError):
                    load_3d_config(path)

    def test_pipeline_volume_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "values.csv").write_text(
                "id,population,gdp\nFRA,3,30\nDEU,5,100\nESP,2,8\n"
            )
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "total_area": 100,
                        "region_gap": 0.1,
                        "growth_steps": 12,
                        "labels": 3,
                    }
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "country_cartogram",
                    "values.csv",
                    "--value-column",
                    "population",
                    "--volume-column",
                    "gdp",
                    "--config",
                    "config.json",
                    "--out",
                    "result/map",
                    "--formats",
                    "svg",
                    "--html-js",
                    "cdn",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            audit = json.loads((root / "result/map.audit.json").read_text())
            self.assertEqual(audit["overlap_pairs"], [])
            self.assertLess(audit["extrusion"]["max_relative_volume_error"], 1e-7)
            self.assertTrue((root / "result/map.3d.html").exists())

    def test_installed_export_command_and_self_contained_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = example_layout()
            layout["regions"][0]["name"] = "</script><script>alert(1)</script>"
            (root / "map.layout.json").write_text(json.dumps(layout))
            (root / "values.csv").write_text("id,population,gdp\nAAA,2,40\nBBB,4,40\n")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "country_cartogram.extrusion",
                    "map.layout.json",
                    "values.csv",
                    "--area-column",
                    "population",
                    "--volume-column",
                    "gdp",
                    "--out",
                    "result/map",
                    "--preview",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for suffix in ("html", "png", "metrics.csv", "audit.json"):
                self.assertGreater((root / f"result/map.3d.{suffix}").stat().st_size, 0)
            html = (root / "result/map.3d.html").read_text()
            self.assertIn("CartogramThree", html)
            self.assertNotIn("plotly", html)
            self.assertNotIn("<script src=", html)
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("Top view", html)
            audit = json.loads((root / "result/map.3d.audit.json").read_text())
            self.assertLess(audit["max_relative_volume_error"], 1e-7)
            with (root / "result/map.3d.metrics.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertAlmostEqual(
                float(rows[0]["mesh_volume"]), float(rows[1]["mesh_volume"])
            )


if __name__ == "__main__":
    unittest.main()
