"""Tapo cloud cameras: stream quality (HD / VGA) per camera, changeable after adding."""

from __future__ import annotations

from foxcam.ingest.supervisor import _fingerprint

from .conftest import app_client


def test_stream_quality_can_be_changed_per_camera(settings):
    with app_client(settings) as c:
        r = c.post("/api/cameras", json={"name": "Garden", "source": {
            "type": "tapo_cloud", "device_id": "dev1", "device_name": "Garden",
            "resolution": "HD"}})
        cam_id = r.json()["id"]
        before = _fingerprint(settings, settings.camera(cam_id))
        r = c.patch(f"/api/cameras/{cam_id}", json={"tapo_resolution": "VGA"})
        assert r.status_code == 200, r.text
        cam = settings.camera(cam_id)
        assert cam.source.resolution == "VGA"
        # Ingest sees a changed source and restarts the relay with it.
        assert _fingerprint(settings, cam) != before
        assert c.patch(f"/api/cameras/{cam_id}", json={"tapo_resolution": "4K"}).status_code == 422
        # Only for Tapo cameras.
        r = c.post("/api/cameras", json={"name": "Test", "source": {"type": "test"}})
        r = c.patch(f"/api/cameras/{r.json()['id']}", json={"tapo_resolution": "VGA"})
        assert r.status_code == 400
