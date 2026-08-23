"""QR staleness guard — regression for incident 2026-08-23.

The dashboard served a hours-old QR block from a frozen bridge.log stamped
with captured_at=now(), so the user scanned a long-expired code that WhatsApp
rejected. Rule: a QR is only offerable while bridge.log was modified recently;
captured_at must reflect the log mtime, never 'now'.
"""
import importlib
import os
import time

import pytest

svc_mod = importlib.import_module("app.services.whatsapp_pairing_service")


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(svc_mod.settings, "HERMES_DATA_DIR", str(tmp_path))
    return tmp_path


def _write_bridge_log(tmp_path, text):
    log = tmp_path / "platforms" / "whatsapp" / "bridge.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text, encoding="utf-8")
    return log


QR_BLOCK = (
    "\n📱 Scan this QR code with WhatsApp on your phone:\n\n"
    "▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
    "█ ▄▄▄▄▄ █ ▀▄█ ██  █ ▄▄▄▀█ ▄█\n"
    "█ █   █ █ ▄▄ ▄ ██▄▀▀▄▄█▄ ▀ █\n"
)


class TestQrStaleness:
    def test_read_latest_qr_none_when_log_is_old(self, tmp_path):
        log = _write_bridge_log(tmp_path, QR_BLOCK)
        old = time.time() - 600  # 10 minutes old
        os.utime(log, (old, old))
        assert svc_mod.read_latest_qr() == (None, None)

    def test_read_latest_qr_fresh_log_returns_block(self, tmp_path):
        log = _write_bridge_log(tmp_path, QR_BLOCK)
        qr, captured = svc_mod.read_latest_qr()
        assert qr is not None and qr.startswith("▄")
        assert captured is not None

    def test_captured_at_reflects_file_mtime_not_now(self, tmp_path):
        log = _write_bridge_log(tmp_path, QR_BLOCK)
        stamp = time.time() - 30  # fresh enough, but distinctly not 'now'
        os.utime(log, (stamp, stamp))

        _, captured = svc_mod.read_latest_qr()
        from datetime import datetime

        parsed = datetime.fromisoformat(captured)
        drift = abs(parsed.timestamp() - stamp)
        assert drift < 2.0, f"captured_at must mirror log mtime (drift={drift}s)"

    @pytest.mark.asyncio
    async def test_pairing_state_hides_stale_qr(self, tmp_path):
        log = _write_bridge_log(tmp_path, QR_BLOCK)
        old = time.time() - 600
        os.utime(log, (old, old))
        state = await svc_mod.get_pairing_state(
            _health={}, hermes_data_dir=str(tmp_path), paired_override=False
        )
        assert state["qr_available"] is False

    @pytest.mark.asyncio
    async def test_pairing_state_shows_fresh_qr(self, tmp_path):
        _write_bridge_log(tmp_path, QR_BLOCK)  # mtime = now → fresh
        state = await svc_mod.get_pairing_state(
            _health={}, hermes_data_dir=str(tmp_path), paired_override=False
        )
        assert state["qr_available"] is True
