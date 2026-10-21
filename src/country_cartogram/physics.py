"""Rigid-body contact engine for geographic growth cartograms (Pymunk/Chipmunk)."""
from __future__ import annotations
import math
import time
import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.affinity import translate
import pymunk
import pymunk.autogeometry
from .progressive import GrowthShapes


def convex_pieces(polygon):
    """Partition the exact simplified outline, retaining concavities and holes."""
    if not polygon.interiors:
        points = list(orient(polygon, 1).exterior.coords)
        pieces = [Polygon(p) for p in pymunk.autogeometry.convex_decomposition(points, 1e-10)]
        if pieces and shapely.union_all(pieces).symmetric_difference(polygon).area <= max(polygon.area, 1) * 1e-9:
            return [np.asarray(p.exterior.coords)[:-1] for p in pieces if p.area > 1e-14]
    triangles = shapely.constrained_delaunay_triangles(polygon)
    return [np.asarray(p.exterior.coords)[:-1] for p in triangles.geoms if p.area > 1e-14]


def layout_physics(final_geometries, anchors, rows, metadata, config, capture=False,
                   initial_state=None, frame_sink=None):
    started = time.monotonic()
    continuing = initial_state is not None
    if continuing:
        growth = GrowthShapes.from_previous(final_geometries, metadata, initial_state['areas'],
                                             initial_state['geographic_scale'])
        anchors = np.asarray(initial_state['anchors'])
        start_positions = np.asarray(initial_state['positions'])
    else:
        growth = GrowthShapes(final_geometries, metadata, config['total_area'])
        anchors = np.asarray(anchors) * growth.geographic_scale
        start_positions = anchors
    masses = growth.target_areas
    space = pymunk.Space()
    space.gravity = (0, 0)
    space.damping = config['damping']
    space.iterations = config['contact_iterations']
    space.collision_slop = .001
    bodies, fixtures = [], []
    # A small numerical margin makes the final true-polygon gap at least the
    # requested one despite the engine's contact slop.
    radius = config['country_gap'] / 2 + .01
    maximum_factors = np.sqrt(np.maximum(growth.initial_areas, growth.target_areas) / growth.target_areas)
    max_outline_error = 0.
    for i, parts in enumerate(growth.parts):
        body = pymunk.Body(float(masses[i]), math.inf)
        body.position = tuple(start_positions[i])
        space.add(body); bodies.append(body)
        for part_index, (outline, _, _) in enumerate(parts):
            tolerance = config['collision_tolerance'] / maximum_factors[i]
            collision_outline = (outline.convex_hull if config['collision_mode'] == 'hull'
                                 else outline.simplify(tolerance, preserve_topology=True))
            error = float(outline.boundary.hausdorff_distance(collision_outline.boundary))
            max_outline_error = max(max_outline_error, error)
            # A convex hull already contains the fine outline. Only the
            # simplification mode needs an additional error allowance.
            margin = 0. if config['collision_mode'] == 'hull' else error
            for vertices in convex_pieces(collision_outline):
                poly = pymunk.Poly(body, vertices.tolist(), radius=radius + margin)
                poly.friction = 0; poly.elasticity = 0
                space.add(poly)
                fixtures.append((i, part_index, vertices, margin, poly))
    print(f'  physics: {len(fixtures)} convex pieces for {len(bodies)} countries', flush=True)
    groups = [np.array([i for i, r in enumerate(rows) if r['cluster'] == name])
              for name in sorted({r['cluster'] for r in rows})]
    by_code = {r['country_code']: i for i, r in enumerate(rows)}
    final_anchors = anchors.copy()
    links = []
    for i, row in enumerate(rows):
        if row.get('parent') in by_code:
            j = by_code[row['parent']]
            original = anchors[i] - anchors[j]
            distance = np.linalg.norm(original)
            compact = original * min(1, config['territory_distance'] / max(distance, 1e-12))
            links.append((i, j, original, compact))
            final_anchors[i] = anchors[j] + compact
    frames, trace = [], []

    def positions():
        return np.asarray([tuple(b.position) for b in bodies])

    def resize(progress):
        areas = (1 - progress) * growth.initial_areas + progress * growth.target_areas
        factors = np.sqrt(areas / growth.target_areas)
        for i, part_index, vertices, error, poly in fixtures:
            _, geographic_offset, final_offset = growth.parts[i][part_index]
            offset = ((1 - progress) * geographic_offset * growth.final_scales[i] + progress * final_offset) * factors[i]
            # Explicitly driven quasi-static resizing; this is not a material
            # expansion model. Reindex the broad phase after changing vertices.
            poly.unsafe_set_vertices((vertices * factors[i] + offset).tolist())
            poly.unsafe_set_radius(radius + error * factors[i])
        for body in bodies:
            space.reindex_shapes_for_body(body)

    def capture_frame(progress, phase='growth'):
        if capture or frame_sink is not None:
            p = positions()
        if frame_sink is not None:
            # A timeline only needs per-country transforms. Avoid constructing
            # and retaining coastlines for every captured simulation step.
            frame_sink({'progress': float(progress), 'phase': phase, 'positions': p.copy(),
                        'areas': (1 - progress) * growth.initial_areas + progress * growth.target_areas})
        if capture:
            frames.append({'progress': float(progress), 'positions': p,
                           'geometries': [translate(g, *xy) for g, xy in zip(growth.at(progress), p)]})

    def advance(progress):
        previous = positions()
        anchor_targets = final_anchors if continuing else (1 - progress) * anchors + progress * final_anchors
        acceleration = config['geographic_pull'] * (anchor_targets - previous)
        for indices in groups:
            center = np.average(previous[indices], axis=0, weights=masses[indices])
            acceleration[indices] += config['cluster_pull'] * (center - previous[indices])
        forces = masses[:, None] * acceleration
        for child, parent, original, compact in links:
            offset = compact if continuing else (1 - progress) * original + progress * compact
            reduced_mass = masses[child] * masses[parent] / (masses[child] + masses[parent])
            force = config['parent_pull'] * (1 if continuing else progress) * reduced_mass * (previous[parent] + offset - previous[child])
            forces[child] += force; forces[parent] -= force
        for i, body in enumerate(bodies):
            body.force = tuple(forces[i])
            body.angular_velocity = 0
        space.step(config['physics_dt'])
        movements = np.linalg.norm(positions() - previous, axis=1)
        advance.worst = rows[int(np.argmax(movements))]['country_code']
        return float(np.max(movements))

    resize(0); capture_frame(0)
    steps = config['growth_steps']
    if continuing:
        change = float(np.max(np.abs(np.log(growth.target_areas / growth.initial_areas))))
        # Small annual changes need fewer resize/reindex stages. Large changes
        # retain the full budget; settling still uses the same stopping rule.
        steps = 0 if change < 1e-12 else min(steps, max(4, math.ceil(steps * min(1., change))))
    for step in range(1, steps + 1):
        u = step / steps
        progress = u * u * (3 - 2 * u)
        resize(progress)
        for _ in range(config['relax_per_step']):
            movement = advance(progress)
        if step % max(1, steps // 20) == 0 or step == steps:
            capture_frame(progress)
            trace.append({'progress': progress, 'max_step_movement': movement})
        if step == 1 or step % max(1, steps // 5) == 0:
            print(f'  growth {progress:.0%}; elapsed {time.monotonic() - started:.1f}s', flush=True)
    stable_steps, converged = 0, False
    for settling in range(config['settle_steps']):
        movement = advance(1.)
        stable_steps = stable_steps + 1 if movement <= config['position_tolerance'] else 0
        if stable_steps >= 15:
            converged = True; break
        if settling and settling % 200 == 0:
            print(f'  settling {settling}; max movement {movement:.5f} ({advance.worst})', flush=True)
            if frame_sink is not None:
                capture_frame(1., 'settling')
    capture_frame(1., 'settled')
    p = positions()
    placed = [translate(g, *xy) for g, xy in zip(final_geometries, p)]
    return placed, p, {
        'method': 'continued_growth_rigid_body_physics' if continuing else 'geographic_growth_rigid_body_physics',
        'engine': 'Pymunk/Chipmunk',
        'engine_version': pymunk.version, 'convex_piece_count': len(fixtures),
        'collision_mode': config['collision_mode'],
        'collision_tolerance': config['collision_tolerance'],
        'maximum_final_collision_outline_error': max_outline_error,
        'growth_steps': steps, 'relax_per_step': config['relax_per_step'],
        'settling_iterations': settling + 1, 'equilibrium_converged': converged,
        'max_final_step_movement': movement, 'position_tolerance': config['position_tolerance'],
        'physics_dt': config['physics_dt'], 'initial_geographic_scale': growth.geographic_scale,
        'growth_trace': trace, 'elapsed_seconds': time.monotonic() - started,
        'mean_displacement_from_start': float(np.mean(np.linalg.norm(p - start_positions, axis=1))),
        'max_displacement_from_start': float(np.max(np.linalg.norm(p - start_positions, axis=1))),
    }, frames
