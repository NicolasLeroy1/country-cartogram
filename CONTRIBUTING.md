# Development

Install an editable checkout and the build tool:

```bash
python -m pip install -e '.[dev]'
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
```

The sample data are demonstrations, not up-to-date statistical estimates.
Inspect audits and figures before replacing files in `docs/gallery/`. Generated
outputs outside the curated gallery are ignored by Git.

When changing the solver, report area error, overlap pairs, minimum actual gap,
and whether the settling tolerance was reached. A visually pleasing layout
alone is not enough. Keep scientific data and presentation changes separable:
`country-cartogram-restyle` can update colors and labels without running physics.

Useful extension points and known tradeoffs are in
[the algorithm notes](docs/algorithm.md). Changes to the collision approximation
should include a concrete geometric regression case. Avoid tests that merely
duplicate the implementation.
