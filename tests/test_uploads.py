"""Tests for the portfolio waterfall diagram -- the app's only upload path.

Everything here is about the rules an upload endpoint has to hold whether or
not the client behaves: the stored name is ours, the type comes from the
bytes, the size is capped, and there is exactly one image so an upload
replaces rather than accumulates.
"""
from __future__ import annotations

import io

import pytest

import uploads

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 128
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path, monkeypatch):
    """Point the upload directory at the test's own tmp dir.

    Without this the suite would write into the repo's data/ directory and
    tests would see each other's images.
    """
    monkeypatch.setenv("SEGMENT_TRACKER_UPLOADS_DIR", str(tmp_path / "uploads"))
    yield


def _post(client, data, filename="diagram.png"):
    return client.post("/api/portfolio/waterfall",
                       data={"file": (io.BytesIO(data), filename)},
                       content_type="multipart/form-data")


def test_no_waterfall_is_a_404_not_an_error(client):
    """The empty case is normal: the tile asks for the image and reads a
    failure to load as 'there isn't one', so no extra probe request exists."""
    resp = client.get("/api/portfolio/waterfall")
    assert resp.status_code == 404


def test_upload_then_serve_round_trip(client):
    resp = _post(client, PNG)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["present"] is True
    assert body["content_type"] == "image/png"
    assert body["uploaded_at"] and body["uploaded_by"]

    served = client.get("/api/portfolio/waterfall")
    assert served.status_code == 200
    assert served.mimetype == "image/png"
    assert served.data == PNG


def test_the_stored_name_is_ours_not_the_clients(client, tmp_path):
    """A crafted filename must not reach the filesystem."""
    resp = _post(client, PNG, filename="../../escape.png")
    assert resp.status_code == 200
    stored = sorted(p.name for p in uploads.upload_dir().iterdir())
    assert stored == ["waterfall.png"]


def test_type_comes_from_the_bytes_not_the_extension(client):
    resp = _post(client, b"this is not an image at all", filename="diagram.png")
    assert resp.status_code == 400
    assert "PNG, JPEG or SVG" in resp.get_json()["detail"]
    assert client.get("/api/portfolio/waterfall").status_code == 404

    # ...and an image with the wrong extension is accepted on its content.
    assert _post(client, JPEG, filename="diagram.png").status_code == 200
    assert client.get("/api/portfolio/waterfall").mimetype == "image/jpeg"


def test_scripted_svg_is_refused_rather_than_cleaned(client):
    """SVG is a document that can carry script, and this file is served
    directly. Anything script-shaped is refused outright; a diagram exported
    from a drawing tool has none of it."""
    for hostile in (
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect onload="alert(1)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>',
    ):
        resp = _post(client, hostile, filename="x.svg")
        assert resp.status_code == 400, hostile
        assert "scripting" in resp.get_json()["detail"]
    assert _post(client, SVG, filename="x.svg").status_code == 200


def test_oversize_upload_is_refused_before_anything_is_written(client):
    resp = _post(client, b"\x89PNG\r\n\x1a\n" + b"0" * (uploads.MAX_UPLOAD_BYTES + 1))
    assert resp.status_code == 400
    assert "limit is" in resp.get_json()["detail"]
    assert not uploads.upload_dir().exists() or not list(uploads.upload_dir().iterdir())


def test_an_upload_replaces_rather_than_accumulates(client):
    """One image per portfolio. A PNG replacing an SVG must not leave the old
    file behind for a stale record to serve."""
    assert _post(client, SVG, filename="a.svg").status_code == 200
    assert sorted(p.name for p in uploads.upload_dir().iterdir()) == ["waterfall.svg"]
    assert _post(client, PNG, filename="b.png").status_code == 200
    assert sorted(p.name for p in uploads.upload_dir().iterdir()) == ["waterfall.png"]
    assert client.get("/api/portfolio/waterfall").mimetype == "image/png"


def test_delete_removes_the_image_and_is_idempotent(client):
    assert _post(client, PNG).status_code == 200
    assert client.delete("/api/portfolio/waterfall").get_json() == {"present": False}
    assert client.get("/api/portfolio/waterfall").status_code == 404
    # Deleting when there is nothing to delete is not an error.
    assert client.delete("/api/portfolio/waterfall").status_code == 200


def test_a_record_pointing_at_a_missing_file_reads_as_absent(client):
    """Truth over bookkeeping: if the file is gone, the UI shows its empty
    state rather than a broken image."""
    assert _post(client, PNG).status_code == 200
    (uploads.upload_dir() / "waterfall.png").unlink()
    assert client.get("/api/portfolio/waterfall").status_code == 404


def test_empty_upload_is_refused(client):
    resp = client.post("/api/portfolio/waterfall",
                       data={"file": (io.BytesIO(b""), "empty.png")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "empty" in resp.get_json()["detail"]


def test_missing_file_part_names_what_to_do(client):
    resp = client.post("/api/portfolio/waterfall", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Choose an image" in resp.get_json()["detail"]
