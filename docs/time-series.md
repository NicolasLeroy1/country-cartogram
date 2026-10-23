# Time series and checkpoints

Each period starts from the preceding layout. The engine reuses its prepared
shapes, positions, island arrangement, and original geographic anchors.

## Input

Use a long CSV with integer periods and a consistent country cohort:

```csv
year,country_code,value
2020,FRA,60
2020,DEU,80
2021,FRA,62
2021,DEU,81
```

Periods are sorted numerically. Select columns with `--time-column` and
`--value-column`. Optional `name`, `cluster`, and `parent` columns follow the
[ordinary input rules](usage.md).

The completed series must contain positive quantities for the same countries
in every period. Groups, parents, boundary source, and shape preparation settings
stay fixed. Inputs are validated and any requested missing-value filling happens
before the first simulation.

## Missing values and changing countries

By default, missing rows or values are errors. Opt in to a consistent cohort:

```bash
country-cartogram-timeline history.csv --missing-values interpolate \
  --viewer-only --out output/history
```

`interpolate` fills interior gaps linearly using actual year distances. Before
the first known value and after the last, it holds the nearest known value.
`--missing-values carry` holds the previous value inside gaps instead. Each
country needs at least one known positive value. Negative, zero, and nonfinite
values remain errors. Missing rows are filled for the periods present in the
input; new calendar periods are not created automatically.

Filling writes `value_status` (`observed`, `interpolated`, `carried_forward`,
or `carried_backward`) and `value_note` into the prepared per-period inputs.
The solver uses those completed values for areas. Layouts, metrics, and audits
retain their status. Estimated countries are grey in the viewer and static
figures; hover and the values table explain the method. You can supply these
columns yourself when using a different estimation method; use the generic
`estimated` status and describe the method in `value_note`.

Use consistent territorial units across the whole series. For example, keep
today's outlines throughout and estimate GDP on those units. A merger or split
needs a territory/value crosswalk first: carrying national totals alone cannot
allocate a predecessor's economy among successors. Avoid including both totals
for the same territory. Historical border changes are not simulated.

Automatic filling currently supports 2D. For 3D, prepare both quantities and
their estimates explicitly before running the timeline.

## Forty-year GDP showcase

```bash
country-cartogram-timeline examples/gdp-1985-2024.csv \
  --config examples/gdp-1985-2024.json --normalization shares \
  --viewer-only --out output/gdp-1985-2024
```

[Open/download the example](gallery/gdp-1985-2024.html). It covers 209 economies
on present-day Natural Earth boundaries for 1985–2024. The source is World Bank
WDI [GDP in constant 2015 US dollars](https://data.worldbank.org/indicator/NY.GDP.MKTP.KD),
licensed CC BY 4.0. GDP shares refer to the included cohort, not total world GDP.
Missing annual inputs are estimated before physics; economies with no known
GDP are excluded. [Provenance](../examples/gdp-1985-2024.provenance.json) records
the source date, snapshot and CSV hashes, exclusions, and World Bank/Natural
Earth code mapping.

`python scripts/prepare_gdp_timeline.py` reproduces the CSV and configuration
offline from the pinned compressed API response. `--refresh` downloads a new
response and changes the source date and hashes.

## Run a series

```bash
country-cartogram-timeline examples/population-timeline.csv \
  --config examples/population-timeline.json --viewer-only --out output/history
```

Open `output/history/timeline.html` to play, scrub, inspect values, or jump to an
annual input. A fixed camera, persistent shapes, and continuous movement help
track countries across years. The viewer interpolates positions and areas
between captured solver states while preserving the quantity encoding. This
display smoothing does not fill missing annual inputs. At 1×, one calendar year
takes one second regardless of how many solver frames that year required.
The year selector and arrow buttons jump to exact annual inputs.

The same viewer supports [experimental 3D](3d.md) with two quantities. Tall
columns on small footprints can be hard to read; use the flat timeline for
presentations centered on the first quantity.

```bash
country-cartogram-timeline examples/population-gdp-timeline.csv \
  --value-column population --volume-column gdp_usd \
  --config examples/population-gdp.json --config-3d examples/population-gdp-timeline.3d.json \
  --viewer-only --out output/history-3d
```

Area represents the first quantity, height the second divided by the first,
and volume the second. Heights share one scale across all periods. The second
quantity follows growth progress between observations and stays fixed while
positions settle. In shares mode, intermediate first quantities are recovered
from footprint shares and the interpolated total to keep the encodings consistent.

## Normalization

The default `--normalization fixed` uses:

```text
area_per_unit = configured total_area / first-period sum
country_area = area_per_unit × country_value
```

The same quantity occupies the same area in every period. Total land area may
change; appending periods does not change earlier scaling.

`--normalization shares` keeps `total_area` fixed. A constant country value can
shrink when the period total grows. Hover values include the period share.

## Continue from a checkpoint

For a single map update:

```bash
country-cartogram values_2021.csv --initial-layout output/map_2020.layout.json \
  --config config.json --out output/map_2021
```

This command normalizes each run independently. Set `total_area` to
`fixed_area_per_unit × sum(values)` yourself for absolute comparisons, or use
the timeline command to manage that scale.

Append one or more periods in a separate output directory:

```bash
country-cartogram-timeline later.csv --initial-layout output/history/2022.layout.json \
  --initial-time 2022 --config config.json --viewer-only --out output/later
```

Fixed mode inherits the checkpoint's area-per-unit factor. Shares mode requires
the checkpoint total area to match `total_area`. A 3D checkpoint also needs
`--initial-values output/history-3d/inputs/2022.csv` with both previous quantities.
Use an explicit 3D `height_scale` to keep the vertical scale across separate exports.

The first frame matches the checkpoint. Velocities restart at zero; small area
changes use fewer growth stages. Changing `shape_error`, `simplify`, island
settings, or boundaries requires a fresh layout. For checkpoints predating
`shape_error`, set it to `0`; older layouts also need their companion audit.

## Files and computation

| Output | Content |
| --- | --- |
| `timeline.html` | Offline 2D/3D viewer |
| `timeline.audit.json` | Scaling, source hash, period audits, solver timings |
| `2020.layout.json`, etc. | Reusable checkpoints |
| `2020.audit.json`, `.metrics.csv` | Per-period numerical checks |
| `inputs/2020.csv`, `.json` | Exact inputs and solver configuration |
| Per-period figures | Enabled by default; skip with `--viewer-only` |
| `timeline.gif` | Optional endpoint slideshow (`--gif`) |

The boundary catalog is parsed once, hull fixtures are cached between periods,
and each country is triangulated once for the viewer. Playback updates compressed
positions, scales, and heights without rebuilding meshes. Rendering stops while paused. Warm starts can
still trigger contact rearrangements, so inspect convergence, movement, and
solver timings in the audit. Endpoint checks do not certify every transient frame.

In each period's audit, `overlap_pairs` and `max_relative_area_error` check the
geometry. `packing.equilibrium_converged` separately reports whether motion fell
below the stopping tolerance. A map can have valid areas and gaps while still
reaching its settling limit. Check this flag when assessing a series; valid
areas and gaps alone do not imply equilibrium.
