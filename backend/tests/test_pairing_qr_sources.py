"""Fresh-QR lookup must span both QR sources: bridge.log AND the pairing
wizard's captured output (pairing_session.out), preferring the freshest file.

Incident context: the CLI wizard prints rotating QRs to its own pty capture,
not to platforms/whatsapp/bridge.log — so an unpaired dashboard saw nothing.
"""
import importlib
import os
import time

import pytest

svc_mod = importlib.import_module("app.services.whatsapp_pairing_service")

HEADER = "📱 Scan this QR code with WhatsApp on your phone:"
ART = "▄▄▄▄▄▄▄\n█ ▄▄ █\n▄▄▄▄▄▄▄"


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(svc_mod.settings, "HERMES_DATA_DIR", str(tmp_path))
    return tmp_path


def _mk_file(tmp_path, name, age_seconds):
    root = tmp_path / "platforms" / "whatsapp"
    root.mkdir(parents=True, exist_ok=True)
    f = root / name
    f.write_text(HEADER + "\n\n" + ART + "\n", encoding="utf-8")
    t = time.time() - age_seconds
    os.utime(f, (t, t))
    return f


def test_serves_qr_from_wizard_output_when_bridge_log_stale(tmp_path):
    _mk_file(tmp_path, "bridge.log", 3600)            # hours old
    _mk_file(tmp_path, "pairing_session.out", 5)      # fresh wizard output
    qr, captured = svc_mod.read_latest_qr()
    assert qr is not None
    assert captured is not None


def test_freshest_source_wins_when_both_fresh(tmp_path):
    old = _mk_file(tmp_path, "bridge.log", 30)
    new = _mk_file(tmp_path, "pairing_session.out", 10)
    qr, captured = svc_mod.read_latest_qr()
    assert qr is not None
    assert abs(captured and __import__("datetime").datetime.fromisoformat(
        captured).timestamp() - (time.time() - 10)) < 2.0


def test_none_when_all_sources_stale(tmp_path):
    _mk_file(tmp_path, "bridge.log", 3600)
    _mk_file(tmp_path, "pairing_session.out", 3600)
    assert svc_mod.read_latest_qr() == (None, None)


@pytest.mark.asyncio
async def test_pairing_state_uses_wizard_output(tmp_path):
    _mk_file(tmp_path, "bridge.log", 3600)
    _mk_file(tmp_path, "pairing_session.out", 8)
    state = await svc_mod.get_pairing_state(
        _health={}, hermes_data_dir=str(tmp_path), paired_override=False
    )
    assert state["qr_available"] is True
