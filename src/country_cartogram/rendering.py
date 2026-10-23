"""Static figures from prepared planar regions."""

import math
import textwrap
import numpy as np
import shapely
from shapely.geometry import box
from shapely.ops import polylabel
from shapely.geometry.polygon import orient
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.colors import to_rgb

from .geometry import polygon_parts
from .configuration import region_color


def add_geometry(ax, geom, color):
    for part in polygon_parts(geom):
        part = orient(part, sign=1)
        vertices, codes = [], []
        for ring in [part.exterior, *part.interiors]:
            xy = np.asarray(ring.coords)
            vertices.extend(xy)
            codes.extend(
                [MplPath.MOVETO]
                + [MplPath.LINETO] * (len(xy) - 2)
                + [MplPath.CLOSEPOLY]
            )
        ax.add_patch(
            PathPatch(
                MplPath(np.asarray(vertices), codes),
                facecolor=color,
                edgecolor="none",
                linewidth=0,
            )
        )


def formatted(value, config):
    value /= config["divisor"]
    return f"{config['prefix']}{value:,.{config['decimals']}f}{config['suffix']}"


def render(geometries, rows, config, stem, formats, dpi, *, boundary_label=""):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "text.usetex": False,
        }
    )
    bg, ink = config["background"], config["ink"]
    fig = plt.figure(figsize=(16, 10), facecolor=bg)
    fig.text(0.045, 0.94, config["title"], fontsize=29, weight="bold", color=ink)
    fig.text(0.046, 0.904, config["subtitle"], fontsize=13, color="#4d6576")
    fig.text(
        0.954,
        0.943,
        "CARTOGRAM",
        fontsize=9,
        weight="bold",
        ha="right",
        color="#5a7585",
    )
    ax = fig.add_axes([0.035, 0.24, 0.93, 0.62], facecolor=bg)
    for g, row in zip(geometries, rows):
        add_geometry(ax, g, region_color(row, config))
    bounds = shapely.total_bounds(geometries)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    ax.set_xlim(bounds[0] - width * 0.055, bounds[2] + width * 0.055)
    ax.set_ylim(bounds[1] - height * 0.09, bounds[3] + height * 0.09)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    ranked = sorted(range(len(rows)), key=lambda i: (-rows[i]["value"], rows[i]["id"]))
    selected = list(
        dict.fromkeys(
            ranked[: config["labels"]]
            + [i for i, r in enumerate(rows) if r["id"] in config["label_regions"]]
        )
    )
    label_boxes, omitted_labels = [], []
    all_geometry = shapely.union_all(geometries)
    for i in selected:
        row, geom = rows[i], geometries[i]
        part = max(polygon_parts(geom), key=lambda p: p.area)
        point = polylabel(part, tolerance=max(math.sqrt(part.area) * 0.01, 0.001))
        name = config["label_aliases"].get(row["id"], row["name"])
        label = (
            "\n".join(textwrap.wrap(name, 15)) + "\n" + formatted(row["value"], config)
        )
        fontsize = min(
            17, max(8.5, 8.5 + 11 * math.sqrt(row["value"] / rows[ranked[0]]["value"]))
        )
        offset = config["label_offsets"].get(row["id"])
        candidates = [(0, 0, fontsize, True), (0, 0, 9, True), (0, 0, 7.5, True)]
        if offset:
            candidates = [(offset[0], offset[1], 9, False)]
        else:
            unit = math.sqrt(part.area) * 0.6 + 3
            candidates += [
                (dx * unit * distance, dy * unit * distance, 8.5, False)
                for distance in (1, 1.5, 2.2, 3)
                for dx, dy in [
                    (1, 0),
                    (-1, 0),
                    (0, 1),
                    (0, -1),
                    (1, 1),
                    (-1, 1),
                    (1, -1),
                    (-1, -1),
                ]
            ]
        placed = False
        for dx, dy, size, inside in candidates:
            rgb = to_rgb(region_color(row, config))
            text_color = (
                "white"
                if inside and np.dot(rgb, [0.2126, 0.7152, 0.0722]) < 0.61
                else ink
            )
            text = ax.text(
                point.x + dx,
                point.y + dy,
                label,
                fontsize=size,
                color=text_color,
                ha="center",
                va="center",
                weight="bold" if inside else "normal",
                linespacing=1.15,
                zorder=4,
            )
            bb = text.get_window_extent(renderer).expanded(1.09, 1.09)
            corners = ax.transData.inverted().transform(
                [[bb.x0, bb.y0], [bb.x1, bb.y1]]
            )
            shape_box = box(*corners[0], *corners[1])
            valid = not any(bb.overlaps(other) for other in label_boxes)
            if inside:
                valid = valid and part.contains(shape_box)
            else:
                valid = valid and not all_geometry.intersects(shape_box)
            valid = (
                valid
                and ax.bbox.contains(bb.x0, bb.y0)
                and ax.bbox.contains(bb.x1, bb.y1)
            )
            if valid:
                if not inside:
                    ax.annotate(
                        "",
                        xy=(point.x, point.y),
                        xytext=(point.x + dx, point.y + dy),
                        arrowprops={"arrowstyle": "-", "lw": 0.65, "color": "#6e8795"},
                        zorder=2,
                    )
                label_boxes.append(bb)
                placed = True
                break
            text.remove()
        if not placed:
            omitted_labels.append(row["id"])

    # Bottom panels: ranked quantities, region key, and reading guide.
    fig.add_artist(
        plt.Line2D(
            [0.045, 0.955],
            [0.222, 0.222],
            transform=fig.transFigure,
            color="#c9dde3",
            lw=1,
        )
    )
    fig.text(0.045, 0.192, "LARGEST QUANTITIES", fontsize=10, weight="bold", color=ink)
    for rank, i in enumerate(ranked[:10]):
        col, line = divmod(rank, 5)
        x, y = 0.045 + col * 0.225, 0.165 - line * 0.025
        row = rows[i]
        fig.text(x, y, f"{rank + 1:02d}", fontsize=8.7, color="#758b97")
        fig.text(x + 0.021, y, row["name"], fontsize=9, color=ink)
        fig.text(
            x + 0.205,
            y,
            formatted(row["value"], config),
            fontsize=9,
            weight="bold",
            ha="right",
            color=ink,
        )
    fig.text(0.52, 0.192, "GROUPS", fontsize=10, weight="bold", color=ink)
    groups = sorted({r["group"] for r in rows})
    for j, group in enumerate(groups[:12]):
        col, line = divmod(j, 6)
        x, y = 0.52 + col * 0.15, 0.162 - line * 0.02
        row = next(r for r in rows if r["group"] == group)
        fig.add_artist(
            Rectangle(
                (x, y),
                0.009,
                0.011,
                transform=fig.transFigure,
                facecolor=config["colors"].get(group, region_color(row, config)),
                edgecolor="none",
            )
        )
        fig.text(x + 0.014, y, group, fontsize=8.2, color=ink)
    fig.text(0.79, 0.192, "HOW TO READ", fontsize=10, weight="bold", color=ink)
    fig.text(
        0.79,
        0.162,
        "Area = quantity\nShapes retain their outlines\nNearby hues = same group\nPositions are approximate",
        fontsize=9,
        color="#4d6576",
        linespacing=1.65,
        va="top",
    )
    note = config["source_note"] + f" | {len(rows)} regions / territories shown."
    fig.text(
        0.045, 0.028, "\n".join(textwrap.wrap(note, 160)), fontsize=7.8, color="#68808c"
    )
    fig.text(
        0.955,
        0.028,
        boundary_label,
        ha="right",
        fontsize=7.8,
        color="#68808c",
    )
    for fmt in formats:
        fig.savefig(
            stem.with_suffix("." + fmt),
            dpi=dpi,
            facecolor=bg,
            metadata={"Creator": "Country Cartogram"}
            if fmt in ("svg", "pdf")
            else None,
        )
    plt.close(fig)
    return omitted_labels
