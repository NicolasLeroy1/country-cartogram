# Time series

Each period starts from the previous map's shapes and landmass positions.
Disconnected parts move independently and stay together through attraction.
They share one regional quantity and resize by the same factor.

## Input

Create `history.csv` with integer years and region values:

```csv
year,id,value
2020,FRA,60
2020,DEU,80
2021,FRA,62
2021,DEU,81
```

```bash
country-cartogram-timeline history.csv --viewer-only --out output/history
```

Open `output/history/timeline.html`. Use Play, the slider, or the year selector.
At 1×, one calendar year takes one second. The selector and arrow buttons jump
to annual inputs; playback moves continuously between captured simulation states.

Years are sorted numerically. Use `--time-column` and `--value-column` to select
other column names. Optional names, groups, and parents follow the
[CSV input rules](usage.md#supply-your-quantities).
The same regions must have positive values in every period, with fixed groups,
parents, and shape settings. Missing values can be filled as described below.

`--viewer-only` skips static figures. Omit it to also generate PNG and SVG maps
for each period. Add `--gif` for a slideshow of annual maps.

## Normalization

| Option | What stays fixed | Use it to compare |
| --- | --- | --- |
| `--normalization fixed` (default) | Area per value unit | Absolute quantities |
| `--normalization shares` | Total map area | Shares of the included total |

Fixed mode sets area per unit to `total_area / first-period total`.
The same value then occupies the same area in every period; total land area can
change. Adding later periods keeps that scale.

Shares mode keeps the map's total land area at `total_area`. A region with a
constant value can shrink if the total grows. Hover values include its share.

## Missing values and changing regions

Missing rows or values are errors by default. To estimate them before simulation:

```bash
country-cartogram-timeline history.csv --missing-values interpolate \
  --viewer-only --out output/history
```

- `interpolate`: fill interior gaps linearly by year; hold the nearest value before or after the known range.
- `carry`: hold the previous value inside gaps; use the first known value for earlier years.

Each region needs at least one known positive value. Zero, negative, and
nonfinite values remain errors. Filling uses only years present in the CSV;
it does not create missing calendar years.

Estimates are grey. Hover or open the values table to see their status and method.
The completed inputs retain `value_status` (`observed`, `interpolated`,
`carried_forward`, or `carried_backward`) and `value_note`. For your own estimates,
supply `value_status=estimated` and describe the method in `value_note`.

Use the same territorial units throughout. For mergers or splits, first allocate
historical values to those units. Carrying a predecessor's national total does
not divide its economy among successors. Avoid counting the same territory twice;
historical border changes are not simulated.

Automatic filling supports 2D. For 3D, prepare both quantities and their estimates
before running the timeline.

## Forty-year GDP showcase

```bash
country-cartogram-timeline examples/gdp-1985-2024.csv \
  --config examples/gdp-1985-2024.json --normalization shares \
  --viewer-only --out output/gdp-1985-2024
```

Download the [ready-made HTML](gallery/gdp-1985-2024.html) and open it locally.
It covers 209 economies in 1985–2024 using World Bank WDI
[GDP in constant 2015 US dollars](https://data.worldbank.org/indicator/NY.GDP.MKTP.KD),
licensed CC BY 4.0. Area shows shares of the included economies' GDP.

Present-day Natural Earth outlines are retained. Missing annual values are
estimated before simulation; economies with no known GDP are excluded.
[Provenance](../examples/gdp-1985-2024.provenance.json) records the source dates,
hashes, exclusions, code mapping, and limits of historical comparisons.

`python scripts/prepare_gdp_timeline.py` reproduces the inputs offline from the
bundled snapshot. `--refresh` downloads new observations and updates provenance.

## Experimental 3D

```bash
country-cartogram-timeline examples/population-gdp-timeline.csv \
  --value-column population --volume-column gdp_usd \
  --config examples/population-gdp.json --config-3d examples/population-gdp-timeline.3d.json \
  --viewer-only --out output/history-3d
```

Area represents the first quantity, height the second divided by the first,
and volume the second. One height scale applies to all periods. Small footprints
with high ratios can be hard to read; see [3D](3d.md) for controls and limits.

## Continue from a checkpoint

Append later periods to a saved map:

```bash
country-cartogram-timeline later.csv --initial-layout output/history/2021.layout.json \
  --initial-time 2021 --viewer-only --out output/later
```

Use the same configuration as the original run. Fixed mode inherits its area
per unit; shares mode requires the checkpoint's total area to match `total_area`.
The first frame matches the checkpoint, with velocities reset to zero.

A 3D checkpoint also needs `--initial-values` pointing to the earlier paired CSV.
Set the same explicit `height_scale` to compare heights across separate exports.
Changing region coverage, groups, shape preparation, or boundaries requires a
fresh layout. Projection and source CRS must also stay the same.

For a single update, use:

```bash
country-cartogram later.csv --initial-layout output/history/2021.layout.json \
  --out output/later-map
```

This command normalizes to its own total. To preserve area per unit, set
`total_area = previous_area_per_unit × new_total`, or use the timeline command.

## Outputs and checks

| Output | Content |
| --- | --- |
| `timeline.html` | Offline viewer |
| `timeline.audit.json` | Scaling, source hash, period checks, solver timings |
| `2020.layout.json`, etc. | Saved maps for continuation |
| `2020.audit.json`, `.metrics.csv` | Per-period checks and values |
| `inputs/2020.csv`, `.json` | Prepared inputs and solver settings |
| Per-period figures | Enabled unless `--viewer-only` is set |
| `timeline.gif` | Optional annual-map slideshow |

Shapes and collision hulls are reused across periods. The viewer stores one mesh
per landmass and updates positions and scales during playback. Parts share
regional values and estimate status.

Check each period's `overlap_pairs`, `max_relative_area_error`, and
`packing.equilibrium_converged`. Valid areas and gaps can still accompany an
unfinished settling phase. Final-map checks do not certify every transition frame.
