"""Shared visual and solver configuration, without loading renderers."""

import colorsys
import hashlib
import json
import math
from pathlib import Path
from matplotlib.colors import to_rgb, to_hex
from .maps import MAP_DEFAULTS, Projection

DEFAULTS = {
    "title": "Cartogram",
    **MAP_DEFAULTS,
    "subtitle": "Area represents quantity; color represents group",
    "source_note": "User-supplied quantities",
    "value_label": "Quantity",
    "prefix": "",
    "divisor": 1,
    "suffix": "",
    "decimals": 1,
    "total_area": 10000,
    "region_gap": 0.2,
    "group_gap": 3.6,
    "anchor_pull": 0.002,
    "group_pull": 0.006,
    "part_pull": 0.03,
    "growth_steps": 80,
    "relax_per_step": 20,
    "settle_steps": 12000,
    "position_tolerance": 0.002,
    "physics_dt": 0.04,
    "contact_iterations": 200,
    "collision_tolerance": 0.12,
    "collision_mode": "hull",
    "damping": 0.7,
    "territory_distance": 12,
    "parent_pull": 0.04,
    "min_part_fraction": 0.001,
    "max_parts": 24,
    "simplify": 0.0015,
    "shape_error": 0.02,
    "labels": 14,
    "label_regions": [],
    "label_offsets": {},
    "label_aliases": {},
    "background": "#edf8fa",
    "ink": "#10243a",
    "colors": {
        "North America": "#1679b7",
        "South America": "#38a36d",
        "Europe": "#8b4cb0",
        "Africa": "#edc54f",
        "Asia": "#e55261",
        "Oceania": "#f29b45",
    },
}


def load_config(path=None, *, source_crs=None, projection=None):
    config = json.loads(json.dumps(DEFAULTS))
    if path:
        supplied = json.loads(Path(path).read_text())
        if not isinstance(supplied, dict):
            raise ValueError("Configuration must be a JSON object")
        unknown = set(supplied) - set(config)
        if unknown:
            raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
        for key, value in supplied.items():
            if key == "colors":
                config[key].update(value)
            else:
                config[key] = value
    for key in (
        "total_area",
        "divisor",
        "territory_distance",
        "position_tolerance",
        "physics_dt",
    ):
        if (
            not isinstance(config[key], (float, int))
            or not math.isfinite(config[key])
            or config[key] <= 0
        ):
            raise ValueError(f"{key} must be finite and positive")
    for key in (
        "region_gap",
        "group_gap",
        "anchor_pull",
        "group_pull",
        "part_pull",
        "parent_pull",
        "simplify",
        "min_part_fraction",
        "collision_tolerance",
    ):
        if (
            not isinstance(config[key], (float, int))
            or not math.isfinite(config[key])
            or config[key] < 0
        ):
            raise ValueError(f"{key} must be finite and non-negative")
    for key in (
        "max_parts",
        "labels",
        "decimals",
        "growth_steps",
        "relax_per_step",
        "settle_steps",
        "contact_iterations",
    ):
        if not isinstance(config[key], int) or config[key] < (
            0 if key in ("labels", "decimals") else 1
        ):
            raise ValueError(f"{key} has an invalid integer value")
    if config["collision_mode"] not in ("hull", "decomposed"):
        raise ValueError("collision_mode must be hull or decomposed")
    if (
        isinstance(config["shape_error"], bool)
        or not isinstance(config["shape_error"], (int, float))
        or not math.isfinite(config["shape_error"])
        or not 0 <= config["shape_error"] <= 0.1
    ):
        raise ValueError("shape_error must be between 0 and 0.1")
    if not 0 <= config["damping"] < 1 or not 0 <= config["min_part_fraction"] < 1:
        raise ValueError("damping and min_part_fraction must be in [0, 1)")
    if config["group_pull"] > 0 and config["part_pull"] <= config["group_pull"]:
        raise ValueError(
            "part_pull must exceed group_pull when group attraction is enabled"
        )
    for color in [config["background"], config["ink"], *config["colors"].values()]:
        to_rgb(color)
    if source_crs is not None:
        config["source_crs"] = source_crs
    if projection is not None:
        config["projection"] = projection
    config.update(Projection(config["source_crs"], config["projection"]).settings)
    return config


def region_color(row, config):
    if row.get("value_status", "observed") != "observed":
        return "#a0a5a9"
    digest = int(hashlib.sha256(row["id"].encode()).hexdigest()[:8], 16)
    group = row["group"]
    if group in config["colors"]:
        base = to_rgb(config["colors"][group])
    else:
        hue = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) / 2**32
        base = colorsys.hls_to_rgb(hue, 0.52, 0.5)
    hue, lightness, saturation = colorsys.rgb_to_hls(*base)
    return to_hex(
        colorsys.hls_to_rgb(
            hue,
            min(0.75, max(0.25, lightness + (digest / 2**32 - 0.5) * 0.17)),
            saturation,
        )
    )
