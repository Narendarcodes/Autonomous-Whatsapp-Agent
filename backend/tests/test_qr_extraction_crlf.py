"""QR extraction must tolerate CR-LF pty output (wizard pairing session log).

The `hermes whatsapp` wizard runs under a pty; its captured output carries
\r characters. Art lines ending in \r previously failed the block-char check,
so QRs printed by the pairing wizard were invisible to the extractor.
"""
import importlib

svc_mod = importlib.import_module("app.services.whatsapp_pairing_service")

HEADER = "📱 Scan this QR code with WhatsApp on your phone:"


def test_extracts_block_from_crlf_pty_output(tmp_path):
    art_lines = ["▄▄▄▄▄▄▄▄▄▄▄▄▄", "█ ▄▄▄▄▄ █ ▀▄█ █", "█▄▄▄▄▄▄▄█▄▀▄█ █"]
    raw = ("\r\n" + HEADER + "\r\n\r\n" + "\r\n".join(art_lines) + "\r\n"
           + "Waiting for scan...\r\n")
    log = tmp_path / "pairing.out"
    log.write_bytes(raw.encode("utf-8"))
    qr = svc_mod.extract_latest_qr(str(log))
    assert qr is not None
    assert qr.splitlines()[0] == art_lines[0]
    assert all("\r" not in line for line in qr.splitlines())


def test_crlf_and_lf_blocks_equivalent(tmp_path):
    art = "▄▄▄▄▄▄\n█ ▄▄ █\n▄▄▄▄▄▄"
    p1 = tmp_path / "a.log"
    p1.write_text(HEADER + "\n\n" + art + "\n", encoding="utf-8")
    p2 = tmp_path / "b.log"
    p2.write_bytes((HEADER + "\r\n\r\n" + art.replace("\n", "\r\n") + "\r\n").encode("utf-8"))
    assert svc_mod.extract_latest_qr(str(p1)) == svc_mod.extract_latest_qr(str(p2))
