# Country Cartogram

Turn a CSV of country quantities into a separated, shape-preserving world map.
Countries start at their geographic positions, gradually grow or shrink, and
settle under local repulsion and continuous attraction within each cluster.

![Geographic start, gradual resizing, and settling](docs/gallery/gdp.growth.gif)

The animation is captured from actual solver states. The example quantities are
historical Natural Earth attributes, not current GDP estimates.

## What is included

- **Gradual geographic growth:** interpolate areas, with linear dimensions
  changing by the square root of the area ratio.
- **Two geometry resolutions:** conservative convex collision shapes for speed,
  finer country outlines for the exported figure.
- **Cluster attraction:** value-weighted moving barycenters, geographic anchors,
  and stable color variations within each continent or custom group.
- **Islands and territories:** compact remote parts while approximately retaining
  direction; optional parent links for separately quantified territories.
- **Audited output:** validate real polygons for overlaps, minimum gaps, and
  proportional areas before exporting PNG, SVG, PDF, coordinates, and metrics.
- **Two-variable 3D:** footprint area represents a first quantity, height is
  second / first, and volume represents the second quantity. Rotate, zoom,
  inspect values, or return to a top view in the interactive HTML.
- **Temporal continuity:** start a new map from the previous layout, or process
  a series of years with fixed absolute scaling and lightweight SVG playback.
- **Reproducible examples:** bundled geography, GDP and population CSVs, an
  animation exporter, a restyling command, tests, and GitHub Actions workflows.

## Quick start

Requires Python 3.10 or later. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .

country-cartogram examples/gdp.csv --config examples/gdp.json --out output/gdp --animate
country-cartogram examples/population.csv --config examples/population.json --out output/population
```

`python -m country_cartogram` is equivalent to `country-cartogram`. Once the
dependencies are installed, the pipeline works offline. Generated outputs stay
in the ignored `output/` directory; selected previews are kept in `docs/gallery/`.

## Use your own data

Create `my_values.csv` with the two required columns:

```csv
country_code,value
FRA,42
DEU,60
ITA,35
ESP,30
```

```bash
country-cartogram my_values.csv --out output/my_map
```

The values above are illustrative. Optional columns are `name`, `cluster`, and
`parent`. Use the [country catalog](src/country_cartogram/data/country_catalog.csv)
for accepted codes and [the blank template](examples/your_values.csv) to prepare
a larger dataset. Delete unused template rows: blank quantities are rejected.
Zero means zero area; missing rows are recorded separately.

Copy [config.example.json](config.example.json), or supply a JSON file containing
only overrides. The most useful controls are `country_gap`, `cluster_pull`,
`geographic_pull`, `growth_steps`, `relax_per_step`, `satellite_distance`, and
`colors`. [The usage guide](docs/usage.md) describes every setting and output.

Python API:

```python
from pathlib import Path
from country_cartogram.pipeline import run

report = run(
    Path("my_values.csv"),
    output=Path("output/my_map"),
    formats=("svg", "png"),
)
print(report["minimum_country_distance"])
```

## Add a second variable in 3D

For example, **area = population**, **height = GDP per capita**, and
**volume = GDP**. Heights use a common linear scale, so the volume relationship
holds across all countries.

```bash
python -m pip install -e '.[3d]'
country-cartogram examples/population-gdp.csv \
  --value-column population --volume-column gdp_usd \
  --config examples/population-gdp.json \
  --config-3d examples/population-gdp.3d.json \
  --out output/population-gdp --preview-3d
```

Open `output/population-gdp.3d.html` locally. The default HTML works offline.
The optional `.3d.png` uses the same meshes, and `.3d.metrics.csv` /
`.3d.audit.json` record heights, measured mesh volumes, scale factors, and errors.

![Population as area, GDP per capita as height, GDP as volume](docs/gallery/population-gdp.3d.png)

The [interactive example](docs/gallery/population-gdp.3d.html) is a compact CDN
export: download its raw file and open it in a browser with internet access.
GitHub displays HTML source rather than executing the visualization.

When only the second variable changes, reuse the positioned polygons:

```bash
country-cartogram-3d output/population-gdp.layout.json examples/population-gdp.csv \
  --area-column population --volume-column gdp_usd \
  --config examples/population-gdp.3d.json --out output/new-heights
```

This skips the physics simulation. On the development machine, meshing, auditing,
and compact HTML export took **1.9 seconds median** for 231 countries / territories
(184,062 triangles). See [3D usage, mathematics, and benchmarks](docs/3d.md).

## Continue through time

Start the next map from a saved layout instead of restarting from geography:

```bash
country-cartogram values_2021.csv --initial-layout output/map_2020.layout.json \
  --config my_config.json --out output/map_2021 --animate
```

The previous country positions, sizes, and compacted islands become the initial
state. The original geographic anchors are retained. Small changes use fewer
resizing stages, followed by the usual collision checks and settling criterion.

For a long CSV with `year,country_code,value` columns:

```bash
country-cartogram-timeline examples/population-timeline.csv \
  --config examples/population-timeline.json --out output/history --gif
```

Open `output/history/timeline.html` to play or scrub actual solver states. The
HTML stores each outline once and animates only positions and scale factors.
It works offline. Each period also has its own audited layout and figures.
The example is a clearly labeled **synthetic scenario**, not observed history.

![Audited period endpoints in the synthetic population scenario](docs/gallery/population-timeline.gif)

Download the [interactive timeline](docs/gallery/population-timeline.html) and
open it locally for the actual resizing and settling frames. The example's
physics took 28.7 seconds for the initial map, then 3.2 and 2.2 seconds for the
continued periods; all three reached the same convergence tolerance.

To append new observations in another run:

```bash
country-cartogram-timeline later_years.csv --initial-layout output/history/2022.layout.json \
  --initial-time 2022 --config my_config.json --out output/later-history
```

The default timeline uses the same area per unit across periods. Use
`--normalization shares` to compare shares with a constant total map area.
Continuation requires consistent positive country coverage and grouping.
See [time-series inputs, checkpoints, and normalization](docs/time-series.md).

## Examples and checks

![GDP cartogram](docs/gallery/gdp.png)

![Population cartogram](docs/gallery/population.png)

| Included example | Countries / territories | Overlapping pairs | Minimum gap | Maximum relative area error |
| --- | ---: | ---: | ---: | ---: |
| GDP | 230 | 0 | 0.4190 | 6.63e-14 |
| Population | 232 | 0 | 0.4190 | 2.35e-13 |

Both reached the configured settling tolerance. These are observed results for
the included datasets; arbitrary inputs can need different force settings or
more settling time. The corresponding audits are in [docs/gallery](docs/gallery).

## How it works

The [algorithm notes](docs/algorithm.md) explain the area schedule, forces,
satellite handling, collision approximation, and the design choices that mattered
in practice. A separate [development guide](CONTRIBUTING.md) covers tests and
reproducing the examples.

| Module | Responsibility |
| --- | --- |
| `geometry.py` | Projection, outline simplification, islands, legacy packer |
| `progressive.py` | Continuous intermediate geometry from geographic to target sizes |
| `physics.py` | Rigid bodies, local contacts, cluster and geographic forces, settling |
| `pipeline.py` | CSV validation, orchestration, independent geometry audit, rendering |
| `evolution.py` | GIF and three-stage overview from solver frames |
| `restyle.py` | Change presentation without recomputing the layout |
| `extrusion.py` | Audited bivariate meshes, interactive 3D HTML, optional PNG |
| `configuration.py` | Shared configuration and stable cluster colors |
| `continuation.py` | Previous layouts as initial shapes and positions |
| `timeline.py` | Ordered periods, checkpoint continuation, compact SVG playback |

These modules live in `src/country_cartogram/`.

## Limits and provenance

This is an experimental non-contiguous cartogram layout, not a geographical map
or a globally optimal packing algorithm. Positions and neighbor relationships
may change. The default collision hulls can leave extra space near concave
coasts. The exported outlines use uniform scaling and translation; additional
elastic deformation is a possible extension, not an implemented feature.

Geography is bundled from Natural Earth 1:50m, release tag v5.1.2, with
[source metadata and checksum](src/country_cartogram/data/sources.json).
Natural Earth data is public domain. Historical example years vary by row;
replace them with a consistent, sourced dataset for substantive analysis.
Boundary and territory conventions follow the source dataset.

Code: [MIT license](LICENSE). No online cartogram service is required.
