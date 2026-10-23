"""Rigid-body contact engine for polygon cartograms (Pymunk/Chipmunk)."""

from __future__ import annotations
import math
import time
import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPoint
from shapely.geometry.polygon import orient
import pymunk
import pymunk.autogeometry
from .progressive import GrowthShapes


def convex_pieces(polygon):
    """Partition the exact simplified outline, retaining concavities and holes."""
    if not polygon.interiors:
        points = list(orient(polygon, 1).exterior.coords)
        pieces = [
            Polygon(p) for p in pymunk.autogeometry.convex_decomposition(points, 1e-10)
        ]
        if (
            pieces
            and shapely.union_all(pieces).symmetric_difference(polygon).area
            <= max(polygon.area, 1) * 1e-9
        ):
            return [
                np.asarray(p.exterior.coords)[:-1] for p in pieces if p.area > 1e-14
            ]
    triangles = shapely.constrained_delaunay_triangles(polygon)
    return [
        np.asarray(p.exterior.coords)[:-1] for p in triangles.geoms if p.area > 1e-14
    ]


def contact_envelope(vertices):
    """Convex polygon + square: conservative padding without rounded contacts.

    Return support vertices and corner directions separately so resizing scales
    the region while keeping its contact gap fixed in chart units. Minkowski
    sums keep this topology for every positive uniform scale and padding.
    """
    corners = np.array([[-1, -1], [-1, 1], [1, -1], [1, 1]], dtype=float)
    points = (vertices[:, None, :] + corners[None, :, :]).reshape(-1, 2)
    lookup = {tuple(point): (i // 4, i % 4) for i, point in enumerate(points)}
    hull = orient(MultiPoint(points).convex_hull, 1)
    indices = np.array(
        [lookup[tuple(point)] for point in list(hull.exterior.coords)[:-1]]
    )
    return vertices[indices[:, 0]], corners[indices[:, 1]]


def layout_physics(
    final_geometries,
    anchors,
    rows,
    metadata,
    config,
    capture=False,
    initial_state=None,
    frame_sink=None,
    collision_cache=None,
):
    started = time.monotonic()
    continuing = initial_state is not None
    growth = GrowthShapes(
        final_geometries, metadata, config["total_area"], initial_state
    )
    owners = growth.owners
    region_masses = growth.target_areas
    masses = growth.part_areas
    anchors = (
        np.asarray(initial_state["anchors"])
        if continuing
        else np.asarray(anchors) * growth.map_scale
    )
    start_positions = (
        np.asarray(initial_state["part_positions"])
        if continuing
        else anchors[owners] + growth.offsets
    )
    space = pymunk.Space()
    space.gravity = (0, 0)
    space.damping = config["damping"]
    space.iterations = config["contact_iterations"]
    space.collision_slop = 0.001
    # Correct penetration gradually: full correction can make crowded islands
    # oscillate. The final polygon audit independently enforces the requested gap.
    space.collision_bias = 0.01
    bodies, fixtures = [], []
    # A small numerical margin makes the final true-polygon gap at least the
    # requested one despite the engine's contact slop.
    region_padding = config["region_gap"] / 2 + 0.01
    group_padding = max(config["region_gap"], config["group_gap"]) / 2 + 0.01
    group_ids = {
        name: i + 1 for i, name in enumerate(sorted({r["group"] for r in rows}))
    }
    separate_groups = group_padding > region_padding and len(group_ids) > 1
    group_fixture_count = 0
    maximum_factors = np.sqrt(
        np.maximum(growth.initial_areas, growth.target_areas) / growth.target_areas
    )
    max_outline_error = 0.0
    cache_hits = 0
    for body_index, (outline, i, part_index) in enumerate(
        zip(growth.parts, owners, growth.indices)
    ):
        body = pymunk.Body(float(masses[body_index]), math.inf)
        body.position = tuple(start_positions[body_index])
        space.add(body)
        bodies.append(body)
        # A timeline retains the same normalized shapes. Hull fixtures can
        # be rescaled from the first solve without repeating GEOS/decomposition.
        key = (rows[i]["id"], part_index)
        factor = growth.final_scales[i]
        cached = (
            collision_cache.get(key)
            if collision_cache is not None and config["collision_mode"] == "hull"
            else None
        )
        if cached is not None:
            pieces, normalized_error = cached
            pieces = [v * factor for v in pieces]
            error = normalized_error * factor
            margin = 0.0
            cache_hits += 1
        else:
            tolerance = config["collision_tolerance"] / maximum_factors[i]
            collision_outline = (
                outline.convex_hull
                if config["collision_mode"] == "hull"
                else outline.simplify(tolerance, preserve_topology=True)
            )
            error = float(
                outline.boundary.hausdorff_distance(collision_outline.boundary)
            )
            margin = 0.0 if config["collision_mode"] == "hull" else error
            pieces = convex_pieces(collision_outline)
            if collision_cache is not None and config["collision_mode"] == "hull":
                collision_cache[key] = (
                    [v / factor for v in pieces],
                    error / factor,
                )
        max_outline_error = max(max_outline_error, error)
        # A convex hull already contains the fine outline. Only the
        # simplification mode needs an additional error allowance.
        for vertices in pieces:
            support, directions = contact_envelope(vertices)
            contacts = [(region_padding, pymunk.ShapeFilter(categories=1, mask=1))]
            if separate_groups:
                contacts.append(
                    (
                        group_padding,
                        pymunk.ShapeFilter(
                            categories=2, mask=2, group=group_ids[rows[i]["group"]]
                        ),
                    )
                )
                group_fixture_count += 1
            # The wider envelope only collides across groups. Explicit
            # vertices avoid zero-normal rounded contacts on tiny islands.
            for padding, filter_ in contacts:
                poly = pymunk.Poly(
                    body, (support + directions * (padding + margin)).tolist()
                )
                poly.filter = filter_
                poly.friction = 0
                poly.elasticity = 0
                space.add(poly)
                fixtures.append(
                    (body_index, i, support, margin, padding, directions, poly)
                )
    fixture_build_seconds = time.monotonic() - started
    print(
        f"  physics: {len(fixtures)} convex pieces for {len(bodies)} landmasses in {len(rows)} regions",
        flush=True,
    )
    memberships = [np.flatnonzero(owners == i) for i in range(len(rows))]
    group_names = sorted({r["group"] for r in rows})
    region_groups = np.array([group_names.index(r["group"]) for r in rows])
    group_masses = np.bincount(region_groups, weights=region_masses)
    by_code = {r["id"]: i for i, r in enumerate(rows)}
    final_anchors = anchors.copy()
    links = []
    for i, row in enumerate(rows):
        if row.get("parent") in by_code:
            j = by_code[row["parent"]]
            original = anchors[i] - anchors[j]
            distance = np.linalg.norm(original)
            compact = original * min(
                1, config["territory_distance"] / max(distance, 1e-12)
            )
            links.append((i, j, original, compact))
            final_anchors[i] = anchors[j] + compact
    frames, trace = [], []

    def positions():
        return np.asarray([tuple(b.position) for b in bodies])

    def centers(p):
        return np.column_stack(
            [
                np.bincount(owners, weights=masses * p[:, axis], minlength=len(rows))
                / region_masses
                for axis in (0, 1)
            ]
        )

    def resize(progress):
        factors = growth.factors(progress)
        for body_index, i, vertices, error, padding, directions, poly in fixtures:
            poly.unsafe_set_vertices(
                (
                    vertices * factors[i] + directions * (padding + error * factors[i])
                ).tolist()
            )
        for body in bodies:
            space.reindex_shapes_for_body(body)

    def capture_frame(progress, phase="growth"):
        if capture or frame_sink is not None:
            p = positions()
        if frame_sink is not None:
            # A timeline only needs per-landmass transforms. Avoid constructing
            # and retaining coastlines for every captured simulation step.
            frame_sink(
                {
                    "progress": float(progress),
                    "phase": phase,
                    "positions": centers(p),
                    "part_positions": p.copy(),
                    "areas": (1 - progress) * growth.initial_areas
                    + progress * growth.target_areas,
                }
            )
        if capture:
            frames.append(
                {
                    "progress": float(progress),
                    "positions": centers(p),
                    "part_positions": p.copy(),
                    "geometries": growth.at(progress, p),
                }
            )

    def advance(progress):
        previous = positions()
        region_centers = centers(previous)
        anchor_targets = (
            final_anchors
            if continuing
            else (1 - progress) * anchors + progress * final_anchors
        )
        group_centers = np.column_stack(
            [
                np.bincount(
                    region_groups, weights=region_masses * region_centers[:, axis]
                )
                / group_masses
                for axis in (0, 1)
            ]
        )
        region_acceleration = config["anchor_pull"] * (
            anchor_targets - region_centers
        ) + config["group_pull"] * (group_centers[region_groups] - region_centers)
        acceleration = region_acceleration[owners] + config["part_pull"] * (
            1 if continuing else progress
        ) * (region_centers[owners] - previous)
        forces = masses[:, None] * acceleration
        for child, parent, original, compact in links:
            offset = (
                compact
                if continuing
                else (1 - progress) * original + progress * compact
            )
            reduced_mass = (
                region_masses[child]
                * region_masses[parent]
                / (region_masses[child] + region_masses[parent])
            )
            force = (
                config["parent_pull"]
                * (1 if continuing else progress)
                * reduced_mass
                * (region_centers[parent] + offset - region_centers[child])
            )
            for region, direction in ((child, 1), (parent, -1)):
                indices = memberships[region]
                forces[indices] += (
                    direction * force * masses[indices, None] / region_masses[region]
                )
        for i, body in enumerate(bodies):
            body.force = tuple(forces[i])
            body.angular_velocity = 0
        space.step(config["physics_dt"])
        movements = np.linalg.norm(positions() - previous, axis=1)
        advance.worst = rows[owners[int(np.argmax(movements))]]["id"]
        return float(np.max(movements))

    resize(0)
    capture_frame(0)
    # Exactly coincident bodies have no contact direction. Seed a tiny,
    # deterministic motion after capturing the unchanged initial map.
    coincident = {}
    for i, body in enumerate(bodies):
        coincident.setdefault(tuple(body.position), []).append(i)
    for indices in coincident.values():
        if len(indices) < 2:
            continue
        angles = np.arange(len(indices)) * (2 * math.pi / len(indices))
        velocities = np.column_stack((np.cos(angles), np.sin(angles))) * 0.001
        velocities -= np.average(velocities, axis=0, weights=masses[indices])
        for i, velocity in zip(indices, velocities):
            bodies[i].velocity = tuple(velocity)
    steps = config["growth_steps"]
    if continuing:
        change = float(
            np.max(np.abs(np.log(growth.target_areas / growth.initial_areas)))
        )
        # Small annual changes need fewer resize/reindex stages. Large changes
        # retain the full budget; settling still uses the same stopping rule.
        steps = (
            0
            if change < 1e-12
            else min(steps, max(4, math.ceil(steps * min(1.0, change))))
        )
    for step in range(1, steps + 1):
        u = step / steps
        progress = u * u * (3 - 2 * u)
        resize(progress)
        for _ in range(config["relax_per_step"]):
            movement = advance(progress)
        if step % max(1, steps // 20) == 0 or step == steps:
            capture_frame(progress)
            trace.append({"progress": progress, "max_step_movement": movement})
        if step == 1 or step % max(1, steps // 5) == 0:
            print(
                f"  growth {progress:.0%}; elapsed {time.monotonic() - started:.1f}s",
                flush=True,
            )
    stable_steps, converged = 0, False
    for settling in range(config["settle_steps"]):
        movement = advance(1.0)
        stable_steps = (
            stable_steps + 1 if movement <= config["position_tolerance"] else 0
        )
        if stable_steps >= 15:
            converged = True
            break
        if settling and settling % 200 == 0:
            print(
                f"  settling {settling}; max movement {movement:.5f} ({advance.worst})",
                flush=True,
            )
            if frame_sink is not None:
                capture_frame(1.0, "settling")
    capture_frame(1.0, "settled")
    p = positions()
    placed = growth.at(1.0, p)
    return (
        placed,
        centers(p),
        {
            "body_count": len(bodies),
            "region_count": len(rows),
            "part_pull": config["part_pull"],
            "method": "continued_growth_rigid_body_physics"
            if continuing
            else "growth_rigid_body_physics",
            "engine": "Pymunk/Chipmunk",
            "engine_version": pymunk.version,
            "convex_piece_count": len(fixtures),
            "group_fixture_count": group_fixture_count,
            "cached_collision_part_count": cache_hits,
            "collision_vertex_count": sum(len(f[2]) for f in fixtures),
            "fixture_build_seconds": fixture_build_seconds,
            "collision_mode": config["collision_mode"],
            "collision_tolerance": config["collision_tolerance"],
            "maximum_final_collision_outline_error": max_outline_error,
            "growth_steps": steps,
            "relax_per_step": config["relax_per_step"],
            "settling_iterations": settling + 1,
            "equilibrium_converged": converged,
            "max_final_step_movement": movement,
            "position_tolerance": config["position_tolerance"],
            "physics_dt": config["physics_dt"],
            "initial_map_scale": growth.map_scale,
            "growth_trace": trace,
            "elapsed_seconds": time.monotonic() - started,
            "mean_displacement_from_start": float(
                np.mean(np.linalg.norm(p - start_positions, axis=1))
            ),
            "max_displacement_from_start": float(
                np.max(np.linalg.norm(p - start_positions, axis=1))
            ),
        },
        frames,
    )
