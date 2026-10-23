"""Prepare the 1985–2024 GDP showcase from a pinned World Bank API snapshot.

Normal runs are offline. --refresh downloads a new snapshot and records its date.
"""

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from urllib.request import urlopen

from country_cartogram.maps import DEFAULT_BOUNDARIES, load_catalog
from country_cartogram.timeline import fill_missing_periods

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
START, END = 1985, 2024
INDICATOR = "NY.GDP.MKTP.KD"
CODE_ALIASES = {"XKX": "KOS"}  # World Bank -> bundled Natural Earth identifier.
URL = f"https://api.worldbank.org/v2/country/all/indicator/{INDICATOR}?format=json&date={START}:{END}&per_page=20000&source=2"
SNAPSHOT = EXAMPLES / "gdp-1985-2024.source.json.gz"


def prepare(snapshot, catalog):
    """Keep economies with any known GDP and explicitly mark estimated years."""
    header, records = snapshot["response"]
    if header["pages"] != 1 or header["total"] != len(records):
        raise ValueError("The GDP snapshot must contain the complete API response")
    observations = defaultdict(dict)
    for record in records:
        code, year = record["countryiso3code"], int(record["date"])
        code = CODE_ALIASES.get(code, code)
        if code not in catalog:
            continue  # Regional aggregates and economies outside our boundary set.
        if record["indicator"]["id"] != INDICATOR or not START <= year <= END:
            raise ValueError("Unexpected indicator or year in GDP snapshot")
        if year in observations[code]:
            raise ValueError(f"Duplicate observation: {code}, {year}")
        value = record["value"]
        observations[code][year] = (
            value
            if value is not None
            and math.isfinite(value)
            and value > 0
            and record.get("obs_status", "") != "F"
            else None
        )
    years = range(START, END + 1)
    included = sorted(
        code for code in catalog if any(observations[code].get(year) for year in years)
    )
    if not included:
        raise ValueError("No economies have a known positive GDP value")
    periods = {year: [] for year in years}
    for year in years:
        for code in included:
            if not observations[code].get(year):
                continue
            country = catalog[code]
            parent = country["parent"] if country["parent"] in included else ""
            periods[year].append(
                {
                    "id": code,
                    "value": format(observations[code][year], ".15g"),
                    "name": country["name"],
                    "parent": parent,
                    "group": catalog[parent]["group"] if parent else country["group"],
                }
            )
    excluded = [
        {
            "id": code,
            "name": catalog[code]["name"],
            "reason": "No known positive, non-forecast GDP in 1985–2024",
        }
        for code in sorted(set(catalog) - set(included))
    ]
    filled = fill_missing_periods(periods)
    rows = [
        {"year": year, **row} for year, records in filled.items() for row in records
    ]
    return rows, included, excluded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download new World Bank observations; otherwise use the bundled snapshot",
    )
    args = parser.parse_args()
    if args.refresh:
        with urlopen(URL, timeout=60) as response:
            snapshot = {
                "source_url": URL,
                "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                "response": json.load(response),
            }
        SNAPSHOT.write_bytes(
            gzip.compress(
                json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode(),
                mtime=0,
            )
        )
    snapshot = json.loads(gzip.decompress(SNAPSHOT.read_bytes()))
    catalog = load_catalog(DEFAULT_BOUNDARIES)
    rows, included, excluded = prepare(snapshot, catalog)
    csv_path = EXAMPLES / "gdp-1985-2024.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "year",
                "id",
                "value",
                "name",
                "group",
                "parent",
                "value_status",
                "value_note",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    config = {
        "title": "GDP, 1985–2024",
        "subtitle": "Country area shows share of GDP among included economies",
        "value_label": "GDP (constant 2015 US$)",
        "source_note": f"World Bank WDI · {INDICATOR} · constant 2015 US$ · {len(included)} economies on present-day boundaries. Grey values are interpolated or held from the nearest observed year. Economies without any known GDP excluded. CC BY 4.0.",
        "prefix": "$",
        "divisor": 1e12,
        "suffix": "T",
        "decimals": 1,
        "region_gap": 0.2,
        "group_gap": 3.6,
        "label_regions": ["USA", "CHN", "JPN", "DEU", "IND", "BRA"],
    }
    provenance = {
        "indicator": INDICATOR,
        "indicator_name": "GDP (constant 2015 US$)",
        "source": "World Bank, World Development Indicators",
        "source_url": snapshot["source_url"],
        "indicator_url": f"https://data.worldbank.org/indicator/{INDICATOR}",
        "license": "CC BY 4.0",
        "license_url": "https://datacatalog.worldbank.org/public-licenses#cc-by",
        "retrieved_utc": snapshot["retrieved_utc"],
        "source_last_updated": snapshot["response"][0]["lastupdated"],
        "snapshot": SNAPSHOT.name,
        "snapshot_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "period_start": START,
        "period_end": END,
        "period_count": END - START + 1,
        "region_count": len(included),
        "included_region_ids": included,
        "id_aliases": CODE_ALIASES,
        "normalization": "shares of the included cohort's GDP, not total world GDP",
        "boundary_convention": "Present-day bundled Natural Earth boundaries throughout; historical border changes are not simulated.",
        "territorial_breaks": "World Bank country-series definitions are retained. No historical border crosswalk is inferred; filling missing values does not reconcile predecessor/successor national accounts.",
        "transformations": "Preserve positive, non-forecast observations; linearly interpolate interior gaps; hold nearest observed value outside the observed range; assign present-day boundary names, groups and parent links. No historical predecessor/successor totals are added together.",
        "estimated_counts": {
            year: sum(
                row["year"] == year and row["value_status"] != "observed"
                for row in rows
            )
            for year in range(START, END + 1)
        },
        "excluded": excluded,
    }
    for name, data in [
        ("gdp-1985-2024.json", config),
        ("gdp-1985-2024.provenance.json", provenance),
    ]:
        (EXAMPLES / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        )
    print(
        f"Prepared {len(included)} economies × {END - START + 1} years; {len(excluded)} boundary records excluded"
    )


if __name__ == "__main__":
    main()
