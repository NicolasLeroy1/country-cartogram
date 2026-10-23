# Country Cartogram

Turn a CSV of country quantities into a 2D cartogram: each country's area is
proportional to its value. Export PNG, SVG, or PDF, and keep the saved layout
to change the style or continue with later observations.

[![40 years of GDP](docs/gallery/gdp-1985-2024.png)](docs/gallery/gdp-1985-2024.html)

## Install and make a map

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
country-cartogram examples/gdp.csv --config examples/gdp.json --out output/gdp
```

Open `output/gdp.svg` or `output/gdp.png`. The run also writes a PDF, a layout,
metrics, and an audit of area accuracy, coverage, and country separation.
More examples: [static GDP](docs/gallery/gdp.png) · [population](docs/gallery/population.png) ·
[growth animation](docs/gallery/gdp.growth.gif).

For your own data, create `my_values.csv`:

```csv
country_code,value
FRA,42
DEU,60
ITA,35
```

```bash
country-cartogram my_values.csv --out output/my_map
```

Use the [country catalog](src/country_cartogram/data/country_catalog.csv) for
accepted codes. Values must be finite and non-negative; zero values are omitted.
Optional `name`, `cluster`, and `parent` columns set labels and grouping.
A configuration file is optional and only needs the settings you want to change:

```json
{
  "title": "My quantities by country",
  "source_note": "Source, units, and year",
  "decimals": 0
}
```

Pass it with `--config my_style.json`. Shapes are simplified **before simulation**
using a default 2% shape error budget; their areas are restored to preserve the
input proportions. See [usage](docs/usage.md) for settings and input rules.

## Reuse the work

Keep `.layout.json`, `.audit.json`, and `.metrics.csv` together. Use a new output
name to retain the original; commands overwrite files with the same name.

| Change | Workflow |
| --- | --- |
| Titles, colors, labels, or export format | `country-cartogram-restyle map.layout.json --config style.json --out output/restyled` |
| Quantities for a later observation | `country-cartogram later.csv --initial-layout map.layout.json --out output/later` |
| A whole time series | `country-cartogram-timeline history.csv --viewer-only --out output/history` |

Restyling uses the saved geometry without rerunning physics. Continuation runs
physics from the previous positions and requires compatible countries and
geometry settings. For comparable absolute areas across periods, use a timeline.
The [Python API recipes](docs/usage.md#use-from-python) use the same functions as
the commands.

## Showcase: 40 years of GDP

```bash
country-cartogram-timeline examples/gdp-1985-2024.csv \
  --config examples/gdp-1985-2024.json --normalization shares \
  --viewer-only --out output/gdp-1985-2024
```

Open `output/gdp-1985-2024/timeline.html` to follow 209 economies from 1985 to
2024. GDP is measured in constant 2015 US dollars; area shows each economy's
share of the included total. Present-day outlines and previous positions are
retained throughout. Small within-group gaps and wider between-group gaps help
keep the continents distinct.

Missing annual GDP values are interpolated or held from the nearest observation
**before simulation**. Grey countries identify estimates; hover or open the
values table to inspect their status. The
[data provenance](examples/gdp-1985-2024.provenance.json) lists the source,
estimation rules, code mapping, and economies with no usable GDP.

Playback moves continuously between captured states, with a fixed camera and
year-based pacing. Use the year selector to jump to an annual input. At 1×,
one year takes one second.
It is a self-contained HTML file that works offline. `--viewer-only` skips
per-period figures and retains checkpoints, metrics, and audits.
[Download the showcase](docs/gallery/gdp-1985-2024.html) to open locally.
See [time series](docs/time-series.md) for the CSV format and normalization.

Open the HTML file directly in your browser, or serve the repository locally:

```bash
python -m http.server 8765 --bind 127.0.0.1
```

Run that command from the repository root and keep its terminal open. Then open
[the local showcase](http://127.0.0.1:8765/docs/gallery/gdp-1985-2024.html).
The localhost link only works while the server is running.

## Experimental 3D

3D adds a second quantity: area shows the first, height shows second ÷ first,
and volume shows the second. Small footprints with large ratios become tall,
thin columns that can look like rendering artifacts and are hard to compare.
Transparency helps visibility, but this remains a limitation of the encoding.
Use 2D for the main presentation; the optional 3D viewer provides a top view
and a values table for inspection.

See the [3D guide](docs/3d.md) for export commands and the
[paired timeline example](docs/gallery/population-gdp-timeline.html) to download
and open locally. The engine and geography are bundled; no JavaScript setup is
needed to generate HTML.

| Guide | Contents |
| --- | --- |
| [Usage](docs/usage.md) | Inputs, settings, saved layouts, Python API |
| [Time series](docs/time-series.md) | Normalization, playback, checkpoints |
| [Experimental 3D](docs/3d.md) | Interpretation, materials, saved-layout export |
| [Algorithm](docs/algorithm.md) | Shape approximation, forces, collision checks |
| [Development](CONTRIBUTING.md) | Source organization, tests, browser checks, builds |

Static examples use historical Natural Earth attributes with mixed source
years. The population examples use invented growth rates. The 40-year GDP
showcase uses World Bank WDI observations with explicitly marked estimates.

Code and Three.js: MIT. Natural Earth geography: public domain. World Bank GDP:
[CC BY 4.0](https://data.worldbank.org/indicator/NY.GDP.MKTP.KD). See
[LICENSE](LICENSE) and [source metadata](src/country_cartogram/data/sources.json).
