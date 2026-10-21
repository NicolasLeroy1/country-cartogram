# Development

Install an editable checkout and the build tool:

```bash
python -m pip install -e '.[dev,3d]'
python -m unittest discover -s tests -v
python -m build
```

Tests cover geometry invariants, input validation, geographic initialization,
cluster attraction during growth, enclave separation, and the installed CLI.
GitHub Actions runs the tests and packaging checks on Python 3.10 and 3.12.
The separate manual example workflow regenerates a full GDP or population map
and retains its exports as a workflow artifact.

To regenerate source examples from the bundled historical attributes:

```bash
python scripts/prepare_examples.py
country-cartogram examples/gdp.csv --config examples/gdp.json --out output/gdp --animate
country-cartogram examples/population.csv --config examples/population.json --out output/population
country-cartogram examples/population-gdp.csv --value-column population --volume-column gdp_usd \
  --config examples/population-gdp.json --config-3d examples/population-gdp.3d.json \
  --out output/population-gdp --preview-3d --html-js cdn
country-cartogram-timeline examples/population-timeline.csv \
  --config examples/population-timeline.json --out output/history --gif
```

The sample data are demonstrations, not up-to-date statistical estimates.
Inspect audits and figures before replacing files in `docs/gallery/`. Generated
outputs outside the curated gallery are ignored by Git.

When changing the solver, report area error, overlap pairs, minimum actual gap,
and whether the settling tolerance was reached. A visually pleasing layout
alone is not enough. Keep scientific data and presentation changes separable:
`country-cartogram-restyle` can update colors and labels without running physics.
`country-cartogram-3d` can update the second quantity without changing the saved
first-quantity layout. For extrusion changes, check concavities, holes, islands,
zero heights, watertightness, and independently measured mesh volumes. Run
`scripts/benchmark_3d.py` to measure the 3D stage without conflating it with physics.
For continuation changes, verify that the first captured state matches the
previous endpoint, the original geographic anchors survive multiple updates,
islands stay compacted, and one fixed area-per-unit factor survives both period
transitions and checkpoint restarts. Record solver timings and movement before
claiming a temporal stability or performance improvement.

Useful extension points and known tradeoffs are in
[the algorithm notes](docs/algorithm.md). Changes to the collision approximation
should include a concrete geometric regression case. Avoid tests that merely
duplicate the implementation.
