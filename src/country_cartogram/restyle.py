#!/usr/bin/env python3
"""Re-render a saved layout without rerunning the packing simulation."""

import argparse
import json
from pathlib import Path
import shutil
from shapely.geometry import shape
from .configuration import DEFAULTS
from .pipeline import render

STYLE_KEYS = {
    "title",
    "subtitle",
    "source_note",
    "value_label",
    "prefix",
    "divisor",
    "suffix",
    "decimals",
    "labels",
    "label_countries",
    "label_offsets",
    "label_aliases",
    "background",
    "ink",
    "colors",
}


def restyle(
    layout_path,
    *,
    config_path,
    output,
    formats=("png", "svg", "pdf"),
    dpi=180,
):
    """Render a saved layout with visual overrides and return its updated audit.

    Keep the source .audit.json and .metrics.csv beside the .layout.json.
    ``output`` is a filename stem; existing outputs at that stem are replaced.
    Geometry and simulation state are copied unchanged, so the result remains
    usable as a checkpoint. Geometry settings in the overrides are rejected.
    """
    layout_path, output = Path(layout_path), Path(output)
    if not layout_path.name.endswith(".layout.json"):
        raise ValueError("Expected a .layout.json file produced by country-cartogram")
    if dpi < 50:
        raise ValueError("dpi must be at least 50")
    formats = tuple(formats)
    if not formats or set(formats) - {"png", "svg", "pdf"}:
        raise ValueError("formats must contain png, svg, or pdf")
    source_stem = layout_path.with_name(layout_path.name[: -len(".layout.json")])
    audit = json.loads(source_stem.with_suffix(".audit.json").read_text())
    overrides = json.loads(Path(config_path).read_text())
    if not isinstance(overrides, dict):
        raise ValueError("Style configuration must be a JSON object")
    bad = set(overrides) - STYLE_KEYS
    if bad:
        raise ValueError(
            f"Geometry settings require repacking with country-cartogram: {sorted(bad)}"
        )
    config = {**DEFAULTS, **audit["config"]}
    for key, value in overrides.items():
        if key == "colors":
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    if config["divisor"] <= 0:
        raise ValueError("divisor must be positive")
    rows = json.loads(layout_path.read_text())["countries"]
    output.parent.mkdir(parents=True, exist_ok=True)
    omitted = render(
        [shape(r["geometry"]) for r in rows],
        rows,
        config,
        output,
        formats,
        dpi,
    )
    audit["config"] = config
    audit["omitted_labels"] = omitted
    output.with_suffix(".audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    for suffix in (".layout.json", ".metrics.csv"):
        source, target = source_stem.with_suffix(suffix), output.with_suffix(suffix)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    print(f"Restyled {output}.*; omitted labels: {omitted}")
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "layout", type=Path, help="Saved .layout.json with companion audit and metrics"
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="JSON visual overrides"
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output filename stem, without extension",
    )
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument(
        "--formats",
        nargs="+",
        choices=["png", "svg", "pdf"],
        default=["png", "svg", "pdf"],
    )
    args = parser.parse_args()
    try:
        restyle(
            args.layout,
            config_path=args.config,
            output=args.out,
            formats=args.formats,
            dpi=args.dpi,
        )
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
