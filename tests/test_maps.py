"""Generic maps share projection, preparation, simulation and rendering."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from pyproj import Transformer
import shapely
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape

from country_cartogram.values import read_table
from country_cartogram.maps import DEFAULT_BOUNDARIES, Projection, load_catalog
from country_cartogram.pipeline import run
from country_cartogram.timeline import run_timeline
from country_cartogram.extrusion import export_3d


class MapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.map = self.root / "provinces.geojson"
        self.features = [
            {
                "type": "Feature",
                "id": code,
                "properties": {"name": name, "group": "Province"},
                "geometry": mapping(geometry),
            }
            for code, name, geometry in [
                ("north", "Northern province", box(1000, 1000, 1100, 1100)),
                ("south", "Southern province", box(1000, 800, 1100, 900)),
            ]
        ]
        self.write_map()
        self.config = self.root / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "projection": "local",
                    "total_area": 100,
                    "labels": 0,
                    "growth_steps": 4,
                }
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_map(self):
        self.map.write_text(
            json.dumps({"type": "FeatureCollection", "features": self.features})
        )

    def test_local_coordinates_keep_holes_islands_and_large_offsets(self):
        mainland = Polygon(
            [(1000, 1000), (1200, 1000), (1200, 1300), (1000, 1300)],
            [[(1040, 1040), (1060, 1040), (1060, 1060), (1040, 1060)]],
        )
        geometry = MultiPolygon([mainland, box(9000, -8000, 9100, -7900)])
        projected = Projection(projection="local").geometry(mapping(geometry))
        self.assertTrue(projected.equals(geometry))
        self.assertEqual(sum(len(part.interiors) for part in projected.geoms), 1)

    def test_projection_uses_requested_crs_and_projected_input(self):
        geographic = box(2, 45, 3, 46)
        transformer = Transformer.from_crs(4326, 3035, always_xy=True)
        expected = shapely.transform(
            geographic,
            lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1]))
            / 100_000,
        )
        result = Projection(projection="EPSG:3035").geometry(mapping(geographic))
        self.assertLess(result.symmetric_difference(expected).area, 1e-12)
        meter_coordinates = shapely.transform(expected, lambda xy: xy * 100_000)
        same_crs = Projection(source_crs="EPSG:3035", projection="EPSG:3035").geometry(
            mapping(meter_coordinates)
        )
        self.assertLess(result.symmetric_difference(same_crs).area, 1e-12)

    def test_builtin_adapter_and_default_projection(self):
        catalog = load_catalog(DEFAULT_BOUNDARIES)
        self.assertEqual(len(catalog), 233)
        self.assertEqual(catalog["USA"]["name"], "United States")
        self.assertEqual(catalog["GUM"]["parent"], "USA")
        result = Projection().geometry(catalog["FRA"]["geometry"])
        self.assertTrue(result.is_valid)
        self.assertGreater(result.area, 0)

    def test_generic_ids_and_invalid_boundary_records(self):
        catalog = load_catalog(self.map)
        self.assertEqual(sorted(catalog), ["NORTH", "SOUTH"])
        self.assertEqual(catalog["NORTH"]["group"], "Province")
        original = copy.deepcopy(self.features)
        for change in ("duplicate", "missing", "nonpolygon"):
            self.features = copy.deepcopy(original)
            if change == "duplicate":
                self.features[1]["id"] = "NORTH"
            elif change == "missing":
                del self.features[1]["id"]
            else:
                self.features[1]["geometry"] = {"type": "Point", "coordinates": [0, 0]}
            self.write_map()
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_catalog(self.map)

    def test_single_period_local_map_and_3d_share_saved_shapes(self):
        csv = self.root / "one.csv"
        csv.write_text("year,id,value,wealth\n2000,north,3,30\n2000,south,1,20\n")
        report = run_timeline(
            csv,
            boundaries=self.map,
            config_path=self.config,
            output=self.root / "out",
            formats=("svg",),
        )
        self.assertEqual(report["period_count"], 1)
        self.assertEqual(report["frame_count"], 1)
        layout_path = self.root / "out/2000.layout.json"
        saved = layout_path.read_bytes()
        layout = json.loads(saved)
        self.assertEqual([r["id"] for r in layout["regions"]], ["NORTH", "SOUTH"])
        np.testing.assert_allclose(
            [shape(r["geometry"]).area for r in layout["regions"]], [75, 25], atol=1e-10
        )
        svg = (self.root / "out/2000.svg").read_text()
        self.assertIn("provinces", svg)
        self.assertNotIn("Natural Earth", svg)
        scene = export_3d(layout_path, csv, volume_column="wealth")
        self.assertEqual(scene["region_count"], 2)
        self.assertLess(scene["max_relative_volume_error"], 1e-7)
        self.assertEqual(layout_path.read_bytes(), saved)
        html = (self.root / "out/timeline.html").read_text()
        self.assertIn("Region values", html)

    def test_projection_change_rejects_continuation_before_physics(self):
        csv = self.root / "values.csv"
        csv.write_text("id,value\nnorth,3\nsouth,1\n")
        run(
            csv,
            boundaries=self.map,
            config_path=self.config,
            output=self.root / "first",
            formats=(),
        )
        with patch("country_cartogram.pipeline.layout_physics") as physics:
            with self.assertRaisesRegex(ValueError, "Outline settings changed"):
                run(
                    csv,
                    boundaries=self.map,
                    config_path=self.config,
                    projection="EPSG:3035",
                    initial_layout=self.root / "first.layout.json",
                    output=self.root / "changed",
                    formats=(),
                )
            physics.assert_not_called()

    def test_cli_national_boundaries_with_selected_projection(self):
        for index, feature in enumerate(self.features):
            feature["geometry"] = mapping(box(2, 45 + index * 2, 3, 46 + index * 2))
        self.write_map()
        csv = self.root / "values.csv"
        csv.write_text("id,value\nnorth,3\nsouth,1\n")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "country_cartogram",
                str(csv),
                "--boundaries",
                str(self.map),
                "--projection",
                "EPSG:3035",
                "--formats",
                "svg",
                "--dpi",
                "60",
                "--out",
                str(self.root / "national"),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        audit = json.loads((self.root / "national.audit.json").read_text())
        self.assertEqual(audit["map"]["projection"], "EPSG:3035")
        self.assertEqual(audit["overlap_pairs"], [])

    def test_csv_validation_is_shared_and_invalid_projection_is_early(self):
        csv = self.root / "bad.csv"
        for text in (
            "id,value,value\nNORTH,1,2\n",
            "id,value\nNORTH\n",
            "id,value\n,1\n",
        ):
            csv.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                read_table(csv, {"id", "value"})
        csv.write_text("id,value\nNORTH,1\n")
        with patch("country_cartogram.pipeline.layout_physics") as physics:
            with self.assertRaises(ValueError):
                run(
                    csv,
                    boundaries=self.map,
                    projection="not-a-crs",
                    formats=(),
                    output=self.root / "bad",
                )
            physics.assert_not_called()
        self.assertFalse((self.root / "bad.layout.json").exists())

    def test_numeric_ids_and_more_than_thirty_two_groups(self):
        self.features = [
            {
                "type": "Feature",
                "id": i,
                "properties": {"group": f"Group {i}"},
                "geometry": mapping(box(i * 4, 0, i * 4 + 1, 1)),
            }
            for i in range(40)
        ]
        self.write_map()
        csv = self.root / "many.csv"
        csv.write_text("year,id,value\n" + "".join(f"2000,{i},1\n" for i in range(40)))
        report = run_timeline(
            csv,
            boundaries=self.map,
            config_path=self.config,
            output=self.root / "many",
            formats=(),
        )
        self.assertEqual(report["region_count"], 40)
        self.assertEqual(report["periods"][0]["overlap_pairs"], [])
        self.assertGreaterEqual(report["periods"][0]["minimum_group_distance"], 3.6)


if __name__ == "__main__":
    unittest.main()
