# Usage

See the [README](../README.md) for installation and a first map.

## Supply your quantities

Create a CSV, or copy `examples/your_values.csv` and keep the rows you need:

```csv
id,value
FRA,42
DEU,60
ITA,35
```

For the bundled world map, use IDs from the [region catalog](../src/country_cartogram/data/region_catalog.csv).
For a custom map, match the polygon feature IDs.

Values must be finite and non-negative. Zero values and missing regions are
omitted and listed separately in the audit. Blank values, negative values,
unknown codes, and duplicate regions are errors. Very small values can be
invisible in raster exports; no minimum region area is imposed.

Optional columns:

| Column | Meaning |
| --- | --- |
| `name` | Display name |
| `group` | Group; defaults to the continent |
| `parent` | Included region to keep a dependent region near |
| `year` | Source year; does not affect a static map |
| `value_status`, `value_note` | Estimate status and method; see [missing values](time-series.md#missing-values-and-changing-regions) |

Static figures support up to 12 groups. If `parent` is absent, the boundary data
supplies it; a blank cell disables the link. Parent and child must share a group.
Chains and cycles are rejected. If the parent is omitted, its link is disabled
with a warning. Land already included in a region's boundary, such as French
Guiana in France, does not need a separate row.

## Run

```bash
country-cartogram values.csv --out output/map
# Read another value column:
country-cartogram values.csv --value-column publications --out output/publications
# Choose formats and resolution:
country-cartogram values.csv --formats svg pdf png --dpi 300 --out output/print_map
```

`--out` is a filename stem without an extension; matching output files are
overwritten. Use `--config` to apply a style file as shown below.

The simulation resizes regions from geographic positions, keeping their
orientation. It checks final areas, overlaps, and gaps before exporting.
Add `--animate` to save a growth GIF and a three-stage PNG.
See [the algorithm](algorithm.md) for how the simulation works.

## Style

Create `style.json` with only the settings you want to change:

```json
{
  "title": "Research output by region",
  "subtitle": "Area represents publication count",
  "source_note": "Source, year, and units",
  "decimals": 0,
  "colors": {"Europe": "#6951a3"},
  "label_regions": ["FRA"]
}
```

```bash
country-cartogram values.csv --config style.json --out output/map
```

| Setting | Default | Effect |
| --- | ---: | --- |
| `colors` | six colors | Group colors, with stable lightness variations by region |
| `labels` | 14 | Largest regions considered for labels |
| `label_regions` | `[]` | Additional region IDs to label |
| `label_offsets` | `{}` | Per-code callout offsets, e.g. `{"FRA": [-8, 6]}` |
| `label_aliases` | `{}` | Short names for labels |
| `divisor`, `prefix`, `suffix`, `decimals` | `1`, `""`, `""`, `1` | Value formatting |
| `value_label` | `Quantity` | Quantity and units in a flat HTML viewer |

Labels are placed inside regions or in nearby callouts. Labels that do not
fit are omitted and listed in the audit. Inspect the SVG or PDF and adjust
labels as needed. The largest ten values also appear below the static map.

## Layout settings

Distances use chart units. The default total land area is 10000 square units.

| Setting | Default | Effect |
| --- | ---: | --- |
| `projection` | `EPSG:8857` | Output CRS, or `local` to keep input coordinates |
| `source_crs` | `EPSG:4326` | Input CRS for geographic/projected maps |
| `total_area` | 10000 | Total land area |
| `region_gap` | 0.2 | Minimum gap within groups |
| `group_gap` | 3.6 | Minimum gap between groups, also bounded by `region_gap` |
| `shape_error` | 0.02 | Shape error budget applied before simulation |
| `simplify` | 0.0015 | Fixed tolerance when `shape_error` is 0; set to 0 to disable simplification |
| `min_part_fraction` | 0.001 | Omit islands below this fraction of a region's area |
| `max_parts` | 24 | Maximum retained parts per region |
| `satellite_distance` | 1.4 | Limit remote island offsets, relative to part radii |
| `territory_distance` | 12 | Preferred parent-territory distance |

See [shape approximation](algorithm.md#shape-approximation) for the error measure
and how island selection is recorded. Final areas preserve the input proportions.

## Solver settings

Most runs can use the defaults. Copy [config.example.json](../config.example.json)
for the full configuration.

| Setting | Default | Effect |
| --- | ---: | --- |
| `anchor_pull` | 0.002 | Attraction toward geographic anchors |
| `group_pull` | 0.006 | Attraction toward each group's moving center |
| `parent_pull` | 0.04 | Strength of parent-territory links |
| `damping` | 0.7 | Fraction of velocity retained per simulated second |
| `growth_steps` | 80 | Maximum resizing stages; continuation can use fewer |
| `relax_per_step` | 20 | Force and contact updates per stage |
| `settle_steps` | 6000 | Maximum updates after resizing |
| `position_tolerance` | 0.002 | Movement threshold for convergence |
| `physics_dt` | 0.04 | Simulated seconds per update |
| `contact_iterations` | 50 | Contact solver iterations per update |
| `collision_mode` | `hull` | Conservative hulls; `decomposed` is experimental |
| `collision_tolerance` | 0.12 | Additional simplification in `decomposed` mode |

## Reuse a saved layout

Keep `map.layout.json`, `map.audit.json`, and `map.metrics.csv` together.
To change the style without rerunning the simulation:

```bash
country-cartogram-restyle output/map.layout.json --config style.json --out output/restyled
```

The style file accepts visual settings only. Restyling copies the saved shapes
and checkpoint state unchanged. New quantities use
[continuation or a timeline](time-series.md); changing the region set, grouping,
or shape preparation requires a fresh layout.

## Use from Python

These functions also power the commands. They accept strings or `Path` objects,
write their outputs, and return audit dictionaries.

```python
from country_cartogram.pipeline import run
from country_cartogram.restyle import restyle
from country_cartogram.timeline import run_timeline

audit = run(
    "values.csv",
    config_path="style.json",
    output="output/map",              # filename stem
    formats=("svg", "png"),
)

restyle(
    "output/map.layout.json",
    config_path="new_style.json",
    output="output/restyled",
    formats=("svg",),
)

series = run_timeline(
    "history.csv",                    # year, id, value
    output="output/history",          # directory
    formats=(),                       # skip static figures
    normalization="fixed",
)
```

`run(..., formats=())` saves the layout, metrics, and audit without figures.
Check the audit for area error and convergence. Invalid inputs raise `ValueError`;
file errors raise `OSError`.

## Outputs

| File | Contents |
| --- | --- |
| `.png`, `.svg`, `.pdf` | Raster and vector figures |
| `.layout.json` | Positioned polygons and state for continuation |
| `.metrics.csv` | Values, shares, areas, errors, retained island fractions |
| `.audit.json` | Coverage, area/gap checks, convergence, settings, source hashes, versions |
| `.growth.gif` | Optional captured simulation frames |
| `.growth.png` | Optional start / resizing / settled comparison |

Coordinates are chart units, not longitude and latitude. Area checks measure
vector polygons. Separate static runs normalize to their own totals; use a
[timeline](time-series.md#normalization) to compare absolute areas across years.
For a second quantity encoded by height and volume, see [3D](3d.md).

## Base maps

Use `--boundaries map.geojson` with a GeoJSON FeatureCollection of Polygon or
MultiPolygon features. Each feature needs an `id`, at the feature level or in
`properties`. IDs are case-insensitive and saved in uppercase. Optional
`name`, `group`, and `parent` properties provide defaults for the CSV.

```json
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "id": "NORTH",
    "properties": {"name": "Northern Reach", "group": "Mainland"},
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 3], [0, 3], [0, 0]]]}
  }]
}
```

Choose the coordinate handling for your map:

| Input | Options |
| --- | --- |
| Longitude/latitude | Default: WGS84 to Equal Earth |
| National map in longitude/latitude | Choose an output CRS, e.g. `--projection EPSG:3035` |
| Already projected boundaries | Set both `--source-crs` and `--projection` |
| Fantasy map or drawing | `--projection local` preserves input coordinates |

```bash
country-cartogram-timeline examples/fantasy.csv --boundaries examples/fantasy.geojson \
  --config examples/fantasy.json --viewer-only --out output/fantasy
```

The same map options work in `run()` and `run_timeline()` as `boundaries`,
`source_crs`, and `projection`. You can also put the CRS settings in the JSON
configuration. The bundled Natural Earth map is loaded through its own adapter;
the solver and renderers use the same planar region format for every map.

## Data

Bundled boundaries are Natural Earth 1:50m Admin 0 regions,
[release v5.1.2](https://github.com/nvkelso/natural-earth-vector/tree/v5.1.2),
excluding Antarctica and the source's open-ocean units. They follow Natural
Earth's boundary conventions. URLs, hashes, dates, and the public-domain license
are in [source metadata](../src/country_cartogram/data/sources.json).

Static examples use historical `GDP_MD` (USD millions) and `POP_EST` (people)
attributes, with source years per row. Population timelines use synthetic growth.
The [40-year GDP timeline](time-series.md#forty-year-gdp-showcase) uses World Bank
observations and marked estimates. See [development](../CONTRIBUTING.md) to
regenerate the inputs.
