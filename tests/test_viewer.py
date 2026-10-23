"""Display payload invariants, temporal encodings and simplified presentation."""

import base64
import gzip
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from shapely.geometry import Point, box, mapping, Polygon, MultiPolygon

from country_cartogram.configuration import load_config
from country_cartogram.extrusion import build_scene, write_html, load_3d_config
from country_cartogram.viewer import write_timeline_viewer, write_document
from country_cartogram.timeline import run_timeline
from country_cartogram.geometry import prepare_region


def decode(path):
    html = Path(path).read_text()
    encoded = re.search(r'id="scene-data"\s*>\s*([^<]+)', html)[1]
    data = json.loads(gzip.decompress(base64.b64decode(encoded)))
    transforms = np.frombuffer(
        base64.b64decode(data["transforms"]), dtype="<f4"
    ).reshape(data["frameCount"], len(data["regions"]), 4)
    return data, transforms


class ViewerTests(unittest.TestCase):
    def test_flat_html_exposes_only_the_first_quantity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flat.html"
            write_document(
                {
                    "mode": "2d",
                    "config": load_3d_config(),
                    "regions": [{"name": "France", "id": "FRA"}],
                    "observations": [[[7, 11, 77]]],
                },
                path,
            )
            html = path.read_text()
            headings = re.search(r"<thead>(.*?)</thead>", html, re.S)[1]
            rows = re.search(r'<tbody id="values">(.*?)</tbody>', html, re.S)[1]
            self.assertEqual(headings.count('<th scope="col">'), 3)
            self.assertEqual(rows.count("<td>"), 2)  # code and first quantity
            self.assertIn("<td>7</td>", rows)
            self.assertNotIn("SECOND ÷ FIRST", html)

    def test_shape_error_is_applied_before_simulation_with_area_and_centroid_preserved(
        self,
    ):
        source = Point(2, 45).buffer(0.4, quad_segs=256)
        exact, anchor, _ = prepare_region(mapping(source), 100, simplify=0)
        simple, simplified_anchor, detail = prepare_region(
            mapping(source), 100, shape_error=0.02
        )
        self.assertLess(
            detail["prepared_outline_vertices"], detail["source_outline_vertices"] / 4
        )
        self.assertLessEqual(detail["max_shape_relative_error"], 0.02)
        self.assertAlmostEqual(simple.area, exact.area, places=8)
        np.testing.assert_allclose(simplified_anchor, anchor)
        self.assertLess(simple.centroid.distance(exact.centroid), 1e-8)
        # Error is evaluated after area correction, so it bounds actual prepared
        # shapes, rather than an intermediate unscaled simplification.
        self.assertLessEqual(simple.symmetric_difference(exact).area / exact.area, 0.02)

    def test_simplification_reduces_vertices_preserves_area_holes_and_islands(self):
        ring = Point(2, 45).buffer(0.4, quad_segs=256)
        mainland = Polygon(
            ring.exterior.coords, holes=[Point(2, 45).buffer(0.05).exterior.coords]
        )
        source = MultiPolygon([mainland, box(3, 45, 3.05, 45.05)])
        simple, _, detail = prepare_region(mapping(source), 100, shape_error=0.02)
        self.assertTrue(simple.is_valid)
        self.assertEqual(len(simple.geoms), 2)
        self.assertEqual(sum(len(p.interiors) for p in simple.geoms), 1)
        self.assertLess(
            detail["prepared_outline_vertices"], detail["source_outline_vertices"] / 4
        )
        self.assertAlmostEqual(simple.area, 100, places=10)
        self.assertLessEqual(detail["max_shape_relative_error"], 0.02)

    def test_static_binary_payload_with_flat_country_and_untrusted_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = {
                "regions": [
                    {
                        "id": c,
                        "name": "</script>@@DATA@@",
                        "group": "A",
                        "value": 4.0,
                        "geometry": mapping(box(x, 0, x + 2, 2)),
                    }
                    for c, x in [("AAA", 0), ("BBB", 4)]
                ]
            }
            layout["regions"][1].update(
                value_status="estimated", value_note="Held from the previous year"
            )
            regions, report = build_scene(layout, {"AAA": (4, 8), "BBB": (4, 0)})
            path = Path(tmp) / "scene.html"
            write_html(regions, report, path)
            data, transforms = decode(path)
            self.assertEqual(data["config"]["opacity"], 0.4)
            self.assertEqual(data["regions"][0]["name"], "</script>@@DATA@@")
            self.assertNotIn("</script>@@DATA@@", path.read_text())
            self.assertEqual(transforms[0, 1, 3], 0)
            self.assertGreater(transforms[0, 0, 3], 0)
            self.assertTrue(data["hasEstimates"])
            self.assertEqual(data["valueStatuses"][""], ["observed", "estimated"])
            self.assertEqual(data["valueNotes"][""][1], "Held from the previous year")
            self.assertIn('<th scope="col">Value status</th>', path.read_text())
            for c in data["regions"]:
                v = np.frombuffer(base64.b64decode(c["vertices"]), dtype="<f4").reshape(
                    -1, 3
                )
                self.assertEqual(v[:, 2].max(), 1)
                self.assertLess(c["displayVolumeError"], 0.02)

    def test_timeline_shares_encoding_and_one_global_height_scale(self):
        base = {
            "regions": [
                {
                    "id": c,
                    "name": c,
                    "group": "A",
                    "value": 1.0,
                    "position": [x, 0],
                    "geometry": mapping(box(x - 1, -1, x + 1, 1)),
                }
                for c, x in [("AAA", 0), ("BBB", 5)]
            ]
        }
        frames = [
            {
                "year": y,
                "from": 2020,
                "phase": phase,
                "progress": p,
                "positions": [[0, 0], [5, 0]],
                "scales": scales,
            }
            for y, p, phase, scales in [
                (2020, 1.0, "settled", [1.0, 1.0]),
                (2021, 0.5, "growth", [np.sqrt(1.25), np.sqrt(0.75)]),
                (2021, 1.0, "settled", [np.sqrt(1.5), np.sqrt(0.5)]),
            ]
        ]
        quantities = {2020: [(1.0, 0.0), (1.0, 10.0)], 2021: [(3.0, 20.0), (1.0, 0.0)]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timeline.html"
            _, report = write_timeline_viewer(
                base,
                frames,
                [0, 2],
                load_config(),
                "shares",
                path,
                quantities=quantities,
                config_3d={"height_scale": 2.0},
                has_volume=True,
            )
            data, transforms = decode(path)
            self.assertEqual(report["geometry_count"], 2)
            for f, values in enumerate(data["observations"]):
                values = np.asarray(values)
                areas = 4 * transforms[f, :, 2] ** 2
                np.testing.assert_allclose(
                    areas / values[:, 0], areas[0] / values[0, 0], rtol=1e-6
                )
                np.testing.assert_allclose(
                    transforms[f, :, 3], 2 * values[:, 2] / values[:, 0], rtol=1e-6
                )
                np.testing.assert_allclose(
                    areas * transforms[f, :, 3],
                    areas[0] / values[0, 0] * 2 * values[:, 2],
                    rtol=1e-6,
                )
            np.testing.assert_allclose(
                data["observations"][-1], [[3, 20 / 3, 20], [1, 0, 0]]
            )

    def test_invalid_second_quantity_fails_before_any_simulation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "series.csv"
            path.write_text("year,id,value,b\n2020,FRA,1,2\n2021,FRA,1,-1\n")
            with patch("country_cartogram.timeline.run") as simulate:
                with self.assertRaises(ValueError):
                    run_timeline(
                        path, output=Path(tmp) / "out", volume_column="b", formats=()
                    )
                simulate.assert_not_called()

    def test_3d_checkpoint_reuses_prepared_shapes_and_previous_quantities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text(
                json.dumps({"total_area": 10, "growth_steps": 4, "labels": 0})
            )
            csv = root / "series.csv"
            csv.write_text("year,id,value,b\n2020,FRA,1,4\n2021,FRA,1.1,5\n")
            first = root / "first"
            run_timeline(
                csv, output=first, config_path=config, volume_column="b", formats=()
            )
            later = root / "later.csv"
            later.write_text("year,id,value,b\n2022,FRA,1.2,6\n")
            with patch(
                "country_cartogram.pipeline.prepare_region",
                side_effect=AssertionError("Reprepared checkpoint"),
            ):
                report = run_timeline(
                    later,
                    output=root / "continued",
                    config_path=config,
                    volume_column="b",
                    formats=(),
                    initial_time=2021,
                    initial_layout=first / "2021.layout.json",
                    initial_values=first / "inputs/2021.csv",
                )
            self.assertEqual(report["viewer"]["geometry_count"], 1)
            data, transforms = decode(root / "continued/timeline.html")
            np.testing.assert_allclose(data["observations"][0], [[1.1, 5 / 1.1, 5]])
            np.testing.assert_allclose(data["observations"][-1], [[1.2, 5.0, 6.0]])
            self.assertEqual(data["frames"][0]["year"], 2021)

    def test_material_config_rejects_invalid_values_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for key, value in [
                ("opacity", 0.05),
                ("opacity", 1.1),
                ("opacity", True),
                ("transmission", 0),
                ("roughness", 1),
                ("surface_noise", -0.1),
                ("render_simplify", 0),
                ("noise_frequency", 0),
            ]:
                path.write_text(json.dumps({key: value}))
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    load_3d_config(path)
