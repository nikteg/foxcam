"""Signed download links: one file, a few minutes, as the user they were made for."""

from __future__ import annotations

from datetime import timedelta

from foxcam import download_links
from foxcam.models import utcnow
from foxcam.web.deps import SESSION_COOKIE

from .conftest import add_segment, app_client, needs_ffmpeg


@needs_ffmpeg
def test_a_download_token_works_without_signing_in(settings):
    seg = add_segment(settings)
    path = f"/api/segments/{seg}/download"
    with app_client(settings) as c:
        r = c.post("/api/download-token")
        assert r.status_code == 200, r.text
        token = r.json()["token"]
    with app_client(settings, email=None) as anon:
        assert anon.get(path).status_code == 401
        assert anon.post("/api/download-token").status_code == 401
        url = f"{path}?dl={token}"
        assert anon.get(url).status_code == 200
        # One token serves every file.
        other = add_segment(settings, start=utcnow() - timedelta(hours=1))
        assert anon.get(f"/api/segments/{other}/download?dl={token}").status_code == 200
        # Safari may still hold an expired session cookie: the token works anyway.
        anon.cookies.set(SESSION_COOKIE, "expired-session")
        assert anon.get(url).status_code == 200
        anon.cookies.clear()
        # Files only.
        assert anon.get(f"/api/segments/{seg}?dl={token}").status_code == 401
        assert anon.get(f"/api/settings?dl={token}").status_code == 401


def test_expired_or_forged_tokens_are_refused(settings):
    path = "/api/segments/1/download"
    assert download_links.verify(settings, download_links.sign(settings, 1, ttl=-1),
                                 path) is None
    assert download_links.verify(settings, "1.9999999999.forged", path) is None
    assert download_links.verify(settings, "garbage", path) is None
