"""Compact, offline Three.js viewers. Geometry is uploaded once, frames are transforms."""

from __future__ import annotations

import base64
import gzip
from html import escape
import json
from pathlib import Path

import numpy as np
from shapely.affinity import translate
from shapely.geometry import shape

THREE_VERSION = "0.186.1"
WEB = Path(__file__).with_name("web")


def binary(array, dtype):
    source = np.asarray(array)
    with np.errstate(over="ignore", under="ignore", invalid="ignore", divide="ignore"):
        packed = np.asarray(source, dtype=dtype)
        if dtype == "<f4":
            error = np.abs(packed.astype(float) - source) / np.maximum(
                np.abs(source), 1e-300
            )
            if not np.all(np.isfinite(packed)) or np.any(error > 0.02):
                raise ValueError(
                    "Float32 display values exceed the 2% error budget; change input units"
                )
    return base64.b64encode(packed.tobytes()).decode("ascii")


def mesh_payload(vertices, faces, roof_faces):
    # Audit the actual float32 display mesh, independently of the source audit.
    # Rendering is allowed a 2% area/volume error; keep local coordinates to
    # avoid cancellation for small islands far from the map origin.
    display = np.asarray(vertices, dtype="<f4").astype(float)

    def measures(v):
        roof = v[roof_faces]
        area = (
            np.linalg.norm(
                np.cross(roof[:, 1] - roof[:, 0], roof[:, 2] - roof[:, 0]), axis=1
            ).sum()
            / 2
        )
        triangles = (v - v.mean(axis=0))[faces]
        volume = (
            np.einsum(
                "ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])
            ).sum()
            / 6
        )
        return np.asarray([area, volume])

    expected = measures(vertices)
    relative = np.abs(measures(display) / expected - 1)
    if (
        not np.all(np.isfinite(display))
        or not np.all(np.isfinite(relative))
        or np.any(relative > 0.02)
    ):
        raise ValueError("Float32 display mesh exceeds the 2% area/volume error budget")
    dtype = "<u2" if len(vertices) <= 65536 else "<u4"
    return {
        "vertices": binary(vertices, "<f4"),
        "faces": binary(faces, dtype),
        "roofs": binary(roof_faces, dtype),
        "indexBytes": 2 if dtype == "<u2" else 4,
        "displayAreaError": float(relative[0]),
        "displayVolumeError": float(relative[1]),
    }


def write_document(payload, path, *, html_js="inline"):
    if html_js not in ("inline", "cdn"):
        raise ValueError("html_js must be inline or cdn")
    has_estimates = any(
        status != "observed"
        for statuses in payload.get("valueStatuses", {}).values()
        for status in statuses
    )
    payload = {**payload, "hasEstimates": has_estimates}
    first_statuses = payload.get("valueStatuses", {}).get(
        str(payload.get("frames", [{"year": ""}])[0]["year"]),
        ["observed"] * len(payload["regions"]),
    )
    data = base64.b64encode(
        gzip.compress(
            json.dumps(payload, separators=(",", ":"), allow_nan=False).encode(),
            mtime=0,
        )
    ).decode("ascii")
    cfg = payload["config"]
    labels = [cfg[k] for k in ("area_label", "height_label", "volume_label")]
    if payload["mode"] == "2d":
        labels = labels[:1]
    rows = "".join(
        '<tr><th scope="row">'
        + escape(c["name"])
        + "</th><td>"
        + escape(c["id"])
        + "</td>"
        + "".join(
            f"<td>{v:,.6g}</td>" for v in payload["observations"][0][i][: len(labels)]
        )
        + (
            "<td>" + escape(first_statuses[i].replace("_", " ")) + "</td>"
            if has_estimates
            else ""
        )
        + "</tr>"
        for i, c in enumerate(payload["regions"])
    )
    html = (WEB / "viewer.html").read_text(encoding="utf-8")
    replacements = {
        "TITLE": escape(cfg["title"]),
        "STYLES": "<style>"
        + (WEB / "viewer.css").read_text(encoding="utf-8")
        + "</style>",
        "SOURCE": escape(cfg["source_note"]),
        "DATA": data,
        "HEADINGS": "".join(
            '<th scope="col">' + escape(label) + "</th>" for label in labels
        )
        + ('<th scope="col">Value status</th>' if has_estimates else ""),
        "ROWS": rows,
        "CARDS": "".join(
            "<div><small>"
            + role
            + "</small><strong>"
            + escape(label)
            + "</strong></div>"
            for role, label in zip(
                ("AREA", "HEIGHT · SECOND ÷ FIRST", "VOLUME"), labels
            )
        ),
    }
    viewer = (
        (WEB / "viewer.js")
        .read_text(encoding="utf-8")
        .replace("@@SURFACE@@", (WEB / "surface.glsl").read_text(encoding="utf-8"))
    )
    if html_js == "inline":
        scripts = (
            "<script>"
            + (WEB / "three.min.js").read_text(encoding="utf-8")
            + "</script><script>"
            + viewer
            + "</script>"
        )
    else:
        imports = {
            "imports": {
                "three": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js",
                "three/addons/": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/",
            }
        }
        scripts = (
            '<script type="importmap">'
            + json.dumps(imports)
            + '</script><script type="module">'
            + "import * as Engine from 'three'; import {OrbitControls} from 'three/addons/controls/OrbitControls.js';\n"
            + "const CartogramThree={...Engine,OrbitControls};\n"
            + viewer
            + "</script>"
        )
    # One substitution pass: user text cannot become a template instruction.
    import re

    replacements["SCRIPTS"] = scripts
    html = re.sub(r"@@([A-Z]+)@@", lambda m: replacements[m[1]], html)
    Path(path).write_text(html, encoding="utf-8")


def write_static_viewer(regions, report, path, *, html_js="inline"):
    from .extrusion import extrude_roof

    items, transforms = [], []
    for c in regions:
        vertices = c["vertices"].copy()
        center = (vertices[:, :2].min(axis=0) + vertices[:, :2].max(axis=0)) / 2
        vertices[:, :2] -= center
        if c["height"] > 0:
            vertices[:, 2] /= c["height"]
            # Roofs precede floors and walls in extrude_polygon.
            roofs = c["faces"][np.all(vertices[c["faces"], 2] == 1, axis=1)]
            faces = c["faces"]
        else:
            vertices, faces, roofs = extrude_roof(vertices[:, :2], c["faces"], 1.0)
        items.append(
            {
                "id": c["id"],
                "name": c["name"],
                "group": c["group"],
                "color": c["color"],
                **mesh_payload(vertices, faces, roofs),
            }
        )
        transforms.append([*center, 1.0, c["height"]])
    payload = {
        "regions": items,
        "transforms": binary([transforms], "<f4"),
        "frameCount": 1,
        "frames": [{"year": "", "phase": "settled", "progress": 1}],
        "endpoints": [0],
        "observations": [
            [[c["area_value"], c["ratio"], c["volume_value"]] for c in regions]
        ],
        "valueStatuses": {"": [c["value_status"] for c in regions]},
        "valueNotes": {"": [c["value_note"] for c in regions]},
        "config": report["config"],
        "bounds": report["extent"],
        "maxHeight": report["max_height"],
        "heightScale": report["height_scale"],
        "mode": "3d",
        "normalization": "fixed",
    }
    write_document(payload, path, html_js=html_js)
    report["display"] = {
        "relative_error_budget": 0.02,
        "max_relative_area_error": max(c["displayAreaError"] for c in items),
        "max_relative_volume_error": max(c["displayVolumeError"] for c in items),
    }


def write_timeline_viewer(
    base,
    frames,
    endpoints,
    config,
    normalization,
    path,
    *,
    quantities,
    config_3d=None,
    html_js="inline",
    has_volume=False,
    value_statuses=None,
    value_notes=None,
):
    from .configuration import region_color
    from .extrusion import DEFAULTS_3D, audit_mesh, extrude_polygon

    cfg = {
        **DEFAULTS_3D,
        "title": config["title"],
        "source_note": config["source_note"],
        **(config_3d or {}),
    }
    if not has_volume:
        cfg.update(
            area_label=config["value_label"], height_label="Flat map", volume_label="—"
        )
    regions, local_bounds, base_areas = [], [], []
    max_volume_error = 0.0
    for row in base["regions"]:
        local = translate(shape(row["geometry"]), *-np.asarray(row["position"]))
        base_areas.append(local.area)
        local_bounds.append(local.bounds)
        vertices, faces, roofs = extrude_polygon(local, 1.0)
        audit = audit_mesh(vertices, faces, roofs, local, 1.0)
        max_volume_error = max(max_volume_error, audit["mesh_relative_volume_error"])
        regions.append(
            {
                "id": row["id"],
                "name": row["name"],
                "group": row["group"],
                "color": region_color({**row, "value_status": "observed"}, config),
                **mesh_payload(vertices, faces, roofs),
            }
        )
    limits, transforms, observations = [], [], []
    for frame in frames:
        scales, positions = np.asarray(frame["scales"]), np.asarray(frame["positions"])
        bounds = np.asarray(local_bounds) * scales[:, None] + np.tile(positions, (1, 2))
        limits.append([*bounds[:, :2].min(axis=0), *bounds[:, 2:].max(axis=0)])
        current = np.asarray(quantities[frame["year"]])
        previous = np.asarray(quantities.get(frame.get("from"), current))
        current = previous * (1 - frame["progress"]) + current * frame["progress"]
        a, b = current[:, 0], current[:, 1]
        if normalization == "shares":
            # Match the solver's interpolated shares using one period total.
            areas = np.asarray(base_areas) * scales**2
            a = areas / areas.sum() * a.sum()
        ratios = b / a
        observations.append(np.column_stack((a, ratios, b)).tolist())
        transforms.append(np.column_stack((positions, scales, ratios)))
    limits = np.asarray(limits)
    bounds = [*limits[:, :2].min(axis=0), *limits[:, 2:].max(axis=0)]
    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    max_ratio = max(float(t[:, 3].max()) for t in transforms)
    height_scale = cfg["height_scale"] or (
        span * 0.25 / max_ratio if max_ratio else 1.0
    )
    for t in transforms:
        t[:, 3] *= height_scale
    if not np.isfinite(transforms).all():
        raise ValueError(
            "Timeline transforms overflow; change quantity units or height_scale"
        )
    payload = {
        "regions": regions,
        "transforms": binary(transforms, "<f4"),
        "frameCount": len(frames),
        "frames": [
            {k: v for k, v in f.items() if k not in ("positions", "scales")}
            for f in frames
        ],
        "endpoints": endpoints,
        "observations": observations,
        "valueStatuses": {
            str(year): statuses for year, statuses in (value_statuses or {}).items()
        },
        "valueNotes": {str(year): notes for year, notes in (value_notes or {}).items()},
        "config": cfg,
        "bounds": bounds,
        "maxHeight": max_ratio * height_scale,
        "heightScale": height_scale,
        "mode": "3d" if has_volume else "2d",
        "normalization": normalization,
    }
    write_document(payload, path, html_js=html_js)
    return [float(v) for v in bounds], {
        "renderer": "threejs",
        "threejs_version": THREE_VERSION,
        "height_scale": height_scale,
        "geometry_count": len(regions),
        "mesh_triangle_count": sum(
            len(base64.b64decode(c["faces"])) // c["indexBytes"] // 3 for c in regions
        ),
        "max_unit_mesh_volume_error": max_volume_error,
        "max_display_relative_area_error": max(c["displayAreaError"] for c in regions),
        "max_display_relative_volume_error": max(
            c["displayVolumeError"] for c in regions
        ),
        "relative_error_budget": 0.02,
        "frame_storage": "gzip + float32 transforms; one mesh per region",
        "display_interpolation": "Continuous positions and areas between captured states; calendar pacing independent of solver frame count",
    }
