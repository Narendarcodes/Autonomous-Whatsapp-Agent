"""Headless re-pairing: dashboard-driven `hermes whatsapp` without a human TTY.

Covers: symlink repair (wizard crashes on dangling link), command construction,
start gating, and the post-scan watchdog that restarts hermes so the gateway
adopts freshly written credentials.
"""
import os

import pytest

import app.services.docker_manager as dm_mod
import app.services.pairing_session_service as svc

HERMES_BIN = "/opt/hermes/.venv/bin/hermes"


def _symlinks_available(tmp_path):
    """Symlink creation needs admin/dev-mode on Windows; prod runs in Linux."""
    probe = tmp_path / "_probe"
    try:
        os.symlink(tmp_path, probe)
        os.remove(probe)
        return True
    except OSError:
        return False


@pytest.fixture()
def needs_symlink(tmp_path):
    if not _symlinks_available(tmp_path):
        pytest.skip("OS denies symlink creation (Windows without dev mode)")


# ---------- symlink repair ----------

def test_repairs_dangling_symlink(tmp_path, needs_symlink):
    root = tmp_path / "platforms" / "whatsapp"
    root.mkdir(parents=True)
    legacy = tmp_path / "whatsapp"
    legacy.mkdir()
    os.symlink(str(root / "session"), str(legacy / "session"))  # dangling

    result = svc.ensure_wizard_paths(str(tmp_path))

    assert (root / "session").is_dir()
    assert (legacy / "session").resolve() == (root / "session").resolve()


def test_creates_missing_symlink_and_target(tmp_path, needs_symlink):
    (tmp_path / "platforms").mkdir()
    (tmp_path / "whatsapp").mkdir()

    svc.ensure_wizard_paths(str(tmp_path))

    assert (tmp_path / "platforms" / "whatsapp" / "session").is_dir()
    assert (tmp_path / "whatsapp" / "session").is_dir()


def test_valid_symlink_left_untouched(tmp_path, needs_symlink):
    root = tmp_path / "platforms" / "whatsapp" / "session"
    root.mkdir(parents=True)
    legacy = tmp_path / "whatsapp"
    legacy.mkdir()
    os.symlink(str(root), str(legacy / "session"))
    before = os.readlink(str(legacy / "session"))

    svc.ensure_wizard_paths(str(tmp_path))

    assert os.readlink(str(legacy / "session")) == before


def test_real_dir_at_legacy_path_not_destroyed(tmp_path):
    legacy_dir = tmp_path / "whatsapp" / "session"
    legacy_dir.mkdir(parents=True)
    marker = legacy_dir / "keep.txt"
    marker.write_text("data", encoding="utf-8")

    svc.ensure_wizard_paths(str(tmp_path))

    assert marker.exists()  # never delete user data


# ---------- command construction ----------

def test_build_pairing_command_shape(tmp_path):
    cmd = svc.build_pairing_command(str(tmp_path))
    assert f"{HERMES_BIN} whatsapp" in cmd
    assert "script -qfc" in cmd                      # pty wrapper
    assert "printf" in cmd and "'n\\ny\\n'" in cmd   # scripted answers
    assert "pairing_session.out" in cmd              # QR lands on shared volume
    assert ": >" in cmd                              # stale output truncated
    assert "pkill" in cmd                            # prior wizard killed


# ---------- start gating ----------

@pytest.mark.asyncio
async def test_start_refuses_when_already_paired(tmp_path, monkeypatch):
    launched = []
    async def fake_runner(container, cmd):
        launched.append((container, cmd))
        return True

    result = await svc.start_pairing_session(
        runner=fake_runner, is_paired_fn=lambda: True, schedule_watchdog=False
    )
    assert result == {"started": False, "reason": "already_paired"}
    assert launched == []


@pytest.mark.asyncio
async def test_start_repairs_paths_and_launches(tmp_path, monkeypatch, needs_symlink):
    launched = []
    async def fake_runner(container, cmd):
        launched.append((container, cmd))
        return True

    (tmp_path / "whatsapp").mkdir()
    os.symlink(str(tmp_path / "nowhere"), str(tmp_path / "whatsapp" / "session"))

    result = await svc.start_pairing_session(
        runner=fake_runner, is_paired_fn=lambda: False, schedule_watchdog=False,
        hermes_data_dir=str(tmp_path),
    )

    assert result["started"] is True
    assert launched and launched[0][0] == dm_mod.HERMES_CONTAINER
    assert "script -qfc" in launched[0][1]
    assert (tmp_path / "platforms" / "whatsapp" / "session").is_dir()


@pytest.mark.asyncio
async def test_start_reports_exec_failure(tmp_path, needs_symlink):
    async def bad_runner(container, cmd):
        return False

    result = await svc.start_pairing_session(
        runner=bad_runner, is_paired_fn=lambda: False, schedule_watchdog=False,
        hermes_data_dir=str(tmp_path),
    )
    assert result == {"started": False, "reason": "exec_failed"}


# ---------- watchdog ----------

@pytest.mark.asyncio
async def test_watchdog_restarts_hermes_once_when_creds_appear():
    states = iter([False, True])
    restarts = []

    async def fake_restart():
        restarts.append(True)

    await svc.pairing_watchdog(
        is_paired_fn=lambda: next(states),
        restart_fn=fake_restart,
        max_polls=5,
        poll_interval=0,
    )
    assert restarts == [True]


@pytest.mark.asyncio
async def test_watchdog_times_out_without_restart():
    restarts = []

    async def fake_restart():
        restarts.append(True)

    await svc.pairing_watchdog(
        is_paired_fn=lambda: False,
        restart_fn=fake_restart,
        max_polls=3,
        poll_interval=0,
    )
    assert restarts == []


# ---------- docker exec_detached contract ----------

@pytest.mark.asyncio
async def test_exec_detached_issues_create_then_detach_start(monkeypatch):
    calls = []

    class FakeResp:
        def __init__(self, payload=None):
            self._payload = payload or {}
            self.status_code = 200
        def json(self):
            return self._payload

    async def fake_api(method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "POST" and path.endswith("/exec"):
            return FakeResp({"Id": "exec123"})
        return FakeResp({})

    monkeypatch.setattr(dm_mod, "_docker_api", fake_api)
    ok = await dm_mod.docker_manager.exec_detached("some_container", "echo hi")

    assert ok is True
    methods_paths = [(m, p.split("/")[-1]) for m, p, _ in calls]
    assert ("POST", "exec") in methods_paths
    assert ("POST", "start") in methods_paths
    create_body = calls[0][2]
    assert create_body["Cmd"] == ["bash", "-c", "echo hi"]
    start_body = calls[1][2]
    assert start_body["Detach"] is True


# ---------- POST /api/pairing/start ----------

class TestStartEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.setup import verify_api_admin

        app = FastAPI()
        app.include_router(
            __import__("importlib").import_module("app.api.whatsapp_pairing").router,
            prefix="/api/pairing",
        )
        app.dependency_overrides[verify_api_admin] = lambda: {"sub": "test"}
        return TestClient(app)

    def test_start_calls_service_and_returns_payload(self, client, monkeypatch):
        import app.api.whatsapp_pairing as api

        seen = {}

        async def fake_start(**kwargs):
            seen.update(kwargs)
            return {"started": True}

        monkeypatch.setattr(api, "start_pairing_session", fake_start)
        resp = client.post("/api/pairing/start")
        assert resp.status_code == 200
        assert resp.json() == {"started": True}

    def test_start_surfaces_refusal_reason(self, client, monkeypatch):
        import app.api.whatsapp_pairing as api

        async def fake_start(**kwargs):
            return {"started": False, "reason": "already_paired"}

        monkeypatch.setattr(api, "start_pairing_session", fake_start)
        resp = client.post("/api/pairing/start")
        assert resp.status_code == 200
        assert resp.json() == {"started": False, "reason": "already_paired"}

    def test_start_maps_exec_failure_to_502(self, client, monkeypatch):
        import app.api.whatsapp_pairing as api

        async def fake_start(**kwargs):
            return {"started": False, "reason": "exec_failed"}

        monkeypatch.setattr(api, "start_pairing_session", fake_start)
        resp = client.post("/api/pairing/start")
        assert resp.status_code == 502
