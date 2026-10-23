"""Regenerate example inputs and configurations from the bundled geography."""

import csv
import hashlib
import json
from pathlib import Path

from country_cartogram.configuration import DEFAULTS
from country_cartogram.pipeline import ROOT as PACKAGE_ROOT, load_catalog

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DATA = PACKAGE_ROOT / "data"
YEARS = range(2020, 2023)
LABELS_3D = {
    "area_label": "Population (people)",
    "height_label": "GDP per person (USD)",
    "volume_label": "GDP (USD)",
    "height_scale": None,
}
CONFIGS = {
    "gdp": {
        "title": "GDP by country",
        "subtitle": "Country area represents GDP",
        "source_note": "Natural Earth historical GDP; mixed years. Input in USD millions.",
        "prefix": "$",
        "divisor": 1_000_000,
        "suffix": "T",
        "decimals": 1,
        "label_countries": ["AUS", "BRA", "IND", "ZAF", "NGA"],
    },
    "population": {
        "title": "Population by country",
        "subtitle": "Country area represents population",
        "source_note": "Natural Earth historical population; mixed years.",
        "divisor": 1_000_000,
        "suffix": "M",
        "decimals": 0,
        "label_countries": ["AUS", "BRA", "ZAF"],
    },
}


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def country_fields(row, catalog, included):
    parent = row["parent"] if row["parent"] in included else ""
    return {
        "country_code": row["country_code"],
        "name": row["name"],
        "parent": parent,
        "cluster": catalog[parent]["cluster"] if parent else row["cluster"],
    }


def growth_factor(code, lower, spread, prefix=""):
    seed = int(hashlib.sha256((prefix + code).encode()).hexdigest()[:8], 16) / 2**32
    return lower + spread * seed


def main():
    catalog = load_catalog(DATA / "countries.geojson")
    metadata = ["country_code", "name", "cluster", "parent"]
    populations = []
    for kind, field, year in [
        ("gdp", "GDP_MD", "GDP_YEAR"),
        ("population", "POP_EST", "POP_YEAR"),
    ]:
        included = {
            code for code, row in catalog.items() if row["properties"][field] > 0
        }
        rows = [
            {
                **country_fields(catalog[code], catalog, included),
                "value": catalog[code]["properties"][field],
                "year": catalog[code]["properties"][year],
            }
            for code in sorted(included)
        ]
        write_csv(
            EXAMPLES / f"{kind}.csv",
            ["country_code", "value", *metadata[1:], "year"],
            rows,
        )
        write_json(EXAMPLES / f"{kind}.json", CONFIGS[kind])
        if kind == "population":
            populations = rows

    write_csv(
        DATA / "country_catalog.csv",
        metadata,
        [{key: row[key] for key in metadata} for _, row in sorted(catalog.items())],
    )
    write_csv(
        EXAMPLES / "your_values.csv",
        ["country_code", "value", *metadata[1:]],
        [
            {**country_fields(row, catalog, catalog), "value": ""}
            for _, row in sorted(catalog.items())
        ],
    )
    write_json(ROOT / "config.example.json", DEFAULTS)

    included = {
        code
        for code, row in catalog.items()
        if row["properties"]["POP_EST"] > 0 and row["properties"]["GDP_MD"] >= 0
    }
    paired = [
        {
            **country_fields(catalog[code], catalog, included),
            "population": catalog[code]["properties"]["POP_EST"],
            "gdp_usd": catalog[code]["properties"]["GDP_MD"] * 1_000_000,
            "population_year": catalog[code]["properties"]["POP_YEAR"],
            "gdp_year": catalog[code]["properties"]["GDP_YEAR"],
        }
        for code in sorted(included)
    ]
    write_csv(
        EXAMPLES / "population-gdp.csv",
        [
            "country_code",
            "population",
            "gdp_usd",
            *metadata[1:],
            "population_year",
            "gdp_year",
        ],
        paired,
    )
    paired_config = {
        **CONFIGS["population"],
        "title": "Population and GDP",
        "source_note": "Natural Earth historical population and GDP; mixed years. Missing GDP excluded.",
    }
    write_json(EXAMPLES / "population-gdp.json", paired_config)
    write_json(
        EXAMPLES / "population-gdp.3d.json",
        {
            **LABELS_3D,
            "title": paired_config["title"],
            "source_note": paired_config["source_note"],
        },
    )

    population_series = [
        {
            **{key: row[key] for key in metadata},
            "year": year,
            "value": round(
                row["value"]
                * growth_factor(row["country_code"], 0.99, 0.04) ** (year - 2020),
                3,
            ),
            "source_population_year": row["year"],
        }
        for year in YEARS
        for row in populations
    ]
    write_csv(
        EXAMPLES / "population-timeline.csv",
        ["year", "country_code", "value", *metadata[1:], "source_population_year"],
        population_series,
    )
    write_json(
        EXAMPLES / "population-timeline.json",
        {
            **CONFIGS["population"],
            "title": "Population: synthetic 2020–2022",
            "subtitle": "Fixed area per person",
            "source_note": "Synthetic growth applied to historical Natural Earth population; not observed time-series data.",
        },
    )

    paired_series = [
        {
            **{key: row[key] for key in metadata},
            "year": year,
            "population": round(
                float(row["population"])
                * growth_factor(row["country_code"], 0.99, 0.04) ** (year - 2020),
                3,
            ),
            "gdp_usd": round(
                float(row["gdp_usd"])
                * growth_factor(row["country_code"], 0.96, 0.12, "GDP")
                ** (year - 2020),
                3,
            ),
        }
        for year in YEARS
        for row in paired
    ]
    write_csv(
        EXAMPLES / "population-gdp-timeline.csv",
        ["year", "country_code", "population", "gdp_usd", *metadata[1:]],
        paired_series,
    )
    write_json(
        EXAMPLES / "population-gdp-timeline.3d.json",
        {
            **LABELS_3D,
            "title": "Population and GDP: synthetic 2020–2022",
            "source_note": "Synthetic population/GDP growth from historical Natural Earth attributes; not observed time-series data.",
        },
    )
    print(f"Prepared {len(catalog)} countries and {len(paired)} paired records")


if __name__ == "__main__":
    main()
