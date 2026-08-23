"""Unit tests for whatsapp_pairing_service (TDD — write first, watch fail)."""
import importlib

import pytest

# conftest.py already binds settings to the test env; import the real modules.
from app.core.config import settings

svc_mod = importlib.import_module("app.services.whatsapp_pairing_service")


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(svc_mod.settings, "HERMES_DATA_DIR", str(tmp_path))
    return tmp_path


def _write_bridge_log(tmp_path: "Path", text: str) -> None:
    log = tmp_path / "platforms" / "whatsapp" / "bridge.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text, encoding="utf-8")


QR_BLOCK = (
    "\n📱 Scan this QR code with WhatsApp on your phone:\n\n"
    "▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
    "█ ▄▄▄▄▄ █ ▀▄█ ██  █ ▄▄▄▀█ ▄█\n"
    "█ █   █ █ ▄▄ ▄ ██▄▀▀▄▄█▄ ▀ █\n"
    "█ █▄▄▄█ █▄█▄▄ █ ▀▀ ▀ ▀▄█ █▀ ▄\n"
    "█▄▄▄▄▄▄▄█▄▀▄█ █▄█▄▀ ▀▄▀ ▀▄█\n"
)


class TestExtractLatestQr:
    def test_returns_last_complete_block(self, tmp_path):
        _write_bridge_log(tmp_path, QR_BLOCK + "Waiting for scan...\n" + QR_BLOCK + "Waiting for scan...\n")
        qr = svc_mod.extract_latest_qr(str(tmp_path / "platforms" / "whatsapp" / "bridge.log"))
        assert qr is not None
        assert qr.startswith("▄")
        assert qr.endswith("█")

    def test_incomplete_block_is_ignored(self, tmp_path):
        partial = QR_BLOCK.splitlines()[0] + "\n"  # header line only, never closed
        _write_bridge_log(tmp_path, partial + "Waiting for scan...\n")
        assert svc_mod.extract_latest_qr(str(tmp_path / "platforms" / "whatsapp" / "bridge.log")) is None

    def test_missing_log_returns_none(self, tmp_path):
        assert svc_mod.extract_latest_qr(str(tmp_path / "nope" / "bridge.log")) is None


class TestPairingState:
    def _mk(self, tmp_path, creds=False, health=None):
        if creds:
            creds_p = tmp_path / "platforms" / "whatsapp" / "session" / "creds.json"
            creds_p.parent.mkdir(parents=True, exist_ok=True)
            creds_p.write_text("{}", encoding="utf-8")

        async def fake_health():
            return health or {}

        return fake_health

    @pytest.mark.asyncio
    async def test_unpaired_with_fresh_qr(self, tmp_path):
        _write_bridge_log(tmp_path, QR_BLOCK)
        state = await svc_mod.get_pairing_state(_health={}, hermes_data_dir=str(tmp_path))
        assert state["paired"] is False
        assert state["qr_available"] is True
        assert "qr" not in state           # omitted from state; fetched separately
        assert state["error_code"] is None or state["error_code"] == ""

    @pytest.mark.asyncio
    async def test_paired_ignores_stale_log(self, tmp_path):
        _write_bridge_log(tmp_path, QR_BLOCK)   # old QR left in log after pairing
        state = await svc_mod.get_pairing_state(_health=None, hermes_data_dir=str(tmp_path), paired_override=True)
        assert state["paired"] is True
        assert "qr" not in state and state["qr_available"] is False

    @pytest.mark.asyncio
    async def test_health_error_surfaced_when_unpaired(self, tmp_path):
        state = await svc_mod.get_pairing_state(
            _health={"platforms": {"whatsapp": {"state": "failed", "error_code": "whatsapp_not_paired"}}},
            hermes_data_dir=str(tmp_path),
            paired_override=False,
        )
        assert state["error_code"] == "whatsapp_not_paired"


class TestApiContract:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Bypass dashboard auth for contract testing
        from app.api.setup import verify_api_admin

        app = FastAPI()
        app.include_router(importlib.import_module("app.api.whatsapp_pairing").router,
                           prefix="/api/pairing")
        app.dependency_overrides[verify_api_admin] = lambda: {"sub": "test"}
        return TestClient(app)

    def test_status_shape(self, client, tmp_path, monkeypatch):
        import app.api.whatsapp_pairing as api
        async def fake_state():
            return {"paired": True, "qr_available": False,
                    "bot_number": "", "error_code": "", "checked_at": "now"}
        monkeypatch.setattr(api.pairing_service, "get_pairing_state", fake_state)

        resp = client.get("/api/pairing/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"paired", "qr_available", "bot_number", "error_code"}
        assert "qr" not in body            # status must NOT carry the QR payload

    def test_qr_endpoint_returns_data_url(self, client, tmp_path, monkeypatch):
        import app.api.whatsapp_pairing as api
        monkeypatch.setattr(api.pairing_service, "read_latest_qr",
                            lambda: ("▄▄█▄▄", "2026-08-22T13:00:00Z"))

        resp = client.get("/api/pairing/qr")
        assert resp.status_code == 200
        body = resp.json()
        assert body["format"] == "unicode_blocks"
        assert body["qr"].startswith("▄")
        assert body["captured_at"]
