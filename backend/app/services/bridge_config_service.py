"""Bridge config service — runtime control of the Hermes WhatsApp bridge.

Lets the dashboard switch the bridge between ``self-chat`` and ``bot`` modes
and tune policy gates WITHOUT touching the host or recomposing the stack.

Why two files?
- ``WHATSAPP_MODE`` is env-only: the gateway reads it once when it spawns the
  Node Baileys bridge (``--mode``). The hermes container command sources
  ``<HERMES_DATA_DIR>/bridge_env`` before ``exec hermes gateway``, so writing
  this file + restarting the container switches the mode.
- Policies (dm/group/require_mention/allow_from) are read from the
  ``whatsapp:`` section of ``<HERMES_DATA_DIR>/config.yaml`` (config.extra
  wins over env in gateway/platforms/whatsapp.py).

The backend container mounts the same volume (HERMES_DATA_DIR), so both files
are writable from here. Apply = write files, then restart hermes via
DockerManager (docker.sock).
"""
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_MODES = {"self-chat", "bot"}
VALID_POLICIES = {"open", "allowlist", "disabled"}

_BRIDGE_ENV_FILE = "bridge_env"
_CONFIG_FILE = "config.yaml"


def _clean(value: Any) -> str:
    return str(value or "").strip()


class BridgeConfigService:
    """File-backed view of the WhatsApp bridge configuration."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = base_dir

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    @property
    def base_dir(self) -> Path:
        root = Path(self._base_dir or settings.HERMES_DATA_DIR)
        return root

    @property
    def bridge_env_path(self) -> Path:
        return self.base_dir / _BRIDGE_ENV_FILE

    @property
    def config_path(self) -> Path:
        return self.base_dir / _CONFIG_FILE

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def get_effective(self, default_mode: str = "self-chat") -> dict:
        """Effective bridge config: bridge_env mode + config.yaml policies."""
        mode = default_mode
        if self.bridge_env_path.exists():
            for line in self.bridge_env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("WHATSAPP_MODE="):
                    mode = line.split("=", 1)[1].strip() or default_mode
                    break

        wa = self._read_whatsapp_section()
        return {
            "mode": _clean(mode),
            "dm_policy": _clean(wa.get("dm_policy")) or "allowlist",
            "group_policy": _clean(wa.get("group_policy")) or "allowlist",
            "require_mention": bool(wa.get("require_mention", True)),
            "allow_from": [str(v) for v in (wa.get("allow_from") or [])],
        }

    def _read_whatsapp_section(self) -> dict:
        if not self.config_path.exists():
            return {}
        try:
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error("Unparseable %s: %s", self.config_path, exc)
            return {}
        section = data.get("whatsapp")
        return section if isinstance(section, dict) else {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    async def apply_update(self, update: dict) -> dict:
        """Validate + persist an update; returns the applied fields.

        Raises ValueError on invalid mode/policy values. Only supplied keys
        change; everything else survives untouched.
        """
        applied: dict = {}

        if "mode" in update and update["mode"] is not None:
            mode = _clean(update["mode"])
            if mode not in VALID_MODES:
                raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
            self._upsert_bridge_env("WHATSAPP_MODE", mode)
            applied["mode"] = mode

        wa_keys = ("dm_policy", "group_policy")
        wa_update = {k: update[k] for k in wa_keys if update.get(k) is not None}
        for key in wa_keys:
            if key in wa_update:
                value = _clean(wa_update[key])
                if value not in VALID_POLICIES:
                    raise ValueError(f"{key} must be one of {sorted(VALID_POLICIES)}")

        extra_wa: dict = {}
        if "require_mention" in update and update["require_mention"] is not None:
            extra_wa["require_mention"] = bool(update["require_mention"])
        if update.get("allow_from") is not None:
            cleaned = [_clean(v) for v in update["allow_from"]]
            extra_wa["allow_from"] = [v for v in cleaned if v]

        if wa_update or extra_wa:
            current = self._read_whatsapp_section()
            merged = {**current, **wa_update, **extra_wa}
            self._write_whatsapp_section(merged)
            applied.update(wa_update)
            applied.update(extra_wa)

        return applied

    def _upsert_bridge_env(self, key: str, value: str) -> None:
        lines: list[str] = []
        if self.bridge_env_path.exists():
            lines = [
                ln for ln in self.bridge_env_path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith(f"{key}=")
            ]
        lines.append(f"{key}={value}")
        self.bridge_env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_whatsapp_section(self, section: dict) -> None:
        data: dict = {}
        if self.config_path.exists():
            try:
                data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
        data["whatsapp"] = section
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ----------------------------------------------------------------------
# Module facade (uses settings.HERMES_DATA_DIR; easy to monkeypatch)
# ----------------------------------------------------------------------
async def restart_hermes() -> bool:
    """Restart the Hermes container so written files take effect."""
    from app.services.docker_manager import docker_manager

    return await docker_manager.restart_hermes_agent()


async def get_bridge_config(default_mode: str = "self-chat") -> dict:
    svc = BridgeConfigService()
    effective = await svc.get_effective(default_mode=default_mode)
    effective["restart_supported"] = Path("/var/run/docker.sock").exists()
    return effective


async def set_bridge_config(update: dict, restart: bool = True) -> dict:
    svc = BridgeConfigService()
    applied = await svc.apply_update(update)
    needs_restart = bool(applied)
    if restart and needs_restart:
        ok = await restart_hermes()
        applied["restarted"] = ok
    return applied


bridge_config_service = BridgeConfigService()
