"""Uniform area growth for independently positioned landmasses."""

import math
import numpy as np
import shapely
from shapely.affinity import scale, translate
from shapely.geometry import MultiPolygon
from .geometry import polygon_parts


class GrowthShapes:
    def __init__(self, final_geometries, metadata, total_area, initial_state=None):
        self.target_areas = np.array([g.area for g in final_geometries])
        self.final_scales = np.array([m["scale_factor"] for m in metadata])
        self.source_areas = self.target_areas / self.final_scales**2
        self.map_scale = (
            initial_state["map_scale"]
            if initial_state
            else math.sqrt(total_area / self.source_areas.sum())
        )
        self.initial_areas = (
            np.asarray(initial_state["areas"])
            if initial_state
            else self.source_areas * self.map_scale**2
        )
        self.parts, self.owners, self.indices, self.offsets = [], [], [], []
        for i, (geometry, detail) in enumerate(zip(final_geometries, metadata)):
            main = np.asarray(detail["parts"][0]["source_center"])
            for j, (part, info) in enumerate(
                zip(polygon_parts(geometry), detail["parts"])
            ):
                self.parts.append(translate(part, *-np.asarray(info["offset"])))
                self.owners.append(i)
                self.indices.append(j)
                self.offsets.append(
                    (np.asarray(info["source_center"]) - main) * self.map_scale
                )
        self.owners = np.asarray(self.owners)
        self.offsets = np.asarray(self.offsets)
        self.part_areas = np.array([part.area for part in self.parts])

    def factors(self, progress):
        return np.sqrt(
            ((1 - progress) * self.initial_areas + progress * self.target_areas)
            / self.target_areas
        )

    def at(self, progress, part_positions):
        factors = self.factors(progress)
        parts = [[] for _ in self.target_areas]
        for outline, owner, position in zip(self.parts, self.owners, part_positions):
            parts[owner].append(
                translate(
                    scale(outline, factors[owner], factors[owner], origin=(0, 0)),
                    *position,
                )
            )
        result = []
        for pieces in parts:
            geometry = MultiPolygon(pieces)
            result.append(
                geometry
                if geometry.is_valid or progress == 1
                else shapely.union_all(pieces)
            )
        return result
