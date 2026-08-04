"""Write small, valid ZMAP+ grid files for tests.

The canonical grid every surfaces test shares is a 3x3 over x 0..200 / y
0..200 (100 m spacing) with ONE null hole at its top-right node:

        x=0   x=100  x=200
y=200   10.0   40.0   (null)
y=100   20.0   50.0   80.0
y=0     30.0   60.0   90.0

Hand-computed reference points used across the tests:
- (50, 150) interior bilinear: (10 + 40 + 20 + 50) / 4 = 30.0
- (100, 100) exactly on a node: 50.0
- (150, 150) leans on the null node: None
- (200, 50) on the right edge: 80 * 0.5 + 90 * 0.5 = 85.0
"""
from __future__ import annotations

from pathlib import Path

NULL_VALUE = -99999.0

GRID_XMIN, GRID_XMAX = 0.0, 200.0
GRID_YMIN, GRID_YMAX = 0.0, 200.0

# Row-major, TOP ROW FIRST (y = ymax), None = null node -- the human-readable
# orientation; write_zmap_grid converts to the file's column-major order.
GRID_VALUES = [
    [10.0, 40.0, None],   # y = 200
    [20.0, 50.0, 80.0],   # y = 100
    [30.0, 60.0, 90.0],   # y = 0
]


def write_zmap_grid(path, values, xmin, xmax, ymin, ymax,
                    null_value=NULL_VALUE, nodes_per_line=4):
    """Write ``values`` (row-major, top row first; None = null) as a standard
    ZMAP+ ASCII grid file and return its path."""
    rows = len(values)
    cols = len(values[0])
    column_major = [null_value if values[row][col] is None else float(values[row][col])
                    for col in range(cols) for row in range(rows)]
    lines = [
        "! Test surface written by tests/zmap_fixtures.py",
        "@TEST GRID FILE, GRID, {}".format(nodes_per_line),
        "  15, {:.4f}, , 4, 1".format(null_value),
        "  {}, {}, {:.4f}, {:.4f}, {:.4f}, {:.4f}".format(rows, cols, xmin, xmax, ymin, ymax),
        "  0.0, 0.0, 0.0",
        "@",
    ]
    for start in range(0, len(column_major), nodes_per_line):
        chunk = column_major[start:start + nodes_per_line]
        lines.append(" " + " ".join("{:.4f}".format(value) for value in chunk))
    file_path = Path(path)
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file_path


def write_sample_grid(path):
    """The canonical 3x3 test grid (see module docstring)."""
    return write_zmap_grid(path, GRID_VALUES, GRID_XMIN, GRID_XMAX, GRID_YMIN, GRID_YMAX)
