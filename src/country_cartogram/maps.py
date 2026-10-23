"""Boundary adapters and projection. The simulation receives planar polygons."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
import shapely
from shapely.affinity import translate
from shapely.geometry import shape, MultiPolygon

DEFAULT_BOUNDARIES = Path(__file__).with_name("data") / "countries.geojson"
MAP_DEFAULTS = {"source_crs": "EPSG:4326", "projection": "EPSG:8857"}


def region_id(value):
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError("Every region needs a nonempty id")
    return str(value).strip().upper()


def natural_earth(feature):
    """Translate Natural Earth attributes into the shared region record."""
    p = feature["properties"]
    if p["CONTINENT"] in ("Antarctica", "Seven seas (open ocean)"):
        return None
    code = p["ISO_A3_EH"] if p["ISO_A3_EH"] != "-99" else p["ADM0_A3"]
    if p["ADM0_A3"] in ("ATC", "IOA"):
        code = p["ADM0_A3"]
    names = {
        "USA": "United States",
        "CHN": "China",
        "RUS": "Russia",
        "KOR": "South Korea",
        "PRK": "North Korea",
        "GBR": "United Kingdom",
        "COD": "DR Congo",
        "COG": "Congo",
        "TWN": "Taiwan",
    }
    return {
        "id": code,
        "name": names.get(code, p["NAME_EN"]),
        "group": p["CONTINENT"],
        "parent": "",
        "geometry": feature["geometry"],
        "properties": p,
    }


def load_catalog(boundaries):
    """Read polygon features with id, optional name, group and parent properties.

    Natural Earth files are adapted automatically. IDs are case-insensitive and stored in uppercase.
    """
    collection = json.loads(Path(boundaries).read_text(encoding="utf-8"))
    if (
        not isinstance(collection, dict)
        or collection.get("type") != "FeatureCollection"
        or not collection.get("features")
    ):
        raise ValueError("Boundaries must be a nonempty GeoJSON FeatureCollection")
    if not isinstance(collection["features"], list):
        raise ValueError("FeatureCollection features must be a list")
    catalog, sovereigns = {}, {}
    for feature in collection["features"]:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("Boundary records must be GeoJSON Features")
        p = feature.get("properties") or {}
        if not isinstance(p, dict):
            raise ValueError("Feature properties must be a JSON object")
        if "ISO_A3_EH" in p and "ADM0_A3" in p:
            row = natural_earth(feature)
            if row is None:
                continue
            sovereigns[row["id"]] = (p["ADMIN"], p["SOVEREIGNT"])
        else:
            code = region_id(feature.get("id", p.get("id")))
            row = {
                "id": code,
                "name": str(p.get("name") or code),
                "group": str(p.get("group") or "Regions"),
                "parent": region_id(p["parent"])
                if p.get("parent") is not None and str(p["parent"]).strip()
                else "",
                "geometry": feature.get("geometry"),
                "properties": p,
            }
        code = region_id(row["id"])
        if code in catalog:
            raise ValueError(f"Duplicate boundary region id: {code}")
        geometry = row["geometry"]
        if not isinstance(geometry, dict) or geometry.get("type") not in (
            "Polygon",
            "MultiPolygon",
        ):
            raise ValueError(
                f"{code}: boundaries require Polygon or MultiPolygon geometry"
            )
        catalog[code] = {**row, "id": code}
    names = {name: code for code, (name, _) in sovereigns.items()}
    for code, (_, sovereign) in sovereigns.items():
        parent = names.get(sovereign, "")
        if parent != code:
            catalog[code]["parent"] = parent
    if not catalog:
        raise ValueError("Boundary file has no included polygon regions")
    return catalog


class Projection:
    """Project geographic or projected input, or retain local coordinates."""

    def __init__(
        self,
        source_crs=MAP_DEFAULTS["source_crs"],
        projection=MAP_DEFAULTS["projection"],
    ):
        self.transformer = None
        self.geographic = False
        if projection == "local":
            self.settings = {"source_crs": None, "projection": "local"}
        else:
            try:
                source, target = (
                    CRS.from_user_input(source_crs),
                    CRS.from_user_input(projection),
                )
                self.transformer = Transformer.from_crs(
                    source, target, always_xy=True, force_over=True
                )
            except (ValueError, TypeError, ProjError) as exc:
                raise ValueError(f"Invalid map projection: {exc}") from None
            self.geographic = source.is_geographic
            self.settings = {
                "source_crs": source.to_string(),
                "projection": target.to_string(),
            }

    def geometry(self, geometry):
        geometry = shape(geometry)
        if (
            geometry.geom_type not in ("Polygon", "MultiPolygon")
            or geometry.is_empty
            or geometry.has_z
            or not np.isfinite(shapely.get_coordinates(geometry)).all()
        ):
            raise ValueError("Map regions require finite, nonempty 2D polygons")
        source = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
        main_x = max(source, key=lambda p: p.area).centroid.x
        parts = []
        for part in source:
            if self.geographic:
                # Keep dateline fragments beside the main part before projection.
                shift = round((main_x - part.centroid.x) / 360) * 360
                if shift:
                    part = translate(part, xoff=shift)
            if self.transformer is not None:
                part = shapely.transform(
                    part,
                    lambda xy: np.column_stack(
                        self.transformer.transform(xy[:, 0], xy[:, 1])
                    )
                    / 100_000,
                )
            if not np.isfinite(shapely.get_coordinates(part)).all():
                raise ValueError(
                    "Projection produced nonfinite coordinates; check the input CRS and extent"
                )
            repaired = shapely.make_valid(part)
            candidates = [repaired]
            while candidates:
                candidate = candidates.pop(0)
                if candidate.geom_type == "Polygon" and candidate.area > 0:
                    parts.append(candidate)
                elif hasattr(candidate, "geoms"):
                    candidates.extend(candidate.geoms)
        if not parts:
            raise ValueError("Map region has no positive polygon area after projection")
        return MultiPolygon(parts)


@dataclass
class BaseMap:
    catalog: dict
    sha256: str
    projection: Projection
    label: str


def load_map(
    path=DEFAULT_BOUNDARIES,
    *,
    source_crs=MAP_DEFAULTS["source_crs"],
    projection=MAP_DEFAULTS["projection"],
):
    path = Path(path)
    catalog = load_catalog(path)
    natural = all("ISO_A3_EH" in row["properties"] for row in catalog.values())
    return BaseMap(
        catalog,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        Projection(source_crs, projection),
        "Natural Earth boundaries" if natural else path.stem,
    )


def add_map_arguments(parser):
    """Shared options for static and timeline commands."""
    parser.add_argument(
        "--boundaries",
        type=Path,
        default=DEFAULT_BOUNDARIES,
        help="GeoJSON polygon regions (default: bundled world map)",
    )
    parser.add_argument("--source-crs", help="Input CRS (default: EPSG:4326)")
    parser.add_argument(
        "--projection",
        help="Output CRS, e.g. EPSG:3035, or local to keep input coordinates",
    )
