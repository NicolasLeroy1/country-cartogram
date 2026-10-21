"""Geometry and deterministic rigid-body packing. All distances are chart units."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPolygon, shape
from shapely.affinity import translate, scale
from pyproj import Transformer

PROJECT = Transformer.from_crs(4326, 8857, always_xy=True, force_over=True)


def project_xy(lon, lat):
    x, y = PROJECT.transform(lon, lat)
    return np.array([x, y], dtype=float) / 100_000


def polygon_parts(geom):
    if geom.geom_type == 'Polygon':
        return [geom] if geom.area > 0 else []
    if hasattr(geom, 'geoms'):
        return [p for g in geom.geoms for p in polygon_parts(g)]
    return []


def prepare_country(geojson, target_area, *, min_part_fraction=.001,
                    max_parts=24, simplify=.0015, satellite_distance=1.4):
    """Uniformly scale retained parts; only translate satellites, never warp them.

    Remote part offsets are compressed radially. Dropped tiny islands are
    accounted for in the returned metadata, and retained area is renormalized.
    """
    source = polygon_parts(shape(geojson))
    if not source:
        raise ValueError('Country has no polygon geometry')
    # Longitude unwrapping makes dateline fragments local to the main part.
    main_lon = max(source, key=lambda p: p.area).centroid.x
    parts = []
    for part in source:
        shift = round((main_lon - part.centroid.x) / 360) * 360
        if shift:
            part = translate(part, xoff=shift)
        part = shapely.transform(part, lambda xy: np.column_stack(
            PROJECT.transform(xy[:, 0], xy[:, 1])) / 100_000)
        parts.extend(polygon_parts(shapely.make_valid(part)))
    parts.sort(key=lambda p: -p.area)
    original_area = sum(p.area for p in parts)
    main_anchor = np.array(parts[0].centroid.coords[0])
    retained = [p for p in parts if p.area / original_area >= min_part_fraction][:max_parts]
    if not retained:
        retained = parts[:1]
    retained_area = sum(p.area for p in retained)
    tol = math.sqrt(original_area) * simplify
    retained = [p.simplify(tol, preserve_topology=True) for p in retained]
    factor = math.sqrt(target_area / sum(p.area for p in retained))
    main = retained[0]
    main_center = np.array(main.centroid.coords[0])
    local_parts = []
    part_info = []
    main_radius = math.sqrt(main.area / math.pi) * factor
    for index, part in enumerate(retained):
        center = np.array(part.centroid.coords[0])
        local = scale(translate(part, -center[0], -center[1]), factor, factor, origin=(0, 0))
        offset = (center - main_center) * factor
        distance = np.linalg.norm(offset)
        if index and distance > 0:
            direction = offset / distance
            radius = math.sqrt(local.area / math.pi)
            distance = min(distance, satellite_distance * (main_radius + radius))
            # Move only along the original direction until parts are disjoint.
            piece_gap = .025 * max(main_radius, .1)
            for _ in range(500):
                offset = direction * distance
                placed = translate(local, *offset)
                if all(placed.distance(other) >= piece_gap for other in local_parts):
                    break
                distance += max(piece_gap, .05 * (main_radius + radius))
            else:
                raise RuntimeError('Could not separate satellite parts')
        placed = translate(local, *offset)
        local_parts.append(placed)
        part_info.append({'source_center': center.tolist(), 'offset': offset.tolist(),
                          'area': placed.area})
    result = MultiPolygon(local_parts)
    if not result.is_valid:
        raise ValueError('Invalid country geometry after part packing')
    return result, main_anchor, {
        'source_part_count': len(parts), 'retained_part_count': len(retained),
        'retained_source_area_fraction': retained_area / original_area,
        'scale_factor': factor, 'parts': part_info,
    }


@dataclass
class Hull:
    points: np.ndarray
    axes: np.ndarray
    radius: float


def hull_data(poly):
    points = np.asarray(poly.convex_hull.exterior.coords)[:-1]
    edges = np.roll(points, -1, axis=0) - points
    axes = np.column_stack((-edges[:, 1], edges[:, 0]))
    axes /= np.linalg.norm(axes, axis=1)[:, None]
    return Hull(points, axes, float(np.max(np.linalg.norm(points, axis=1))))


def pair_data(a, b):
    axes = np.vstack((a.axes, b.axes))
    ap, bp = a.points @ axes.T, b.points @ axes.T
    return axes, ap.min(0), ap.max(0), bp.min(0), bp.max(0)


def separation_vector(pair, relative_position, gap):
    """Exact separating-axis translation for convex collision envelopes."""
    axes, amin, amax, bmin, bmax = pair
    displacement = axes @ relative_position
    positive = bmax - amin - displacement + gap
    negative = amax + displacement - bmin + gap
    if np.any(positive <= 0) or np.any(negative <= 0):
        return None
    ip, im = np.argmin(positive), np.argmin(negative)
    if positive[ip] < negative[im]:
        return axes[ip] * (positive[ip] + 1e-7)
    return -axes[im] * (negative[im] + 1e-7)


def pack_rigid(polys, anchors, *, gap=.35, iterations=250, geographic_pull=.015,
               cohesion=.003, damping=.7, masses=None, links=(), parent_pull=.04):
    """Spring relaxation plus position-based collision constraints.

    Convex envelopes deliberately sacrifice some packing density to guarantee
    that concave coastlines and multipart countries cannot overlap. The final
    cleanup turns springs off. Failure is explicit; no overlap is hidden.
    """
    n = len(polys)
    anchors = np.asarray(anchors, dtype=float)
    positions = anchors.copy()
    velocity = np.zeros_like(positions)
    hulls = [hull_data(p) for p in polys]
    radii = np.array([h.radius for h in hulls])
    masses = np.sqrt(np.array([p.area for p in polys])) if masses is None else np.asarray(masses)
    inverse_mass = 1 / np.maximum(masses, 1e-8)
    cache = {}
    total_constraints = 0

    def project_constraints(sweeps=1):
        nonlocal total_constraints
        count = 0
        for _ in range(sweeps):
            d = positions[:, None, :] - positions[None, :, :]
            near = np.sum(d * d, axis=2) < (radii[:, None] + radii[None, :] + gap) ** 2
            pairs = np.argwhere(np.triu(near, 1))
            count = 0
            for i, j in pairs:
                key = (int(i), int(j))
                if key not in cache:
                    cache[key] = pair_data(hulls[i], hulls[j])
                correction = separation_vector(cache[key], positions[i] - positions[j], gap)
                if correction is None:
                    continue
                weight = inverse_mass[i] / (inverse_mass[i] + inverse_mass[j])
                positions[i] += correction * weight
                positions[j] -= correction * (1 - weight)
                count += 1
            total_constraints += count
            if not count:
                break
        return count

    for iteration in range(iterations):
        # Cooling makes the last iterations settle instead of vibrating.
        cooling = 1 - .9 * iteration / max(iterations - 1, 1)
        center = np.average(positions, axis=0, weights=masses)
        force = geographic_pull * (anchors - positions) + cohesion * (center - positions)
        for child, parent, offset in links:
            pull = parent_pull * (positions[parent] + offset - positions[child])
            fraction = inverse_mass[child] / (inverse_mass[child] + inverse_mass[parent])
            force[child] += pull * fraction
            force[parent] -= pull * (1 - fraction)
        velocity = damping * velocity + cooling * force
        lengths = np.linalg.norm(velocity, axis=1)
        velocity *= np.minimum(1, 1.5 / np.maximum(lengths, 1e-12))[:, None]
        positions += velocity
        project_constraints(2)
    for cleanup in range(2000):
        if project_constraints() == 0:
            break
    else:
        raise RuntimeError('Collision solver did not converge; increase geographic_spread or reduce gap')
    return positions, {'iterations': iterations, 'cleanup_iterations': cleanup,
                       'collision_constraints_applied': total_constraints}


def layout_countries(geometries, anchors, rows, config):
    """Pack within clusters, then pack entire clusters around geographic anchors."""
    groups = sorted({r['cluster'] for r in rows})
    global_positions = np.zeros((len(rows), 2))
    cluster_shapes, cluster_targets, cluster_indices, stats = [], [], [], {}
    values = np.array([r['value'] for r in rows])
    by_code = {r['country_code']: i for i, r in enumerate(rows)}
    for group in groups:
        indices = [i for i, r in enumerate(rows) if r['cluster'] == group]
        subset = [geometries[i] for i in indices]
        raw = np.array([anchors[i] for i in indices])
        # Explicit parent links get a compact offset in their original direction.
        for k, i in enumerate(indices):
            parent = rows[i].get('parent', '')
            if parent in by_code:
                j = by_code[parent]
                vector = anchors[i] - anchors[j]
                norm = np.linalg.norm(vector)
                if norm > 0:
                    raw[k] = anchors[j] + vector / norm * min(norm, config['territory_distance'])
        center = np.average(raw, axis=0, weights=np.sqrt(values[indices]))
        raw -= center
        extent = max(np.sqrt(np.mean(np.sum(raw * raw, axis=1))), 1)
        desired = np.sqrt(sum(g.area for g in subset) / np.pi)
        local_anchors = raw * desired / extent * config['geographic_spread']
        local_by_code = {rows[i]['country_code']: k for k, i in enumerate(indices)}
        links = [(k, local_by_code[rows[i]['parent']],
                  local_anchors[k] - local_anchors[local_by_code[rows[i]['parent']]])
                 for k, i in enumerate(indices) if rows[i].get('parent') in local_by_code]
        positions, info = pack_rigid(subset, local_anchors, gap=config['country_gap'],
                                    iterations=config['iterations'],
                                    geographic_pull=config['geographic_pull'],
                                    cohesion=config['cluster_pull'], damping=config['damping'],
                                    links=links, parent_pull=config['parent_pull'])
        positions -= np.average(positions, axis=0, weights=values[indices])
        global_positions[indices] = positions
        placed = [translate(p, *xy) for p, xy in zip(subset, positions)]
        cluster_shapes.append(shapely.union_all(placed))
        cluster_indices.append(indices)
        target = config.get('cluster_anchors', {}).get(group)
        cluster_targets.append(project_xy(*target) if target else center)
        stats[group] = info
    cluster_offsets, info = pack_rigid(cluster_shapes, cluster_targets,
                                      gap=config['cluster_gap'], iterations=config['iterations'],
                                      geographic_pull=.03, cohesion=0, damping=config['damping'])
    for indices, offset in zip(cluster_indices, cluster_offsets):
        global_positions[indices] += offset
    placed = [translate(p, *xy) for p, xy in zip(geometries, global_positions)]
    return placed, global_positions, {'countries': stats, 'clusters': info}
