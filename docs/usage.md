# Usage and reuse

A reusable Python pipeline for separated, shape-preserving country cartograms.
Change the country quantities in a CSV, choose groups and a style, then export
PNG, editable SVG, vector PDF, coordinates, and numerical validation results.

See [the project overview](../README.md) for a quick start and [algorithm notes](algorithm.md) for the design rationale.

## Supply your quantities

Start with `examples/your_values.csv`. Fill the rows you need and delete the rest.
Required columns are `country_code` and `value`:

```csv
country_code,value
USA,29200
CHN,18700
DEU,4700
FRA,3200
BRA,2200
AUS,1700
```

These values illustrate the input format. Codes are predominantly ISO alpha-3; the exact
accepted identifiers and names are in `../src/country_cartogram/data/country_catalog.csv`. Natural Earth
also uses special identifiers such as `KOS`, `CYN`, `SOL`, `KAS`, `IOA`, and `ATC`.

Optional columns:

| Column | Meaning |
| --- | --- |
| `name` | Display name override |
| `cluster` | Continent or any grouping you choose |
| `parent` | Another included country code; brings a territory near its parent |
| `year` | Provenance carried in the input; does not affect geometry |
| `value_status`, `value_note` | Observed/estimated status and method; see [time series](time-series.md#missing-values-and-changing-countries) |

The supplied figure template supports up to 12 clusters; the default uses six.

If `parent` is absent, the Natural Earth sovereign relationship is the default.
A blank `parent` cell explicitly disables that relationship. A parent and child
must be in the same cluster. Parent chains and cycles are rejected. If a parent
has no positive input quantity, its link is disabled with a warning. Units that
are already components of the parent's boundary (for example French Guiana
within France in this dataset) are handled as polygon parts, not duplicate rows.

Negative, nonfinite, duplicate, unknown-code, and blank values are rejected.
Zeros have zero area and are omitted. Missing rows are omitted without being
treated as zero. Both lists appear separately in the audit. No arbitrary
minimum country size is imposed: extremely small quantities may be invisible
in raster exports. The area principle is preserved.

```bash
country-cartogram my_values.csv --config my_style.json --out output/my_map
# Use another numeric column in the same file:
country-cartogram my_values.csv --value-column publications --out output/publications
# Choose exports or increase resolution:
country-cartogram my_values.csv --formats svg pdf png --dpi 300 --out output/print_map
```

`--out` takes a path stem without an extension. Runs overwrite outputs with the
same stem, so choose a different stem for different datasets or versions.

## Geometry

The default growth solver resizes countries gradually from geographic positions,
with group attraction and local collision forces. Rotation is locked. Final
polygons are checked for area, overlap, and minimum gap. See the
[algorithm guide](algorithm.md) for equations and collision modes.

`--animate` writes captured solver states as `.growth.gif` and a three-stage
`.growth.png`. `layout_mode: "legacy"` retains the older two-stage packer for
comparison; its settings are marked below.

## Reuse a saved layout

Treat the output stem as a small bundle: keep `map.layout.json`,
`map.audit.json`, and `map.metrics.csv` together. The layout holds positioned
polygons and checkpoint state; the audit holds the configuration and checks.
Figures can be regenerated from this bundle.

To change typography, labels, titles, or colors without rerunning the simulation:

```bash
country-cartogram-restyle output/gdp.layout.json --config my_style.json --out output/gdp_restyled
```

The override file accepts visual settings only. Force, gap, geometry, or grouping
changes require a new layout. Restyled bundles retain the same geometry and
checkpoint state. An output stem replaces the files at that stem, so use a new
name to keep earlier versions.

For new quantities, continue with `--initial-layout` or use the
[timeline command](time-series.md) to manage scaling across periods. Changing
the country cohort or shape preparation settings requires a fresh layout.

## Use from Python

The command-line tools call these same functions. Paths accept strings or
`pathlib.Path` objects; each function writes its outputs and returns an audit
dictionary. Invalid input raises `ValueError`; file errors raise `OSError`.

```python
from country_cartogram.pipeline import run
from country_cartogram.restyle import restyle
from country_cartogram.timeline import run_timeline

audit = run(
    "my_values.csv",
    config_path="my_style.json",
    output="output/map",              # filename stem
    formats=("svg", "png"),
)

restyle(
    "output/map.layout.json",
    config_path="new_style.json",     # visual overrides only
    output="output/map_restyled",
    formats=("svg",),
)

series = run_timeline(
    "history.csv",                   # year, country_code, value
    config_path="my_style.json",
    output="output/history",         # directory
    formats=(),                      # HTML + checkpoints, no static figures
    normalization="fixed",
)
```

`run(..., formats=())` saves geometry and checks without rendering figures.
Keep the returned audit for area error, coverage, and convergence; inspect the
figure as well. The layout coordinates are chart coordinates, not a geographic
reference system. See the [outputs](#outputs) below for file contents.

## Controls

Copy `config.example.json` or use a small JSON file containing only overrides.

| Setting | Default | Effect |
| --- | ---: | --- |
| `country_gap` | 0.2 | Minimum separation within groups, in chart units |
| `cluster_gap` | 3.6 | Minimum separation across groups; at least `country_gap`. Legacy mode uses cluster envelopes |
| `geographic_spread` | 0.72 | Legacy mode only: spread of country anchors |
| `geographic_pull` | 0.002 | Weak spring strength toward geographic anchors |
| `cluster_pull` | 0.006 | Long-range attraction toward the moving cluster barycenter |
| `parent_pull` | 0.04 | Strength of parent-territory links |
| `damping` | 0.7 | Fraction of velocity retained per simulated second |
| `layout_mode` | `growth` | Gradual geographic simulation; `legacy` keeps the previous layout |
| `growth_steps` | 80 | Number of smooth area-resizing stages |
| `relax_per_step` | 20 | Simultaneous force/contact updates per growth stage |
| `settle_steps` | 6000 | Maximum updates after reaching target sizes |
| `position_tolerance` | 0.002 | Maximum step displacement for the equilibrium test |
| `physics_dt` | 0.04 | Simulated seconds per update; large steps can cause jitter |
| `contact_iterations` | 50 | Collision solver iterations within each update |
| `collision_mode` | `hull` | One conservative convex hull per land component; `decomposed` is experimental |
| `collision_tolerance` | 0.12 | Extra outline simplification in `decomposed` mode only |
| `iterations` | 250 | Legacy mode only: packing iterations |
| `territory_distance` | 12 | Final preferred parent-relative offset for separate territory records |
| `satellite_distance` | 1.4 | Maximum remote-part offset as a multiple of summed part radii |
| `min_part_fraction` | 0.001 | Omit parts below this fraction of country land area |
| `max_parts` | 24 | Maximum retained components per country |
| `shape_error` | 0.02 | Pre-simulation shape error budget; see [approximation](algorithm.md) |
| `simplify` | 0.0015 | Fixed tolerance when `shape_error` is 0; set to 0 to disable simplification |
| `total_area` | 10000 | Total displayed land area in chart units |
| `labels` | 14 | Top countries considered for automatic labeling |
| `label_countries` | `[]` | Additional countries to label |
| `label_offsets` | `{}` | Per-code callout offsets in chart units |
| `label_aliases` | `{"GBR":"UK"}` | Short labels for countries with long names |
| `colors` | six colors | Base cluster colors; stable country-specific lightness variations |
| `cluster_anchors` | six anchors | Legacy mode only: cluster `[longitude, latitude]` targets |
| `divisor`, `prefix`, `suffix`, `decimals` | `1`, `""`, `""`, `1` | Value formatting only |
| `value_label` | `Quantity` | Quantity and units shown in a flat HTML viewer |

For example:

```json
{
  "title": "Research output by country",
  "subtitle": "Country area represents publication count",
  "source_note": "Your source, year, and inclusion criteria",
  "country_gap": 0.6,
  "labels": 12,
  "decimals": 0,
  "colors": {"Europe": "#6951a3"},
  "label_countries": ["FRA"],
  "label_offsets": {"FRA": [-8, 6]}
}
```

Labels are fitted inside countries where possible, otherwise tried as callouts.
Labels that cannot be placed without colliding with other labels or land are
omitted and listed in the audit. The largest ten quantities also appear in the
ranking panel. For publication, inspect the SVG/PDF and adjust labels as needed.

## Outputs

Use `--initial-layout previous.layout.json` to continue a single map, or
`country-cartogram-timeline` for an ordered time series. See the
[time-series guide](time-series.md) for fixed absolute scaling, checkpoints,
input compatibility, and compact 2D/3D playback.

For experimental two-variable extrusion, use `--volume-column` alongside `--value-column`.
Area represents the first variable, height is second / first, and volume
represents the second. Tall, narrow columns can be hard to interpret.
See [the 3D guide](3d.md) for examples,
reusing a saved layout, input validation, and performance measurements.

| File | Contents |
| --- | --- |
| `.png` | Raster figure |
| `.svg` | Editable vector outlines and text |
| `.pdf` | Vector figure for manuscripts or slides |
| `.metrics.csv` | Values, shares, actual areas, relative errors, retained island fractions |
| `.audit.json` | Coverage, zero/missing codes, gap/area checks, omitted labels, configuration, source hashes, versions |
| `.layout.json` | Positioned polygons, properties, scales, and component metadata |
| `.growth.gif` | Optional animation captured from actual solver states |
| `.growth.png` | Optional geographic-start / resizing / settled comparison |

The layout JSON uses arbitrary **cartogram coordinates**, not longitude and
latitude, and deliberately has no `.geojson` extension. Never interpret it as a
georeferenced map. Area diagnostics measure vector polygon geometry, excluding
text and raster antialiasing. Multiple maps normalize to their own totals by
default: compare shares, not absolute area between separate charts.

## Data and reproducibility

Bundled geography: [Natural Earth](https://www.naturalearthdata.com/), 1:50m Admin 0
countries, [repository release tag v5.1.2](https://github.com/nvkelso/natural-earth-vector/tree/v5.1.2).
Source URL, SHA-256, retrieval date, and license are in `../src/country_cartogram/data/sources.json`.
Natural Earth data is [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
Its boundaries reflect its own conventions; countries, dependencies, and disputed
units are a dataset-specific statistical coverage choice. Antarctica and the
source's "Seven seas" units are excluded from this pipeline's catalog.

Static example quantities come from the historical `GDP_MD` (USD millions) and
`POP_EST` (people) attributes, with per-row source years. The time-series inputs
for population apply synthetic growth rates. The [40-year GDP series](time-series.md#forty-year-gdp-showcase)
uses World Bank observations and marked estimates. `scripts/prepare_examples.py` regenerates the Natural Earth examples'
CSVs and configurations without downloading data or changing retrieval metadata.

Code license: MIT. The bundled Natural Earth dataset is public domain.
See [development](../CONTRIBUTING.md) for checks and source organization.
