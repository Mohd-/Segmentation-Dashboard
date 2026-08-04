"""Read ZMAP+ ASCII grid surfaces and sample them at UTM37N points.

A surface is one ``.dat`` file in the standard ZMAP plus grid format: '!'
comment lines, a header section opened by ``@<name>, GRID, <nodes per line>``
holding three comma-separated records (null value / decimal count / start
column; rows, cols and the four extents), an ``@`` terminator line, then the
node values COLUMN-MAJOR -- each column printed top to bottom, i.e. y
DECREASING, columns in x-increasing order. The declared null value (and any
blank field) means "no data here".

Coordinates are used AS-IS, exactly like map_layers.py: every surface must
already be in UTM Zone 37N metres, the same flat plane the lead/staked
coordinates live in, so there is no reprojection anywhere in this module.

Sampling is bilinear over the four surrounding nodes and deliberately refuses
to invent data: a point outside the extents, or one whose interpolation would
lean on a null node, returns None. Landing EXACTLY on a node returns that
node's value (the degenerate bilinear case -- every other weight is zero), so a
hole in the grid never bleeds into its neighbours.

What does NOT belong here: Flask objects, SQL, project data. Which surface
file answers which question is config's business (config.tsq_surface_file /
config.ground_elevation_surface_file), and writing sampled values into the
database is workflow/surfaces_fill.py's.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Grid:
    """One parsed ZMAP+ grid: shape, extents and column-major node values.

    ``values[col * rows + row]`` is the node at the col-th x step and the
    row-th y step FROM THE TOP (row 0 is y = ymax, the file's own order).
    Null/blank nodes are stored as None.
    """

    __slots__ = ("rows", "cols", "xmin", "xmax", "ymin", "ymax",
                 "null_value", "null_text", "values")

    def __init__(self, rows, cols, xmin, xmax, ymin, ymax,
                 null_value, null_text, values):
        self.rows = rows
        self.cols = cols
        self.xmin = xmin
        self.xmax = xmax
        self.ymin = ymin
        self.ymax = ymax
        self.null_value = null_value
        self.null_text = null_text
        self.values = values

    def node(self, col, row) -> Optional[float]:
        """The node value at (col, row-from-top), or None where the grid is null."""
        return self.values[col * self.rows + row]


def _split_record(line) -> List[str]:
    """One comma-separated header/value record -> stripped fields."""
    return [field.strip() for field in line.split(",")]


def _parse_tokens(line, field_width, start_column) -> List[Optional[str]]:
    """The value tokens of one node line, preserving blank fields as None.

    Canonical ZMAP+ data is fixed-width with no delimiter at all; the header's
    field width and one-based starting column are therefore authoritative.
    Some exporters emit whitespace- or comma-separated values instead, which
    remain supported as fallbacks. A blank fixed-width/comma field is a real
    no-data node. A single trailing comma is only a separator artefact.
    """
    data = line[max(start_column - 1, 0):]
    if "," in data:
        tokens = [field.strip() for field in data.split(",")]
        if tokens and tokens[-1] == "":
            tokens.pop()
        return [token or None for token in tokens]
    if field_width > 0 and len(data) >= field_width and len(data) % field_width == 0:
        return [data[offset:offset + field_width].strip() or None
                for offset in range(0, len(data), field_width)]
    return list(data.split())


def _parse_number(token, decimal_count) -> float:
    """Parse one numeric ZMAP field, including its legacy implied decimal."""
    normalized = token.replace("D", "E").replace("d", "e")
    number = float(normalized)
    if "." not in normalized and "e" not in normalized.lower() and decimal_count:
        number /= 10 ** decimal_count
    return number


def _parse(lines) -> Grid:
    """Parse the lines of a ZMAP+ grid file. Raises ValueError on any defect."""
    index = 0
    total = len(lines)

    def next_record():
        """The next non-blank, non-comment line, or None at end of file."""
        nonlocal index
        while index < total:
            line = lines[index].strip()
            index += 1
            if line and not line.startswith("!"):
                return line
        return None

    header = next_record()
    if header is None or not header.startswith("@"):
        raise ValueError("not a ZMAP+ grid (missing '@<name>, GRID, ...' header)")
    fields = _split_record(header[1:])
    if len(fields) < 2 or fields[1].upper() != "GRID":
        raise ValueError("not a ZMAP+ GRID file (header type is {!r})".format(
            fields[1] if len(fields) > 1 else ""))
    try:
        nodes_per_line = int(fields[2])
    except (IndexError, ValueError):
        raise ValueError("unreadable nodes-per-line in ZMAP+ header")
    if nodes_per_line < 1:
        raise ValueError("invalid nodes-per-line in ZMAP+ header")

    records = [next_record() for _ in range(3)]
    if any(record is None for record in records):
        raise ValueError("truncated ZMAP+ header (expected three header records)")
    if next_record() != "@":
        raise ValueError("missing '@' header terminator")

    record_one = _split_record(records[0])
    try:
        field_width = int(record_one[0])
        decimal_count = int(record_one[3])
        start_column = int(record_one[4])
    except (IndexError, ValueError):
        raise ValueError("unreadable field layout in ZMAP+ header record 1")
    if field_width < 1 or decimal_count < 0 or start_column < 1:
        raise ValueError("invalid field layout in ZMAP+ header record 1")
    numeric_null = record_one[1] if len(record_one) > 1 else ""
    null_text = record_one[2] if len(record_one) > 2 else ""
    if not numeric_null and not null_text:
        raise ValueError("missing null marker in ZMAP+ header record 1")
    try:
        null_value = _parse_number(numeric_null, decimal_count) if numeric_null else None
    except ValueError:
        raise ValueError("unreadable null value in ZMAP+ header record 1")
    record_two = _split_record(records[1])
    try:
        rows = int(float(record_two[0]))
        cols = int(float(record_two[1]))
        xmin, xmax, ymin, ymax = (float(field) for field in record_two[2:6])
    except (IndexError, ValueError):
        raise ValueError("unreadable rows/cols/extents in ZMAP+ header record 2")
    if rows < 2 or cols < 2:
        raise ValueError("degenerate grid ({} rows x {} cols); need at least 2x2".format(rows, cols))
    if not (xmax > xmin and ymax > ymin):
        raise ValueError("inverted or empty extents (x {}..{}, y {}..{})".format(
            xmin, xmax, ymin, ymax))

    values: List[Optional[float]] = []
    while index < total:
        line = lines[index]
        index += 1
        stripped = line.strip()
        if not line or stripped.startswith("!"):
            continue
        tokens = _parse_tokens(line, field_width, start_column)
        if len(tokens) > nodes_per_line:
            raise ValueError("data line contains {} nodes; header allows at most {}".format(
                len(tokens), nodes_per_line))
        for token in tokens:
            if token is None:
                values.append(None)     # blank field = no data at this node
                continue
            if null_text and token == null_text:
                values.append(None)
                continue
            try:
                number = _parse_number(token, decimal_count)
            except ValueError:
                raise ValueError("unreadable node value {!r}".format(token))
            values.append(None if null_value is not None and number == null_value else number)
    if len(values) != rows * cols:
        raise ValueError("expected {} node values ({} rows x {} cols), found {}".format(
            rows * cols, rows, cols, len(values)))
    return Grid(rows, cols, xmin, xmax, ymin, ymax, null_value, null_text or None, values)


# Parsed grids, keyed by absolute path with a full stat fingerprint as the
# staleness check: a replaced surface file is re-read on its next sample, an
# unchanged one is parsed once per process. (Worst case under concurrent first
# reads is a redundant parse, which is harmless.)
_CACHE: Dict[str, Tuple[Tuple[int, int, int, int, int], Grid]] = {}


def load_grid(path) -> Grid:
    """Parse (or return the cached parse of) the ZMAP+ grid at ``path``.

    Cached by absolute path plus stat fingerprint. Raises OSError when the file cannot be read and
    ValueError when it cannot be parsed -- callers who must never fail go
    through :func:`sample_surface` instead.
    """
    file_path = Path(path).expanduser().resolve()
    key = str(file_path)
    stat = file_path.stat()
    fingerprint = (stat.st_dev, stat.st_ino, stat.st_size,
                   stat.st_mtime_ns, stat.st_ctime_ns)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    grid = _parse(file_path.read_text(encoding="utf-8").splitlines())
    _CACHE[key] = (fingerprint, grid)
    return grid


def sample(grid, x, y) -> Optional[float]:
    """Bilinear sample of ``grid`` at (x, y), or None where honesty forbids one.

    None outside the extents, for a non-numeric/non-finite point, and whenever
    any node that would CONTRIBUTE (carry non-zero weight) is null -- a hole in
    the surface must stay a hole, not an invented average of its rim. Exactly
    on a node every other weight is zero, so the node's own value comes back
    (and None if that node is itself the hole).
    """
    try:
        px = float(x)
        py = float(y)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(px) and math.isfinite(py)):
        return None
    if not (grid.xmin <= px <= grid.xmax and grid.ymin <= py <= grid.ymax):
        return None

    step_x = (grid.xmax - grid.xmin) / (grid.cols - 1)
    step_y = (grid.ymax - grid.ymin) / (grid.rows - 1)
    fx = (px - grid.xmin) / step_x          # fractional column (x increasing)
    fy = (grid.ymax - py) / step_y          # fractional row FROM THE TOP (file order)
    # Decimal header coordinates need not have exact binary representations.
    # Snap a mathematically exact grid line back to its integer index so a
    # 1e-16 rounding weight cannot make an adjacent null node veto the sample.
    nearest_x = round(fx)
    nearest_y = round(fy)
    if math.isclose(fx, nearest_x, rel_tol=0.0, abs_tol=1e-12):
        fx = float(nearest_x)
    if math.isclose(fy, nearest_y, rel_tol=0.0, abs_tol=1e-12):
        fy = float(nearest_y)
    # Clamp the cell so a point on the max edge samples the last cell with
    # weight 1.0 on its far side instead of indexing off the grid.
    col = min(int(fx), grid.cols - 2)
    row = min(int(fy), grid.rows - 2)
    tx = fx - col
    ty = fy - row

    contributions = (
        ((1.0 - tx) * (1.0 - ty), col, row),
        (tx * (1.0 - ty), col + 1, row),
        ((1.0 - tx) * ty, col, row + 1),
        (tx * ty, col + 1, row + 1),
    )
    result = 0.0
    for weight, node_col, node_row in contributions:
        if weight == 0.0:
            continue                        # zero-weight node cannot veto the sample
        value = grid.node(node_col, node_row)
        if value is None:
            return None                     # a contributing null makes the point a hole
        result += weight * value
    return result


def sample_surface(path, x, y) -> Optional[float]:
    """Sample the surface file at (x, y); None on ANY problem, never an exception.

    The convenience entry point for save-time auto-fill: an unconfigured path
    (falsy), a missing file, or a corrupt/unparseable one all mean "no value
    here" -- logged, because a corrupt surface on the share is worth a line in
    the log, but never allowed to break the save that asked.
    """
    if not path:
        return None
    try:
        grid = load_grid(path)
        return sample(grid, x, y)
    except Exception as err:  # noqa: BLE001 -- a bad surface file must never break a save
        logger.warning("Surface %s is unusable (%s); returning no value.", path, err)
        return None
