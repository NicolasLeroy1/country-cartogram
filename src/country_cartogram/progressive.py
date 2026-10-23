"""Geographic geometry interpolation used by the rigid-body growth engine."""

from __future__ import annotations
import math
import numpy as np
import shapely
from shapely.affinity import scale, translate
from shapely.geometry import MultiPolygon
from .geometry import polygon_parts


class GrowthShapes:
    """Construct all intermediate geometries from immutable final outlines."""

    def __init__(self, final_geometries, metadata, total_area):
        self.target_areas = np.array([g.area for g in final_geometries])
        self.final_scales = np.array([m["scale_factor"] for m in metadata])
        self.source_areas = self.target_areas / self.final_scales**2
        self.geographic_scale = math.sqrt(total_area / self.source_areas.sum())
        self.initial_areas = self.source_areas * self.geographic_scale**2
        self.final_geometries = final_geometries
        self.parts = []
        for geometry, detail in zip(final_geometries, metadata):
            main_center = np.array(detail["parts"][0]["source_center"])
            parts = []
            for part, info in zip(polygon_parts(geometry), detail["parts"]):
                final_offset = np.array(info["offset"])
                parts.append(
                    (
                        translate(part, *-final_offset),
                        np.array(info["source_center"]) - main_center,
                        final_offset,
                    )
                )
            self.parts.append(parts)

    @classmethod
    def from_previous(cls, final_geometries, metadata, initial_areas, geographic_scale):
        """Continue from compacted shapes; never unfold islands back to geography."""
        obj = cls.__new__(cls)
        obj.final_geometries = final_geometries
        obj.target_areas = np.array([g.area for g in final_geometries])
        obj.initial_areas = np.asarray(initial_areas, dtype=float)
        obj.final_scales = np.array([m["scale_factor"] for m in metadata])
        obj.source_areas = obj.target_areas / obj.final_scales**2
        obj.geographic_scale = geographic_scale
        obj.parts = []
        for geometry, detail, factor in zip(
            final_geometries, metadata, obj.final_scales
        ):
            parts = []
            for part, info in zip(polygon_parts(geometry), detail["parts"]):
                offset = np.asarray(info["offset"])
                # Both endpoints have the same normalized inset arrangement.
                parts.append((translate(part, *-offset), offset / factor, offset))
            obj.parts.append(parts)
        return obj

    def at(self, progress):
        if progress >= 1:
            return list(self.final_geometries)
        current_areas = (
            1 - progress
        ) * self.initial_areas + progress * self.target_areas
        factors = np.sqrt(current_areas / self.target_areas)
        geometries = []
        for i, parts in enumerate(self.parts):
            factor = factors[i]
            pieces = []
            for outline, geographic_offset, final_offset in parts:
                offset = (
                    (1 - progress) * geographic_offset * self.final_scales[i]
                    + progress * final_offset
                ) * factor
                pieces.append(
                    translate(scale(outline, factor, factor, origin=(0, 0)), *offset)
                )
            # Components can cross during their gradual inset movement. Union
            # is used only for rendering those intermediate states.
            geometry = MultiPolygon(pieces)
            geometries.append(
                geometry if geometry.is_valid else shapely.union_all(pieces)
            )
        return geometries
