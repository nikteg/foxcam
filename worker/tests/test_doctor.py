from __future__ import annotations

import shutil

import httpx
import pytest
from media_worker import doctor


def test_checks_run_without_server():
    checks = {c.name: c for c in doctor.run_checks(None, None)}
    assert checks["python"].status == doctor.OK
    assert doctor.environment() in {"wsl", "docker", "windows", "macos", "linux"}
    expected = doctor.OK if shutil.which("ffmpeg") else doctor.FAIL
    assert checks["ffmpeg"].status == expected
    assert "server" not in checks


def _respond(monkeypatch, response=None, error=None):
    def fake_get(url, headers, timeout):
        assert url.endswith("/api/worker/whoami")
        if error:
            raise error
        return response

    monkeypatch.setattr(httpx, "get", fake_get)


@pytest.mark.parametrize(("response", "status", "text"), [
    (httpx.Response(200, json={"authenticated": True, "token_name": "pc"}), doctor.OK, "'pc'"),
    (httpx.Response(200, json={"authenticated": True, "token_name": None}), doctor.OK, "allowed"),
    (httpx.Response(401, json={"detail": "invalid worker token"}), doctor.FAIL, "invalid"),
    (httpx.Response(404, json={}), doctor.FAIL, "404"),
])
def test_server_check(monkeypatch, response, status, text):
    _respond(monkeypatch, response)
    check = doctor.check_server("http://server:8000", "msw_x")
    assert check.status == status and text in check.detail


def test_server_unreachable_from_wsl_hints_at_localhost(monkeypatch):
    _respond(monkeypatch, error=httpx.ConnectError("refused"))
    monkeypatch.setattr(doctor, "environment", lambda: "wsl")
    check = doctor.check_server("http://localhost:8000", None)
    assert check.status == doctor.FAIL and "mirrored" in check.hint


def test_cli_exit_code(monkeypatch, capsys):
    _respond(monkeypatch, error=httpx.ConnectError("refused"))
    with pytest.raises(SystemExit) as exc:
        doctor.main(["--server", "http://127.0.0.1:1"])
    assert exc.value.code == 1
    assert "[FAIL] server" in capsys.readouterr().out


def test_worker_stops_when_the_server_rejects_its_token(monkeypatch):
    from media_worker import cli
    from media_worker.client import Client

    def reject(self):
        request = httpx.Request("POST", "http://server/api/worker/lease")
        response = httpx.Response(401, json={"detail": "invalid worker token"}, request=request)
        raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(Client, "lease", reject)
    monkeypatch.setattr("sys.argv", ["media-worker", "--server", "http://server", "--once"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert "invalid worker token" in str(exc.value)
