"""Signed download links: one file, a few minutes, as the user they were made for."""

from __future__ import annotations

from datetime import timedelta

from foxcam import download_links
from foxcam.models import utcnow

from .conftest import add_segment, app_client, needs_ffmpeg


@needs_ffmpeg
def test_a_download_link_works_without_signing_in(settings):
    seg = add_segment(settings)
    path = f"/api/segments/{seg}/download"
    with app_client(settings) as c:
        r = c.post("/api/download-links", json={"path": path})
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert c.post("/api/download-links", json={"path": "/api/settings"}).status_code == 422
    with app_client(settings, email=None) as anon:
        assert anon.get(path).status_code == 401
        assert anon.get(url).status_code == 200
        token = url.split("dl=")[1]
        # Only for its own file.
        other = add_segment(settings, start=utcnow() - timedelta(hours=1))
        assert anon.get(f"/api/segments/{other}/download?dl={token}").status_code == 401


def test_expired_or_forged_links_are_refused(settings):
    path = "/api/segments/1/download"
    assert download_links.verify(settings, download_links.sign(settings, 1, path, ttl=-1),
                                 path) is None
    assert download_links.verify(settings, "1.9999999999.forged", path) is None
    assert download_links.verify(settings, "garbage", path) is None
