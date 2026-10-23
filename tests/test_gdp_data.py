"""Historical observations stay intact and estimated inputs remain traceable."""

import copy
import csv
import gzip
import hashlib
import json
from pathlib import Path
from runpy import run_path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = run_path(str(ROOT / "scripts/prepare_gdp_timeline.py"))["prepare"]


class GDPDataTests(unittest.TestCase):
    def test_preserves_observations_and_marks_missing_or_forecast_years_as_estimates(
        self,
    ):
        catalog = {
            code: {"name": code, "parent": "", "cluster": "Example"}
            for code in ["AAA", "BBB", "CCC"]
        }
        records = [
            {
                "indicator": {"id": "NY.GDP.MKTP.KD"},
                "countryiso3code": code,
                "date": str(year),
                "value": year * 100.0,
                "obs_status": "",
            }
            for year in range(1985, 2025)
            for code in catalog
        ]
        records[1]["value"] = None
        records[2]["obs_status"] = "F"
        snapshot = {"response": [{"pages": 1, "total": len(records)}, records]}
        rows, included, excluded = PREPARE(snapshot, catalog)
        self.assertEqual(included, ["AAA", "BBB", "CCC"])
        self.assertEqual(len(rows), 120)
        self.assertEqual(float(rows[0]["value"]), 198500)
        self.assertEqual(excluded, [])
        self.assertEqual(
            [r["value_status"] for r in rows[:3]],
            ["observed", "carried_backward", "carried_backward"],
        )
        self.assertEqual(float(rows[1]["value"]), 198600)
        duplicate = copy.deepcopy(snapshot)
        duplicate["response"][1].append(copy.deepcopy(records[0]))
        duplicate["response"][0]["total"] += 1
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            PREPARE(duplicate, catalog)

    def test_bundled_csv_matches_pinned_provenance_and_has_forty_annual_observations(
        self,
    ):
        source = ROOT / "examples/gdp-1985-2024.source.json.gz"
        path = ROOT / "examples/gdp-1985-2024.csv"
        provenance = json.loads(
            (ROOT / "examples/gdp-1985-2024.provenance.json").read_text()
        )
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(),
            provenance["snapshot_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(), provenance["csv_sha256"]
        )
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), provenance["country_count"] * 40)
        for year in range(1985, 2025):
            cohort = [r for r in rows if int(r["year"]) == year]
            self.assertEqual(
                sorted(r["country_code"] for r in cohort),
                provenance["included_country_codes"],
            )
            self.assertTrue(all(float(r["value"]) > 0 for r in cohort))
        snapshot = json.loads(gzip.decompress(source.read_bytes()))
        self.assertEqual(
            snapshot["response"][0]["lastupdated"], provenance["source_last_updated"]
        )


if __name__ == "__main__":
    unittest.main()
