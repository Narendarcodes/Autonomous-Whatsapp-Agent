"""Unit tests for bridge_config_service (TDD — write first, watch fail).

The service lets the dashboard switch the Hermes WhatsApp bridge between
self-chat and bot modes (plus policy gates) at runtime:

- Mode lives in ``<hermes_data>/bridge_env`` (KEY=VALUE, sourced by the
  hermes container command before `exec hermes gateway`) because WHATSAPP_MODE
  is env-only — read once when the gateway spawns the Node bridge.
- Policies (dm/group/require_mention/allow_from) live in the ``whatsapp:``
  section of ``<hermes_data>/config.yaml`` (config.extra wins over env).
- Apply = write both files, then restart the hermes container.
"""
import importlib
from pathlib import Path

import pytest
import yaml

# conftest.py already binds settings to the test env; import the real module.
svc_mod = importlib.import_module("app.services.bridge_config_service")


VALID_MODES = {"self-chat", "bot"}
VALID_POLICIES = {"open", "allowlist", "disabled"}


@pytest.fixture()
def base(tmp_path) -> Path:
    return tmp_path


@pytest.fixture()
def svc(base: Path):
    return svc_mod.BridgeConfigService(base_dir=str(base))


def _write_config(base: Path, whatsapp: dict | None = None) -> None:
    data: dict = {"model": {"provider": "x"}, "agent": {"max_turns": 90}}
    if whatsapp is not None:
        data["whatsapp"] = whatsapp
    (base / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


class TestGetEffective:
    async def test_defaults_when_no_files(self, svc, base: Path):
        eff = await svc.get_effective(default_mode="self-chat")
        assert eff["mode"] == "self-chat"
        assert eff["dm_policy"] == "allowlist"  # sane default even without config.yaml
        assert isinstance(eff["allow_from"], list)

    async def test_mode_read_from_bridge_env(self, svc, base: Path):
        (base / "bridge_env").write_text("WHATSAPP_MODE=bot\n", encoding="utf-8")
        eff = await svc.get_effective(default_mode="self-chat")
        assert eff["mode"] == "bot"

    async def test_policies_read_from_config_yaml(self, svc, base: Path):
        _write_config(base, {
            "dm_policy": "open",
            "group_policy": "disabled",
            "require_mention": False,
            "allow_from": ["916300354385", "200283032441063@lid"],
        })
        eff = await svc.get_effective(default_mode="bot")
        assert eff["dm_policy"] == "open"
        assert eff["group_policy"] == "disabled"
        assert eff["require_mention"] is False
        assert eff["allow_from"] == ["916300354385", "200283032441063@lid"]

    async def test_bridge_env_preserves_other_keys(self, svc, base: Path):
        (base / "bridge_env").write_text(
            "WHATSAPP_MODE=self-chat\nHERMES_BASE_URL=http://hermes:8642\n",
            encoding="utf-8",
        )
        await svc.apply_update({"mode": "bot"})
        raw = (base / "bridge_env").read_text(encoding="utf-8")
        assert "WHATSAPP_MODE=bot" in raw
        assert "HERMES_BASE_URL=http://hermes:8642" in raw


class TestApplyUpdate:
    async def test_writes_mode_and_policies(self, svc, base: Path):
        _write_config(base, {"dm_policy": "allowlist"})
        changed = await svc.apply_update({
            "mode": "bot",
            "dm_policy": "open",
            "group_policy": "allowlist",
            "require_mention": True,
            "allow_from": ["916300354385"],
        })
        assert changed["mode"] == "bot"
        eff = await svc.get_effective(default_mode="self-chat")
        assert eff["mode"] == "bot"
        assert eff["dm_policy"] == "open"
        assert eff["group_policy"] == "allowlist"
        assert eff["require_mention"] is True

    async def test_partial_update_touches_only_given_keys(self, svc, base: Path):
        _write_config(base, {"dm_policy": "open", "group_policy": "disabled"})
        await svc.apply_update({"mode": "self-chat", "dm_policy": "allowlist"})
        eff = await svc.get_effective(default_mode="self-chat")
        # untouched key survives
        assert eff["group_policy"] == "disabled"
        assert eff["dm_policy"] == "allowlist"

    async def test_config_yaml_other_sections_survive(self, svc, base: Path):
        _write_config(base, {"dm_policy": "allowlist"})
        await svc.apply_update({"mode": "bot"})
        data = yaml.safe_load((base / "config.yaml").read_text(encoding="utf-8"))
        assert data["model"]["provider"] == "x"
        assert data["agent"]["max_turns"] == 90
        assert "whatsapp" in data

    async def test_invalid_mode_rejected(self, svc, base: Path):
        with pytest.raises(ValueError, match="mode"):
            await svc.apply_update({"mode": "dual_number"})

    async def test_invalid_policy_rejected(self, svc, base: Path):
        with pytest.raises(ValueError, match="dm_policy"):
            await svc.apply_update({"dm_policy": "yolo"})

    async def test_empty_update_is_noop_success(self, svc, base: Path):
        changed = await svc.apply_update({})
        assert changed == {}

    async def test_allow_from_normalizes_whitespace(self, svc, base: Path):
        _write_config(base, {})
        await svc.apply_update({"allow_from": ["  916300354385 ", ""]})
        eff = await svc.get_effective(default_mode="self-chat")
        assert eff["allow_from"] == ["916300354385"]


class TestApiContract:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Bypass dashboard auth for contract testing
        from app.api.setup import verify_api_admin

        app = FastAPI()
        app.include_router(importlib.import_module("app.api.whatsapp_pairing").router,
                           prefix="/api/pairing")
        app.dependency_overrides[verify_api_admin] = lambda: {"sub": "test"}
        return TestClient(app)

    def test_get_bridge_config(self, client, monkeypatch, tmp_path):
        import app.api.whatsapp_pairing as api

        async def fake_get(default_mode="self-chat"):
            return {"mode": "bot", "dm_policy": "allowlist",
                    "group_policy": "allowlist", "require_mention": True,
                    "allow_from": ["916300354385"], "restart_supported": True}

        monkeypatch.setattr(api, "get_bridge_config", fake_get)
        resp = client.get("/api/pairing/bridge")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "bot"
        assert "restart_supported" in body

    def test_put_bridge_config_applies(self, client, monkeypatch):
        import app.api.whatsapp_pairing as api
        calls = {}

        async def fake_set(update, restart=True):
            calls.update(update)
            return {**update, "restarted": True}

        monkeypatch.setattr(api, "set_bridge_config", fake_set)
        resp = client.put("/api/pairing/bridge", json={"mode": "bot"})
        assert resp.status_code == 200
        assert calls == {"mode": "bot"}
        assert resp.json()["restarted"] is True

    def test_put_rejects_invalid_mode(self, client, monkeypatch):
        resp = client.put("/api/pairing/bridge", json={"mode": "dual_number"})
        assert resp.status_code == 400
        assert "mode" in resp.json()["detail"].lower()

    def test_put_rejects_invalid_policy(self, client, monkeypatch):
        resp = client.put("/api/pairing/bridge", json={"dm_policy": "yolo"})
        assert resp.status_code == 400

    def test_put_rejects_empty_body(self, client, monkeypatch):
        import app.api.whatsapp_pairing as api
        async def fake_set(update, restart=True):  # pragma: no cover - must not run
            raise AssertionError("empty update must be rejected before service")

        monkeypatch.setattr(api, "set_bridge_config", fake_set)
        resp = client.put("/api/pairing/bridge", json={})
        assert resp.status_code == 400


class TestModuleLevelHelpers:
    async def test_module_facade_uses_settings_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(svc_mod.settings, "HERMES_DATA_DIR", str(tmp_path))
        restarted = []

        async def fake_restart():
            restarted.append(True)
            return True

        monkeypatch.setattr(svc_mod, "restart_hermes", fake_restart)
        await svc_mod.set_bridge_config({"mode": "bot"})
        assert restarted == [True]
        eff = await svc_mod.get_bridge_config(default_mode="self-chat")
        assert eff["mode"] == "bot"
