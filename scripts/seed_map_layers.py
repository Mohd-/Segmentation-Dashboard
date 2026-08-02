#!/usr/bin/env python
"""Write sample UTM37N shapefile sets into the configured map layers directory.

The dev counterpart of seed_dev.py: seed_dev gives the map its WELLS, this
gives it the acreage they sit in, so the whole map surface (pan/zoom, layer
toggles, hover attributes, well-in-block association) can be exercised before
any real shapefile arrives.

Two layers, both deterministic:

  sample_blocks   4 polygons ("Block A".."Block D"), each ENCLOSING one
                  quadrant of seed_dev.LEAD_CLUSTER_CENTERS -- imported from
                  seed_dev rather than restated, so the blocks cannot drift
                  away from the wells they are supposed to contain.
  sample_lines    one polyline per block, threading its field centers: a
                  stand-in for seismic lines / pipelines to prove the line
                  geometry family and multi-part rendering.

Idempotent: reruns overwrite the same files. It REFUSES to write into a
directory holding any ``.shp`` that is not one of ours, because that directory
is where a deployment drops REAL data (config.map_layers_dir(), overridable
with SEGMENT_TRACKER_MAP_DATA_DIR) and sample files must never land on top of
it.

Run:  .venv/bin/python scripts/seed_map_layers.py
      SEGMENT_TRACKER_MAP_DATA_DIR=/tmp/mapdata .venv/bin/python scripts/seed_map_layers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import shapefile  # pyshp

import config
from seed_dev import LEAD_CLUSTER_CENTERS

# Prefix every generated file carries. The overwrite guard below is keyed on
# it: anything else in the directory is somebody's real data.
SAMPLE_PREFIX = "sample_"

# Which field codes each block covers. The block's extent is DERIVED from the
# centers of its members (plus the margin below), so adding a field to
# seed_dev.LEAD_CLUSTER_CENTERS and listing it here is the whole change.
BLOCK_MEMBERS = [
    ("Block A", ["GALV", "ORYX", "FYNN", "MDFT", "QASM"]),
    ("Block B", ["CROX", "WREN", "SARH", "RUBX"]),
    ("Block C", ["IBEX", "LUNA", "TANQ", "HOFR"]),
    ("Block D", ["VEGA", "DYNE", "KELS", "BRAN"]),
]

# Metres of block extending past its outermost field center. Comfortably more
# than seed_dev's few-km per-well scatter, so every seeded well -- not just
# every field center -- falls inside its block.
BLOCK_MARGIN_M = 30000.0

# UTM Zone 37N / WGS84, written beside each set so the projection travels with
# the data (nothing in the app reads it; a GIS opening these files does).
UTM37N_WKT = (
    'PROJCS["WGS_1984_UTM_Zone_37N",GEOGCS["GCS_WGS_1984",'
    'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],'
    'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",39.0],'
    'PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",0.0],'
    'UNIT["Meter",1.0]]'
)


def _members(codes):
    """The known (x, y) centers for a block's field codes, in listed order."""
    return [LEAD_CLUSTER_CENTERS[code] for code in codes if code in LEAD_CLUSTER_CENTERS]


def _block_ring(centers):
    """Closed rectangular ring around ``centers``, clockwise (pyshp outer ring)."""
    minx = min(x for x, _ in centers) - BLOCK_MARGIN_M
    maxx = max(x for x, _ in centers) + BLOCK_MARGIN_M
    miny = min(y for _, y in centers) - BLOCK_MARGIN_M
    maxy = max(y for _, y in centers) + BLOCK_MARGIN_M
    return [[minx, miny], [minx, maxy], [maxx, maxy], [maxx, miny], [minx, miny]]


def _guard(layers_dir):
    """Refuse to write next to shapefiles this script did not create."""
    foreign = sorted(p.name for p in layers_dir.glob("*.shp")
                     if not p.name.startswith(SAMPLE_PREFIX))
    if foreign:
        raise SystemExit(
            "Refusing to write samples into {}: it already holds real shapefile "
            "sets ({}). Point SEGMENT_TRACKER_MAP_DATA_DIR at a scratch directory "
            "instead.".format(layers_dir, ", ".join(foreign)))


def write_blocks(layers_dir):
    """4 enclosing polygons, each with a ``name`` attribute for the hover card."""
    path = layers_dir / "sample_blocks"
    writer = shapefile.Writer(str(path), shapeType=shapefile.POLYGON)
    writer.field("name", "C", size=40)
    writer.field("fields", "C", size=80)
    writer.field("area_km2", "N", decimal=1)
    written = 0
    for label, codes in BLOCK_MEMBERS:
        centers = _members(codes)
        if not centers:
            continue
        ring = _block_ring(centers)
        width = ring[2][0] - ring[0][0]
        height = ring[2][1] - ring[0][1]
        writer.poly([ring])
        writer.record(label, ",".join(codes), round(width * height / 1e6, 1))
        written += 1
    writer.close()
    return path, written


def write_lines(layers_dir):
    """One polyline per block, threading that block's field centers."""
    path = layers_dir / "sample_lines"
    writer = shapefile.Writer(str(path), shapeType=shapefile.POLYLINE)
    writer.field("name", "C", size=40)
    written = 0
    for label, codes in BLOCK_MEMBERS:
        centers = _members(codes)
        if len(centers) < 2:
            continue
        writer.line([[[x, y] for x, y in centers]])
        writer.record("{} traverse".format(label))
        written += 1
    writer.close()
    return path, written


def write_prj(layers_dir, stems):
    for stem in stems:
        (layers_dir / "{}.prj".format(stem)).write_text(UTM37N_WKT, encoding="utf-8")


def main():
    layers_dir = config.map_layers_dir()
    layers_dir.mkdir(parents=True, exist_ok=True)
    _guard(layers_dir)

    print("Layers directory: {}".format(layers_dir))
    blocks_path, block_count = write_blocks(layers_dir)
    print("wrote {}.shp ({} polygons, margin {:.0f} m around {} field centers)".format(
        blocks_path, block_count, BLOCK_MARGIN_M, len(LEAD_CLUSTER_CENTERS)))
    lines_path, line_count = write_lines(layers_dir)
    print("wrote {}.shp ({} polylines)".format(lines_path, line_count))
    write_prj(layers_dir, [blocks_path.stem, lines_path.stem])
    print("wrote {}.prj, {}.prj (UTM Zone 37N / WGS84)".format(
        blocks_path.stem, lines_path.stem))


if __name__ == "__main__":
    main()
