"""User-uploaded images: today, the Portfolio Analysis waterfall diagram.

Charter mirrors cos.py and resource_calc.py -- no Flask objects, no request
globals. Give it the raw bytes and a filename, get back a stored record or a
``ValueError`` with a message the user can act on.

This is the app's FIRST upload path, so the rules are set here rather than
inferred later:

* The stored name is ours, never the client's. Nothing derived from an
  uploaded filename reaches the filesystem, so a crafted name cannot escape
  the upload directory or overwrite anything.
* The TYPE is decided by the bytes, not the extension. A .png that is really
  something else is refused.
* SVG is accepted but SANITISED to a degree, because SVG is a document that
  can carry script -- an <img src> cannot run it, but the file is also served
  directly, so anything script-shaped is rejected outright rather than
  cleaned. A diagram exported from Excel or a drawing tool has none of it.
* There is exactly ONE waterfall for the whole portfolio, so an upload
  REPLACES the previous one (file and record) rather than accumulating.
* Size is capped before anything is written.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

import config
import db
from helpers import utc_now_str

# One image, one key. Storing the record in app_settings rather than a table of
# its own: it is a single row that will never be queried by anything but key.
WATERFALL_SETTING_KEY = "portfolio_waterfall"

MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Magic-byte signature -> (extension, content type). Decided by the bytes; the
# uploaded filename is never consulted.
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
)

# Anything that makes an SVG behave like a document rather than a drawing.
_SVG_FORBIDDEN = re.compile(
    rb"<\s*script|<\s*foreignObject|\son\w+\s*=|javascript:|<\s*iframe|<\s*use[^>]+href\s*=\s*[\"']\s*http",
    re.IGNORECASE,
)


def upload_dir() -> Path:
    """Directory holding user uploads. Created on first write, not at import."""
    return Path(config.uploads_dir())


def _sniff(data: bytes) -> tuple:
    for signature, extension, content_type in _SIGNATURES:
        if data.startswith(signature):
            return extension, content_type
    head = data[:1024].lstrip()
    if head.startswith(b"<?xml") or head.lower().startswith(b"<svg"):
        if _SVG_FORBIDDEN.search(data):
            raise ValueError(
                "That SVG contains scripting or embedded documents and was not stored. "
                "Export the diagram as a plain drawing, or upload a PNG.")
        return "svg", "image/svg+xml"
    raise ValueError("Upload a PNG, JPEG or SVG image.")


def _read_setting(session) -> Optional[str]:
    row = db.fetch_one(session, "SELECT value FROM app_settings WHERE key = :key",
                       {"key": WATERFALL_SETTING_KEY})
    return row["value"] if row else None


def _write_setting(session, value: Optional[str]) -> None:
    with db.write_transaction(session):
        if value is None:
            db.execute(session, "DELETE FROM app_settings WHERE key = :key",
                       {"key": WATERFALL_SETTING_KEY})
            return
        db.execute(session, """
            INSERT INTO app_settings (key, value) VALUES (:key, :value)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, {"key": WATERFALL_SETTING_KEY, "value": value})


def _record_from_value(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse the stored 'extension|uploaded_at|uploaded_by' triple."""
    if not value:
        return None
    parts = value.split("|", 2)
    if len(parts) != 3:
        return None
    extension, uploaded_at, uploaded_by = parts
    path = upload_dir() / ("waterfall." + extension)
    if not path.exists():
        return None
    content_type = {"png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml"}[extension]
    return {"path": path, "extension": extension, "content_type": content_type,
            "uploaded_at": uploaded_at, "uploaded_by": uploaded_by}


def get_waterfall(session) -> Optional[Dict[str, Any]]:
    """The stored waterfall record, or None when there is none.

    Returns None (not an error) when the setting names a file that is no longer
    on disk: the UI then shows its empty state, which is the truth.
    """
    return _record_from_value(_read_setting(session))


def waterfall_info(session) -> Dict[str, Any]:
    """The JSON-friendly half of the record, for the portfolio payload."""
    record = get_waterfall(session)
    if not record:
        return {"present": False}
    return {"present": True, "uploaded_at": record["uploaded_at"],
            "uploaded_by": record["uploaded_by"], "content_type": record["content_type"]}


def save_waterfall(session, data: bytes, uploaded_by: str = "Web User") -> Dict[str, Any]:
    """Validate and store the portfolio's waterfall diagram, replacing any prior one."""
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            "That image is %.1f MB; the limit is %d MB."
            % (len(data) / (1024 * 1024), MAX_UPLOAD_BYTES // (1024 * 1024)))
    extension, _content_type = _sniff(data)

    directory = upload_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # Remove any previous image FIRST, so a PNG replacing an SVG cannot leave
    # the old file behind to be served by a stale record.
    _remove_files()
    (directory / ("waterfall." + extension)).write_bytes(data)
    _write_setting(session, "|".join([extension, utc_now_str(), str(uploaded_by or "Web User")]))
    return waterfall_info(session)


def delete_waterfall(session) -> Dict[str, Any]:
    """Remove the stored diagram. Deleting when there is none is not an error."""
    _remove_files()
    _write_setting(session, None)
    return {"present": False}


def _remove_files() -> None:
    directory = upload_dir()
    if not directory.exists():
        return
    for extension in ("png", "jpg", "svg"):
        candidate = directory / ("waterfall." + extension)
        if candidate.exists():
            candidate.unlink()
