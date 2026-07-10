"""Small, dependency-free helper functions shared across the domain modules.

What belongs here:
- Pure date/number/text utilities used by more than one module (timestamp
  strings, ISO date parsing, day-difference math, the target-date health label,
  numeric coercion).

What does NOT belong here:
- Anything that touches the database, Flask, SQLAlchemy, or business rules. If a
  helper needs a session or knows about tasks/projects, it belongs in
  ``workflow.py`` / ``reporting.py`` instead.

These were previously free functions at the top of the monolithic
``database.py``; they are pulled out so cos/workflow/reporting/export can share
one copy.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def utc_now_str() -> str:
    """Current UTC time as ``YYYY-MM-DD HH:MM:SS`` (the stored timestamp format).

    Uses ``datetime.utcnow()`` deliberately to preserve the exact legacy string
    and value; this emits a DeprecationWarning on newer Pythons, which the test
    suite tolerates.
    """
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    """Today's date as an ISO ``YYYY-MM-DD`` string."""
    return date.today().isoformat()


def parse_iso_date(value) -> Optional[date]:
    """Parse an ISO date string into a ``date``; return ``None`` if unparseable."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


def health_from_target(target_date_str, overall_status) -> str:
    """Classify a project's schedule health from its target date and status.

    Completed -> ``Completed``; no target -> ``On Track``; past target ->
    ``Overdue``; within 14 days -> ``Due Soon``; otherwise ``On Track``.
    """
    target_dt = parse_iso_date(target_date_str)
    if overall_status == "Completed":
        return "Completed"
    if not target_dt:
        return "On Track"
    delta = (target_dt - date.today()).days
    if delta < 0:
        return "Overdue"
    if delta <= 14:
        return "Due Soon"
    return "On Track"


def to_float_or_none(value) -> Optional[float]:
    """Coerce a possibly comma-formatted string/number to float, else ``None``."""
    if value is None:
        return None
    text = str(value).replace(',', '').strip()
    if not text or text == '-':
        return None
    try:
        return float(text)
    except Exception:
        return None
