"""Disconnected landmasses move independently while retaining regional totals."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from shapely.geometry import MultiPolygon, box, mapping, shape

from country_cartogram.configuration import load_config
from country_cartogram.geometry import polygon_parts
from country_cartogram.physics import layout_physics
from country_cartogram.timeline import run_timeline
from test_viewer import decode


class LandmassTests(unittest.TestCase):
    def test_internal_attraction_is_stronger_than_between_regions(self):
        config = {
            **load_config(),
            "total_area": 2,
            "anchor_pull": 0,
            "growth_steps": 4,
            "relax_per_step": 10,
            "settle_steps": 1,
        }
        self.assertGreater(config["part_pull"], config["group_pull"])
        meta = [
            {
                "scale_factor": 1.0,
                "parts": [
                    {"source_center": [x, 0], "offset": [x, 0], "area": 1}
                    for x in (0, 8)
                ],
            }
        ]
        geometries = [
            MultiPolygon([box(-0.5, -0.5, 0.5, 0.5), box(7.5, -0.5, 8.5, 0.5)])
        ]
        rows = [{"id": "ONE", "group": "A", "parent": "", "value": 2}]
        together = layout_physics(
            geometries, np.array([[0, 0]]), rows, meta, config, capture=True
        )
        apart_rows = [
            {"id": str(i), "group": "A", "parent": "", "value": 1} for i in range(2)
        ]
        apart_meta = [
            {
                "scale_factor": 1,
                "parts": [{"source_center": [0, 0], "offset": [0, 0], "area": 1}],
            }
            for i in range(2)
        ]
        apart = layout_physics(
            [box(-0.5, -0.5, 0.5, 0.5)] * 2,
            np.array([[0, 0], [8, 0]]),
            apart_rows,
            apart_meta,
            config,
            capture=True,
        )
        t = together[3][-1]["part_positions"]
        a = apart[3][-1]["part_positions"]
        self.assertLess(np.linalg.norm(t[1] - t[0]), np.linalg.norm(a[1] - a[0]))
        self.assertEqual(together[2]["body_count"], 2)
        np.testing.assert_allclose(t.mean(axis=0), [4, 0], atol=1e-10)
        self.assertAlmostEqual(together[0][0].area, 2)

    def test_foreign_growth_moves_parts_and_viewer_keeps_one_regional_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boundaries = root / "map.geojson"
            boundaries.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "id": code,
                                "properties": {"group": "One"},
                                "geometry": mapping(geometry),
                            }
                            for code, geometry in [
                                (
                                    "A",
                                    MultiPolygon(
                                        [
                                            box(-0.5, -0.5, 0.5, 0.5),
                                            box(7.5, -0.5, 8.5, 0.5),
                                        ]
                                    ),
                                ),
                                ("B", box(3.5, -0.5, 4.5, 0.5)),
                            ]
                        ],
                    }
                )
            )
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "projection": "local",
                        "total_area": 3,
                        "anchor_pull": 0,
                        "group_pull": 0.001,
                        "part_pull": 0.03,
                        "growth_steps": 12,
                        "labels": 0,
                    }
                )
            )
            values = root / "values.csv"
            values.write_text(
                "year,id,value,b\n2000,A,2,6\n2000,B,1,2\n2001,A,2,6\n2001,B,4,8\n"
            )
            out = root / "out"
            report = run_timeline(
                values,
                boundaries=boundaries,
                config_path=config,
                volume_column="b",
                output=out,
                formats=(),
            )
            layouts = [
                json.loads((out / f"{year}.layout.json").read_text())
                for year in (2000, 2001)
            ]
            positions = [
                np.array(
                    [
                        p.centroid.coords[0]
                        for p in polygon_parts(shape(layout["regions"][0]["geometry"]))
                    ]
                )
                for layout in layouts
            ]
            self.assertGreater(
                abs(
                    np.linalg.norm(positions[1][1] - positions[1][0])
                    - np.linalg.norm(positions[0][1] - positions[0][0])
                ),
                0.2,
            )
            for layout, expected in zip(layouts, ([2, 1], [2, 4])):
                np.testing.assert_allclose(
                    [shape(r["geometry"]).area for r in layout["regions"]],
                    expected,
                    atol=1e-10,
                )
                a = shape(layout["regions"][0]["geometry"])
                self.assertGreaterEqual(a.geoms[0].distance(a.geoms[1]), 0.2 - 1e-5)
            self.assertTrue(
                all(p["packing"]["equilibrium_converged"] for p in report["periods"])
            )
            data, transforms = decode(out / "timeline.html")
            self.assertEqual(len(data["regions"]), 2)
            self.assertEqual(len(data["meshes"]), 3)
            self.assertEqual([m["region"] for m in data["meshes"]], [0, 0, 1])
            np.testing.assert_allclose(data["observations"][-1], [[2, 3, 6], [4, 2, 8]])
            self.assertGreater(
                np.max(
                    np.abs(
                        (transforms[-1, 1, :2] - transforms[-1, 0, :2])
                        - (transforms[0, 1, :2] - transforms[0, 0, :2])
                    )
                ),
                0.2,
            )
            self.assertEqual(
                (out / "timeline.html").read_text().count('<th scope="row">'), 2
            )


if __name__ == "__main__":
    unittest.main()
