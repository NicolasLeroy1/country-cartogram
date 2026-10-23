# Development

```bash
python -m pip install -e '.[dev]'
ruff check src tests scripts
ruff format --check src tests scripts
python -m unittest discover -s tests -v
python -m build
```

CI tests Python 3.10 and 3.12, builds a wheel, and checks its geography and CLI
entry points. Generated work belongs in the ignored `output/` directory.
`docs/gallery/` contains the selected examples linked from the documentation.

## Source layout

| Files | Responsibility |
| --- | --- |
| `maps.py`, `values.py` | Map adapters, projection, CSV validation |
| `configuration.py`, `geometry.py` | Settings, shape approximation, part placement |
| `progressive.py`, `physics.py` | Growth shapes, forces, collision solving |
| `continuation.py`, `timeline.py` | Checkpoints and ordered observations |
| `pipeline.py` | Shared simulation and export orchestration |
| `rendering.py`, `evolution.py`, `restyle.py` | Static figures, simulation GIFs, restyling |
| `extrusion.py`, `viewer.py` | Meshes, numerical checks, compressed viewer data |
| `web/viewer.js`, `web/viewer.css`, `web/viewer.html`, `web/surface.glsl` | Browser controls, layout, Perlin normals |

Python files are in `src/country_cartogram/`. Browser files are in its `web/`
directory. The minified Three.js bundle is generated; edit the authored viewer
source instead. Rebuild the engine with `python scripts/build_viewer.py`.
That command requires Node/npm; normal package installation does not.

Keep CLI parsing in `main()` and reusable work in `run()`, `restyle()`,
`run_timeline()`, or `export_3d()`. The commands and Python API should call the
same implementation. Saved layout geometry is shared by restyling, continuation,
and extrusion; avoid separate preparation paths for each renderer.

Format the browser source with Prettier 3.6.2:

```bash
npx prettier@3.6.2 --write src/country_cartogram/web/viewer.{js,css,html} scripts/check_{translucency,timeline}.cjs
```

## Numerical changes

For solver changes, check area error, overlapping pairs, minimum polygon gap,
settling iterations, and convergence. Test approximation on holes and islands
before timing it. Vertex reductions can change the contact path and settling
work, so report measured timings rather than an assumed speed ratio.

For continuation, check the first captured state against the previous endpoint,
retained geographic anchors, independent landmass positions, fixed area scale,
and checkpoint restarts. Check that internal attraction is stronger than group
attraction and that the viewer counts each region's quantity once. Cached
hulls must match uncached results. For meshes, check concavities, holes, islands,
zero heights, closure, and measured volumes.

## Browser changes

With Playwright installed in a separate Node environment:

```bash
NODE_PATH=/path/to/node_modules node scripts/check_translucency.cjs output/population-gdp.3d.html
NODE_PATH=/path/to/node_modules node scripts/check_timeline.cjs output/gdp-1985-2024/timeline.html
```

Set `CHROME_PATH` if needed. The check compares opaque and translucent pixels,
reverses region draw order, tests opacity across camera changes, and checks
shader errors and network requests. Also inspect playback, hover, group filters,
mobile layout, and PNG downloads. Browser **Save image** captures the translucent
material; Matplotlib previews use opaque materials.
The timeline check verifies continuous movement, area/height encodings, year
selection, grey estimates, and offline operation.

`scripts/prepare_examples.py` regenerates example CSVs and configurations from
the bundled attributes. It leaves the geography's retrieval metadata alone.
The manual GitHub Actions workflow renders complete examples. Benchmark 3D
export separately from physics with `scripts/benchmark_3d.py`; it retains only
the benchmark JSON and removes temporary exports.

`scripts/prepare_gdp_timeline.py` regenerates the 40-year GDP example offline
from its pinned World Bank snapshot. Use `--refresh` only when intentionally
updating the source observations; provenance records hashes and retrieval time.

Custom-map tests cover local coordinates, holes and islands, national projections,
single-period playback, continuation, and 3D reuse. Keep map adapters in `maps.py`;
the solver and rendering code must not interpret source-specific properties.
