# Time series and continuation

Temporal maps should evolve from the previous map rather than repeat geographic
initialization. This package supports both a single update and a complete time
series, including continuation from a checkpoint saved in an earlier run.

## Update one map

```bash
country-cartogram values_2021.csv --initial-layout output/map_2020.layout.json \
  --config my_config.json --out output/map_2021 --animate
```

The optional animation starts with **Previous layout**. Both forces remain
active as countries grow or shrink. The first frame has the previous map's
positions, areas, and island arrangements; islands are not unfolded back to
their remote geographic positions. Velocities start at zero. The original
geographic anchors and parent directions are preserved to limit cumulative
drift across a long sequence.

Only quantities, display names, styles, and force settings may change. Country
coverage, grouping, parent links, the boundary source, and outline preparation
settings must remain compatible. A changed `simplify`, `satellite_distance`,
`min_part_fraction`, or `max_parts` setting requires a fresh layout. Starting
from a legacy two-stage packing is not supported.

Version 0.3 layouts contain the anchor frame and boundary fingerprint themselves.
Older growth-mode layouts work when their companion `.audit.json` is present.
Old anchors are reconstructed from source component centers; new layouts retain
the exact anchor coordinates. Never substitute a different boundary file to
resume an existing series.

`country-cartogram` still normalizes each individual run to `total_area`.
For a fixed absolute scale across individual updates, set each run's
`total_area = fixed_area_per_unit * sum(values)`, or use the timeline command,
which manages this automatically.

Python API:

```python
from pathlib import Path
from country_cartogram.pipeline import run

report = run(
    Path("values_2021.csv"), initial_layout=Path("output/map_2020.layout.json"),
    config_path=Path("my_config.json"), output=Path("output/map_2021"),
)
print(report["packing"]["mean_displacement_from_start"])
```

The same update may also use `--volume-column` to regenerate a 3D export with a
second variable. The dedicated timeline viewer is 2D.

## Process a time series

Use one row per country and period, with integer period labels:

```csv
year,country_code,value
2020,FRA,60
2020,DEU,80
2021,FRA,62
2021,DEU,81
2022,FRA,63
2022,DEU,83
```

These numbers illustrate the format. Periods are sorted numerically; input rows
may be unordered. Optional columns `name`, `cluster`, and `parent` have the same
meaning as in ordinary inputs. Use `--time-column` or `--value-column` to select
other column names. Period labels need not be consecutive, but must be integers.
The exporter does not infer missing observations or interpolate a statistical
time series.

```bash
country-cartogram-timeline series.csv --config style.json --out output/history
```

The first period starts geographically. Every subsequent period starts from the
preceding **audited endpoint**. Inputs, codes, groups, and period coverage are
validated before the first simulation. Every period gets a `.layout.json`,
`.audit.json`, `.metrics.csv`, and selected figures. `--formats svg` avoids PNG
work when vector files and interactive playback suffice.

The generated files include:

| File | Purpose |
| --- | --- |
| `timeline.html` | Offline SVG player with play/pause, scrubbing, period navigation, and country hover |
| `timeline.audit.json` | Normalization, input hash, continuation provenance, per-period audits and solver timings |
| `2020.layout.json`, etc. | Reusable per-period checkpoints with polygons and anchor state |
| `2020.svg` / `.png`, etc. | Per-period figures according to `--formats` |
| `inputs/2020.csv`, `.json`, etc. | Exact per-period input and configuration used |
| `timeline.gif` | Optional (`--gif`) slideshow of audited period endpoints |

The HTML plays actual simulation states captured during resizing and settling;
it does not morph unrelated independently packed maps. The camera is fixed over
the union of all displayed frames, and colors remain consistent. Intermediate
frames are solver states between observations, not additional observed years.
The GIF intentionally shows period endpoints only; use the HTML for transitions.

## Choose the normalization

The default, `--normalization fixed`, uses

```text
area_per_unit = configured total_area / sum(first-period values)
country_area(period) = area_per_unit * country_value(period)
```

The same quantity therefore occupies the same area in every period. The total
land area may grow or shrink. Fixing the reference to the first period also
means appending future periods does not change earlier results.

`--normalization shares` retains the same total area in every period:

```text
country_area(period) = total_area * value(period) / sum(period values)
```

In that mode, a country whose value stays constant can shrink when other values
grow. The viewer's hover values display shares. Select the normalization based
on whether you intend to compare absolute quantities or relative shares.

## Append observations from a checkpoint

Do not recompute earlier years when new data arrive:

```bash
country-cartogram-timeline new_observations.csv \
  --initial-layout output/history/2022.layout.json --initial-time 2022 \
  --config style.json --out output/new-observations
```

This also accepts a CSV with just one new period. The checkpoint is the first
visible frame and starts the next simulation directly. Fixed normalization
inherits the checkpoint's area-per-unit factor. In shares mode, the checkpoint
total area must equal the configured `total_area`. Keep quantity units consistent.
`--initial-time` only labels the checkpoint; it does not modify data. The original
series is left in its existing output directory.

## Performance and constraints

Continuation skips geographic projection, coastline simplification, and island
repacking. It uniformly resizes the exact retained shapes from the checkpoint
and initializes bodies at their saved positions. It adapts the number of resizing
stages to the largest absolute log area ratio: small changes use as few as four,
while large changes retain the configured `growth_steps` budget. Unchanged areas
skip resizing stages. Collision resolution and the original settling tolerance
remain active; there is no loosened audit or convergence threshold.

The browser receives one SVG path per country plus compact arrays of positions
and scales for each captured frame. The engine does not reconstruct or retain
full coastline geometry for these timeline frames. HTML playback only changes
SVG transforms; it needs no Python server or external JavaScript library.
Static rendering is separate. GIF export is optional.

The current continuation implementation requires a stable cohort with **strictly
positive quantities** in every period. Adding/removing countries, crossing zero,
changing clusters, or changing boundary topology requires an explicitly new
layout. Negative and missing quantities are rejected. This avoids inventing a
minimum footprint, dropping observations silently, or presenting a geographic
reset as continuity.

Warm starts encourage temporal continuity but do not guarantee small movements
or faster convergence for every dataset. Large changes can trigger contact
rearrangements. Layouts are path-dependent: a continued result need not match a
freshly initialized result for the same final values. The audit records elapsed
physics time, actual resizing stages, settling iterations, convergence, and mean
and maximum displacement from the initial positions. Check these along with the
area/gap audit when interpreting time-series motion. Transient solver frames are
not individually certified by the final geometry audit.

The included `population-timeline.csv` is a synthetic 2020–2022 scenario built
from historical Natural Earth population attributes and deterministic invented
growth rates. The labels are illustrative; it is not observed demographic history.

Observed results for this 232-country scenario on the development machine:

| Period | Initialization | Physics time | Resize stages | Settling steps | Mean country movement |
| --- | --- | ---: | ---: | ---: | ---: |
| 2020 | Geographic | 28.73 s | 80 | 4,432 | 23.83 chart units |
| 2021 | Previous map | 3.25 s | 4 | 730 | 0.50 chart units |
| 2022 | Previous map | 2.21 s | 4 | 168 | 0.33 chart units |

All three converged with `position_tolerance = 0.002`, had no overlapping pairs,
and passed the configured gap and area checks. Times exclude figure rendering
and file export. These are an initial solve and two small-change updates, not
identical workloads; other datasets can behave differently. The HTML contains
16 captured states and is 1.46 MB. See the [recorded audit](gallery/population-timeline.audit.json)
and [interactive example](gallery/population-timeline.html) (download and open locally).
