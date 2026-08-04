"""Read UTM37N shapefiles into GeoJSON-like dicts for the map surface.

Ported from the standalone map viewer. Uses ``pyshp`` (pure Python, no
GDAL/geopandas -- the deployment target is a locked-down Windows box). Every
coordinate is passed through UNTOUCHED: the source data is already in UTM Zone
37N metres, which is exactly the plane the front-end canvas draws in, so there
is no reprojection anywhere in this module.

One layer = one ``.shp`` set (``.shp`` + ``.shx`` + ``.dbf``) in
``config.map_layers_dir()``; the file stem is the layer name. The country
outlines are a PSEUDO-layer: they are a prebuilt JSON file
(``config.map_borders_file()``), not a shapefile, and :func:`list_layers`
reports them first so the map always has its backdrop before anything else.

What does NOT belong here: Flask objects, SQL, project data. The wells overlay
(project coordinates) is a database read and lives in workflow/mapdata.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import shapefile  # pyshp

import config

# pyshp shapeType groups -> our simplified geometry family. The front-end only
# needs to know how to DRAW a feature (dot / stroke / fill), so the Z/M
# variants collapse into their 2D family.
_POINT_TYPES = {1, 11, 21, 8, 18, 28}       # Point / PointZ / PointM / MultiPoint*
_LINE_TYPES = {3, 13, 23}                    # PolyLine / PolyLineZ / PolyLineM
_POLY_TYPES = {5, 15, 25}                    # Polygon / PolygonZ / PolygonM

# Layer names are FILENAME fragments, so the vocabulary is deliberately narrow:
# no separators, no traversal, nothing a shell or a path join could reinterpret.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# The name reserved for the prebuilt country outlines. It is not a shapefile,
# so it never resolves through _safe_path -- the route serves the JSON file.
BORDERS_LAYER_NAME = "borders"


def _geom_family(shape_type) -> str:
    if shape_type in _POINT_TYPES:
        return "point"
    if shape_type in _LINE_TYPES:
        return "line"
    if shape_type in _POLY_TYPES:
        return "polygon"
    return "polygon"  # sensible default for null/unknown


def _safe_path(name) -> Path:
    """Resolve a layer name to its ``.shp`` path, rejecting traversal.

    TWO checks, because either alone is defeatable: the character allowlist
    rules out separators and ``..`` outright, and the resolved-parent check is
    the backstop that a symlinked layer name cannot escape the directory.
    ValueError -> 400, FileNotFoundError -> 404 via main.py's handlers.
    """
    layers_dir = config.map_layers_dir()
    if not name or not _SAFE_NAME.match(name):
        raise ValueError("Invalid layer name: {!r}".format(name))
    path = (layers_dir / "{}.shp".format(name)).resolve()
    if layers_dir.resolve() not in path.parents:
        raise ValueError("Invalid layer name: {!r}".format(name))
    if not path.exists():
        raise FileNotFoundError("No such layer: {}".format(name))
    return path


def _borders_metadata():
    """Metadata for the borders pseudo-layer, or None if it cannot be read.

    The file is generated, not hand-written, so a corrupt one means a failed
    build or a half-copied file -- either way the map's DATA layers are still
    perfectly listable, and dropping the backdrop from the list beats a 500
    that blanks the whole sidebar. load_borders() degrades the same way.
    """
    try:
        borders = json.loads(config.map_borders_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):   # unreadable file / not JSON (incl. bad UTF-8)
        return None
    if not isinstance(borders, dict):
        return None
    return {
        "name": BORDERS_LAYER_NAME,
        "geom_type": "line",  # drawn as outlines, never filled
        "feature_count": len(borders.get("features", [])),
        "bbox": borders.get("bbox"),
        "is_borders": True,
    }


def list_layers() -> List[Dict[str, Any]]:
    """Metadata for every available layer (no geometry), borders FIRST.

    The borders pseudo-layer leads the list when its file exists so the client
    can draw the backdrop first without knowing the name is special; the
    shapefile sets follow in filename order. A layer whose ``.shp`` cannot be
    opened (a half-copied set missing its .shx/.dbf) is SKIPPED rather than
    failing the whole list -- one bad file on the share must not blank the map.

    A file the loader could never serve is skipped too: a stem outside
    _SAFE_NAME (spaces are ubiquitous in GIS deliverables -- "Seismic Blocks
    2024.shp") would list happily and then 400 on the click that loads it, so
    it is not offered at all.
    """
    layers: List[Dict[str, Any]] = []
    if config.map_borders_file().exists():
        borders = _borders_metadata()
        if borders is not None:
            layers.append(borders)
    layers_dir = config.map_layers_dir()
    if not layers_dir.exists():
        return layers
    for shp in sorted(layers_dir.glob("*.shp")):
        if not _SAFE_NAME.match(shp.stem):
            continue        # listable but not loadable -> don't offer it
        reader = None
        try:
            # EVERY read is inside the guard, not just the open: pyshp raises
            # ShapefileException for a missing .shx/.dbf but a bare
            # struct.error when the header itself is truncated, and both mean
            # the same thing here -- a half-copied set on the share must not
            # blank the map for the layers that ARE intact.
            reader = shapefile.Reader(str(shp))
            meta = {
                "name": shp.stem,
                "geom_type": _geom_family(reader.shapeType),
                "feature_count": len(reader),
                "bbox": list(reader.bbox) if len(reader) else None,  # UTM37 m
            }
        except Exception:
            continue
        finally:
            if reader is not None:
                reader.close()
        layers.append(meta)
    return layers


def _shape_geometry(shape) -> Dict[str, Any]:
    """Convert a pyshp shape into {type, coordinates}. Rings/parts preserved.

    ``shape.parts`` holds the START index of each ring/part, so the point list
    is split on those boundaries: a polygon's holes and a multi-part line's
    segments stay separate rings instead of collapsing into one stroke that
    crosses the map.
    """
    family = _geom_family(shape.shapeType)
    pts = [[float(x), float(y)] for x, y in shape.points]

    if family == "point":
        return {"type": "point", "coordinates": pts}

    parts = list(shape.parts) if shape.parts else [0]
    parts.append(len(pts))
    rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
    return {"type": family, "coordinates": rings}


def load_layer(name) -> Dict[str, Any]:
    """Return one layer as a GeoJSON-like FeatureCollection (UTM37 coords)."""
    path = _safe_path(name)
    reader = None
    try:
        try:
            reader = shapefile.Reader(str(path))
            # Field names, skipping the leading DeletionFlag tuple pyshp
            # prepends. The .shp can open and STILL be unusable: `fields`
            # raises when the companion .dbf is absent, and a truncated .shp
            # raises struct.error mid-read. Both are "this set is broken",
            # which is a 400 about the layer, not a 500 about the server.
            field_names = [f[0] for f in reader.fields[1:]]
            shape_records = list(reader.shapeRecords())
            bbox = list(reader.bbox) if len(reader) else None
            shape_type = reader.shapeType
        except Exception as err:
            raise ValueError(
                "Layer {} cannot be read (missing or corrupt .shx/.dbf?)".format(name)
            ) from err
    finally:
        if reader is not None:
            reader.close()

    features = []
    for sr in shape_records:
        props = dict(zip(field_names, list(sr.record)))
        # DBF values can be bytes/date/Decimal; keep the response JSON-safe.
        props = {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                 for k, v in props.items()}
        features.append({
            "geometry": _shape_geometry(sr.shape),
            "properties": props,
        })
    return {
        "name": name,
        "geom_type": _geom_family(shape_type),
        "bbox": bbox,
        "features": features,
    }


def load_borders() -> str:
    """The prebuilt borders file's RAW JSON text, or an empty collection.

    Returned as text, not a parsed dict: the file is already the exact response
    body (~70 KB), so re-parsing and re-serializing it on every request would
    be pure waste. A missing file degrades to an empty feature collection --
    the map still draws its data layers, just without the backdrop.
    """
    path = config.map_borders_file()
    if not path.exists():
        return json.dumps({"name": BORDERS_LAYER_NAME, "features": [], "bbox": None})
    return path.read_text(encoding="utf-8")
