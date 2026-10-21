# Country Cartogram

A reusable Python pipeline for separated, shape-preserving country cartograms.
Change the country quantities in a CSV, choose clusters and a style, then export
PNG, editable SVG, vector PDF, coordinates, and numerical validation results.

See [the project overview](../README.md) for a quick start and [algorithm notes](algorithm.md) for the design rationale.

## Run it

Python 3.10+ is required (tested with Python 3.12). From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .

country-cartogram examples/gdp.csv --config examples/gdp.json --out output/gdp
country-cartogram examples/population.csv --config examples/population.json --out output/population
```

The geography is bundled, so after dependency installation, rendering needs no
network connection, API key, or external application. `requirements-tested.txt`
records the direct dependency versions used for the included outputs.

## Supply your quantities

Start with `examples/your_values.csv`. Fill the rows you need and delete the rest.
The two required columns are `country_code` and `value`:

```csv
country_code,value
USA,29200
CHN,18700
DEU,4700
FRA,3200
BRA,2200
AUS,1700
```

These example numbers are merely a demonstration of the input format. Use your
own sourced values for analysis. Codes are predominantly ISO alpha-3; the exact
accepted identifiers and names are in `../src/country_cartogram/data/country_catalog.csv`. Natural Earth
also uses special identifiers such as `KOS`, `CYN`, `SOL`, `KAS`, `IOA`, and `ATC`.

Optional columns:

| Column | Meaning |
| --- | --- |
| `name` | Display name override |
| `cluster` | Continent or any grouping you choose |
| `parent` | Another included country code; brings a territory near its parent |
| `year` | Provenance carried in the input; does not affect geometry |

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

## The geometry and physics

The default `growth` layout implements a single simultaneous simulation:

1. Start with countries and their retained islands at their projected geographic
   positions. Apply one common scale to the whole map so the initial total land
   area equals `total_area`. There is no independent continental repacking.
2. Define each country's final target area as
   `T_i = value_i / sum(values) * total_area`.
3. Advance a smooth progress parameter `t` from 0 to 1. Set the current area to
   `A_i(t) = (1-t) * A_i(0) + t * T_i`, and uniformly scale each outline by the
   square root of its area ratio. Countries grow and shrink gradually.
4. Throughout resizing, apply **long-range attraction within every cluster**:
   `acceleration_i = cluster_pull * (cluster_barycenter - position_i)`.
   The moving barycenter uses the final country values as weights. Also apply
   a weaker spring toward the geographic anchor, damping, and parent links.
5. Apply **short-range repulsion only at local polygon contacts** with the
   Pymunk/Chipmunk rigid-body engine. By default, each retained land component
   uses its convex hull as an inexpensive collision shape. Islands have separate
   hulls attached to the same country body; no hull spans a whole continent.
   These hulls contain the finer outlines, making separation conservative.
   A contact radius supplies half the requested gap on each side, plus a small
   numerical margin. Mass is proportional to quantity, rotation is locked,
   and contacts have no bounce or friction.
6. Gradually bring remote polygon parts closer to their main part, retaining
   their projected direction. Explicit territory records follow parent links
   while preserving their own values. These insets are introduced progressively,
   rather than relocating the initial map before the simulation starts.
7. Once all target sizes are reached, keep both attraction and repulsion active
   during settling. Stop after 15 consecutive steps below `position_tolerance`,
   or at the configured iteration limit. The audit reports which occurred;
   reaching the limit is never described as proven equilibrium.
8. Validate the actual final polygon geometries, all pairwise country distances,
   and area ratios independently of the physics engine. A failed audit aborts
   the export instead of accepting an overlapping result.

Country outlines are translated and uniformly scaled, never rotated or warped.
The final sum of a country's retained polygon areas is exactly proportional to
its value. Doubling a quantity does not double its width: four times the area
means twice the linear dimensions. Values are normalized independently per map.

**Two geometry resolutions:** the solver moves coarse collision shapes; the
renderer exports finer simplified coastlines. This saves collision work without
replacing the displayed countries by their hulls. Concave coasts can leave more
empty space than requested. The default also allows an enclave to move outside
its surrounding country when their new areas no longer fit together. Geographic
neighbor relationships are soft preferences, not fixed constraints. Neither
included example needs additional shape deformation.

For experimentation, `collision_mode: "decomposed"` uses simplified concave
polygons partitioned into convex pieces, retaining holes. `collision_tolerance`
controls the additional simplification in final chart units; a measured boundary
error is added to the contact margin. It can fit concavities more closely, but is
slower and may trap enclaves as the surrounding hole shrinks. This mode failed
the GDP example's gap audit during development; the verified default is `hull`.

This is a deterministic rigid-body layout with externally driven shape changes.
The resizing is not a calibrated material-expansion simulation. Gradual resizing
is a continuation heuristic,
not a guarantee of a unique or globally optimal layout. With extreme quantities,
neighbor relationships and geographic positions can still change substantially.

The coastline preprocessing retains substantial islands and simplifies very fine
boundary detail. The retained fraction is reported per country. Within-country
parts can briefly overlap while moving to their insets; their pieces share one
physics body and therefore do not collide with each other. Final components are disjoint and final areas
are checked. Thus the final area guarantees do not imply exact area throughout
those intermediate inset transitions.

For comparison, `layout_mode: "legacy"` selects the original two-stage method:
pack country convex envelopes within continents, then pack entire continent
clusters. `geographic_spread`, `cluster_gap`, `cluster_anchors`, and `iterations`
apply only to this legacy mode. To reproduce its original force settings, also
set `geographic_pull` to 0.015 and `cluster_pull` to 0.003.

```bash
# The default is the gradual geographic-growth simulation.
country-cartogram examples/gdp.csv --config examples/gdp.json --out output/gdp_growth --animate
```

`--animate` also exports an actual solver trajectory as `.growth.gif` and a
three-stage overview as `.growth.png`. They show geographic start, resizing,
and final settling. They are not interpolations between the old and new figures.

The bundled outputs were regenerated with the default growth solver:

| Example | Countries / territories | Collision pieces | Minimum actual gap | Maximum relative area error | Settling tolerance reached |
| --- | ---: | ---: | ---: | ---: | --- |
| GDP | 230 | 693 | 0.4190 | 6.63e-14 | Yes |
| Population | 232 | 695 | 0.4190 | 2.35e-13 | Yes |

Both have zero overlapping country pairs. Physics took approximately 30 seconds
per example in the development environment; figure and animation export add
time. These are observed runs, not a runtime guarantee. Numerical convergence
means 15 successive steps below the configured displacement threshold, not a
proof of a global energy minimum.

## Controls

Copy `config.example.json` or use a small JSON file containing only overrides.

| Setting | Default | Effect |
| --- | ---: | --- |
| `country_gap` | 0.4 | Minimum separation between all countries, in chart units |
| `cluster_gap` | 2.5 | Legacy mode only: separation between cluster envelopes |
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
| `simplify` | 0.0015 | Outline tolerance as a fraction of square root of country area |
| `total_area` | 10000 | Total displayed land area in chart units |
| `labels` | 14 | Top countries considered for automatic labeling |
| `label_countries` | `[]` | Additional countries to label |
| `label_offsets` | `{}` | Per-code callout offsets in chart units |
| `label_aliases` | `{"GBR":"UK"}` | Short labels for countries with long names |
| `colors` | six colors | Base cluster colors; stable country-specific lightness variations |
| `cluster_anchors` | six anchors | Legacy mode only: cluster `[longitude, latitude]` targets |
| `divisor`, `prefix`, `suffix`, `decimals` | `1`, `""`, `""`, `1` | Value formatting only |

For example:

```json
{
  "title": "Research output by country",
  "subtitle": "Country area represents publication count",
  "source_note": "Your source, year, and inclusion criteria",
  "country_gap": 0.6,
  "cluster_gap": 3.5,
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

To change typography, labels, titles, or colors without rerunning the simulation:

```bash
country-cartogram-restyle output/gdp.layout.json --config my_style.json --out output/gdp_restyled
```

The restyling configuration accepts visual settings only. Force, gap, geometry,
or grouping changes require a fresh `src/country_cartogram/pipeline.py` run.

## Outputs

Use `--initial-layout previous.layout.json` to continue a single map, or
`country-cartogram-timeline` for an ordered time series. See the
[time-series guide](time-series.md) for fixed absolute scaling, checkpoints,
input compatibility, and compact SVG playback.

For two-variable extrusion, use `--volume-column` alongside `--value-column`.
Area represents the first variable, height is second / first, and volume
represents the second. See [the 3D guide](3d.md) for installation, examples,
reusing a saved layout, input validation, and performance measurements.

| File | Contents |
| --- | --- |
| `.png` | Presentation-ready raster image |
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
units are not a universal statistical coverage standard. Antarctica and the
source's "Seven seas" units are excluded from this pipeline's catalog.

Both demonstrations use **historical attributes bundled with the geography**:
`GDP_MD` (US dollars, millions) and `POP_EST` (persons), with a source year in each
CSV row. Years vary. These are software examples, not verified current statistics
and not a reconstruction of the reference's 2024 IMF data. In particular, inspect
dependency coverage and possible source overlap before making substantive totals.
Replace the example quantities with your selected data source for research use.

`scripts/prepare_examples.py` regenerates both examples, configurations, and the blank
template directly from the bundled boundary attributes.

Technical references: [Pymunk/Chipmunk](https://www.pymunk.org/en/latest/pymunk.html),
[Shapely documentation](https://shapely.readthedocs.io/en/stable/manual.html),
[Natural Earth country layer](https://www.naturalearthdata.com/downloads/50m-cultural-vectors/50m-admin-0-countries-2/).
The growth engine is in `src/country_cartogram/physics.py` and the legacy packer in `src/country_cartogram/geometry.py`.
No online cartogram service or proprietary rendering engine is required.

## Verify

```bash
python -m unittest discover -s tests -v
```

Tests cover exact area scaling, satellite direction, polygon holes, nested-body
collision separation, deterministic behavior, invalid inputs, parent cycles,
the distinction between missing and zero quantities, true geographic initialization,
collision partitions preserving concave gaps and holes, enclave separation with
the default coarse collision shapes, and cluster attraction during growth. The two full example
runs provide separate end-to-end checks with substantially different quantities.

## Files to modify

- Quantities and grouping: your CSV.
- Appearance and forces: your JSON configuration.
- Shape preparation and legacy layout: `src/country_cartogram/geometry.py`.
- Geometry interpolation: `src/country_cartogram/progressive.py`.
- Gradual geographic growth, attraction, and local contacts: `src/country_cartogram/physics.py`.
- Simulation animation: `src/country_cartogram/evolution.py`.
- Validation, labeling, and rendering: `src/country_cartogram/pipeline.py`.

Code license: MIT (see `LICENSE`). The bundled Natural Earth dataset is public domain.
