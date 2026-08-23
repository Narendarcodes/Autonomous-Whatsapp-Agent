"""Headless WhatsApp re-pairing driver.

Problem this solves (incident 2026-08-23): when the Baileys session is gone,
the Hermes gateway refuses to spawn the bridge ("Run `hermes whatsapp`"), and
that CLI wizard demands an interactive TTY and crashes on a dangling session
symlink. A dashboard user could therefore never re-pair without an operator.

Flow implemented here:
1. `ensure_wizard_paths` — make the legacy path the wizard uses
   (`<data>/whatsapp/session`) a valid symlink to the real gateway session dir.
2. `build_pairing_command` — a headless shell command that kills any previous
   wizard, truncates stale QR output, and runs `hermes whatsapp` under
   `script -qfc` (pty) with scripted answers, capturing rotating QRs to
   `<data>/platforms/whatsapp/pairing_session.out` on the shared volume.
3. `start_pairing_session` — gates on paired state, launches via docker
   exec-detached, and schedules `pairing_watchdog`, which restarts hermes once
   credentials appear so the gateway adopts the new session.

The QR side of this contract lives in whatsapp_pairing_service (reads
pairing_session.out as a fresh-QR source).
"""
import asyncio
import os

from app.core.config import settings
from app.core.logging import get_logger
from app.services.docker_manager import HERMES_CONTAINER, docker_manager

logger = get_logger(__name__)

_HERMES_BIN = "/opt/hermes/.venv/bin/hermes"
# Bracket trick: matches "hermes whatsapp" processes but never our own command line.
_KILL_PREVIOUS = 'pkill -f "hermes[ ]whatsapp" 2>/dev/null'
_WATCHDOG_MAX_POLLS = 100       # ~5 min at 3s intervals
_WATCHDOG_INTERVAL_S = 3.0


def _paths(hermes_data_dir: str | None = None) -> dict:
    base = hermes_data_dir or getattr(settings, "HERMES_DATA_DIR", "/opt/hermes_data")
    root = f"{base.rstrip('/')}/platforms/whatsapp"
    return {
        "session": f"{root}/session",
        "legacy_link": f"{base.rstrip('/')}/whatsapp/session",
        "pairing_out": f"{root}/pairing_session.out",
    }


def ensure_wizard_paths(hermes_data_dir: str | None = None) -> None:
    """Guarantee the wizard's legacy symlink points at a live session dir."""
    p = _paths(hermes_data_dir)
    os.makedirs(p["session"], exist_ok=True)
    os.makedirs(os.path.dirname(p["legacy_link"]), exist_ok=True)

    link = p["legacy_link"]
    if os.path.islink(link):
        if os.path.realpath(link) == os.path.realpath(p["session"]):
            return  # already correct — touch nothing
        os.remove(link)  # wrong target or dangling → replace
        os.symlink(p["session"], link)
    elif not os.path.exists(link):
        # Dangling symlinks report exists()==False but islink()==True was caught
        # above; plain missing → create.
        if os.path.islink(link):
            os.remove(link)
        os.symlink(p["session"], link)
    # else: a REAL directory occupies the legacy path — never destroy data;
    # the wizard will use it directly and Baileys creds land there.


def build_pairing_command(hermes_data_dir: str | None = None) -> str:
    """Headless one-liner that yields rotating QRs into pairing_session.out."""
    out = _paths(hermes_data_dir)["pairing_out"]
    return (
        f"{_KILL_PREVIOUS}; "
        f": > \"{out}\"; "
        f"printf 'n\\ny\\n' | script -qfc \"{_HERMES_BIN} whatsapp\" \"{out}\""
    )


async def pairing_watchdog(
    *,
    is_paired_fn,
    restart_fn,
    max_polls: int = _WATCHDOG_MAX_POLLS,
    poll_interval: float = _WATCHDOG_INTERVAL_S,
) -> None:
    """After a scan succeeds (creds appear), restart hermes exactly once so
    the gateway adopts the new session; give up quietly on timeout."""
    for _ in range(max_polls):
        await asyncio.sleep(poll_interval)
        try:
            if is_paired_fn():
                logger.info("Pairing watchdog: creds detected — restarting hermes")
                ok = await restart_fn()
                logger.info("Pairing watchdog: hermes restart %s", "ok" if ok else "FAILED")
                return
        except Exception as exc:  # noqa: BLE001 — watchdog must never crash the app
            logger.warning("Pairing watchdog poll error: %s", exc)
    logger.info("Pairing watchdog: timed out waiting for credentials")


async def start_pairing_session(
    *,
    runner=None,
    is_paired_fn=None,
    restart_fn=None,
    schedule_watchdog: bool = True,
    hermes_data_dir: str | None = None,
) -> dict:
    """Kick off a fresh pairing session usable from the dashboard.

    runner: async (container_name, command) -> bool   [docker exec detached]
    is_paired_fn: () -> bool                          [creds.json presence]
    restart_fn:   async () -> bool                    [hermes container restart]
    """
    from app.services.whatsapp_pairing_service import is_paired

    runner = runner or docker_manager.exec_detached
    is_paired_fn = is_paired_fn or (lambda: is_paired(hermes_data_dir))
    restart_fn = restart_fn or docker_manager.restart_hermes_agent

    if is_paired_fn():
        return {"started": False, "reason": "already_paired"}

    await asyncio.to_thread(ensure_wizard_paths, hermes_data_dir)

    ok = await runner(HERMES_CONTAINER, build_pairing_command(hermes_data_dir))
    if not ok:
        return {"started": False, "reason": "exec_failed"}

    logger.info("Pairing session started in %s", HERMES_CONTAINER)
    if schedule_watchdog:
        asyncio.create_task(
            pairing_watchdog(is_paired_fn=is_paired_fn, restart_fn=restart_fn)
        )
    return {"started": True}
