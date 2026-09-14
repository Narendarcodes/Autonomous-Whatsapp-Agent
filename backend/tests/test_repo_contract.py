"""Repo-contract gate: compose profiles, env mirror, streams, schema doc.

Stdlib only (ast/re/pathlib/subprocess) so it runs WITHOUT live Postgres,
Redis, or heavy ML deps — no services needed, locally or in CI.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
COMPOSE = REPO / "docker" / "docker-compose.yml"
ENV_EXAMPLE = BACKEND / ".env.example"
CONFIG = BACKEND / "app" / "core" / "config.py"
STREAMS = BACKEND / "app" / "intake" / "streams.py"
MODELS = BACKEND / "app" / "models" / "models.py"
SCHEMA_DOC = REPO / "DATABASE_SCHEMA.md"

LEAN_SERVICES = {"postgres", "redis", "openwa", "backend"}
FULL_SERVICES = {"litellm", "hermes", "mcp-server", "tunnel", "whisper-api", "kokoro-api"}

# New tables must extend this set AND DATABASE_SCHEMA.md (freshness by design).
EXPECTED_TABLES = {
    "observed_contacts",
    "users",
    "events_cache",
    "reminders",
    "pending_decisions",
    "audit_log",
    "chat_acl",
    "sender_acl",
    "user_preferences",
    "preference_proposals",
    "sessions",
    "api_keys",
    "tenants",
    "dashboard_users",
    "customer_google_tokens",
}


def _services_block() -> str:
    text = COMPOSE.read_text(encoding="utf-8")
    m = re.search(r"^services:\n(.*?)^(?:volumes|networks|name):", text, re.M | re.S)
    assert m, "services block not found"
    return m.group(1)


def _service_block(name: str) -> str:
    block = _services_block()
    start = block.index(f"\n  {name}:")
    rest = block[start + 1 :]
    nxt = re.search(r"^  [a-z0-9][a-z0-9-]*:", rest[len(f"  {name}:") + 1 :], re.M)
    return rest[: nxt.start() + len(f"  {name}:") + 1] if nxt else rest


def test_compose_service_sets():
    names = set(re.findall(r"^  ([a-z0-9][a-z0-9-]*):", _services_block(), re.M))
    assert names == LEAN_SERVICES | FULL_SERVICES


def test_full_services_profiled_lean_not():
    for name in FULL_SERVICES:
        assert "profiles:" in _service_block(name) and "full" in _service_block(name), name
    for name in LEAN_SERVICES:
        assert "profiles:" not in _service_block(name), name


def test_backend_hermes_dependency_optional():
    dep = _service_block("backend")
    assert "required: false" in dep, "backend must boot in lean deploys without hermes"


def _settings_fields() -> set[str]:
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    fields = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.add(stmt.target.id)
    return fields - {"model_config"}


def _env_example_keys() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)\s*=", line)
        if m:
            keys.add(m.group(1))
    return keys


def test_settings_mirrored_in_env_example():
    missing = _settings_fields() - _env_example_keys()
    assert not missing, f"Settings without .env.example entry: {sorted(missing)}"


def _str_assigns(path: Path, names: set[str]) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in names
            and isinstance(node.value, ast.Constant)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def test_intake_stream_constants():
    consts = _str_assigns(
        STREAMS, {"STREAM", "GROUP", "DEAD_STREAM", "PENDING_KEY", "IDEM_PREFIX", "RL_PREFIX"}
    )
    assert set(consts) == {"STREAM", "GROUP", "DEAD_STREAM", "PENDING_KEY", "IDEM_PREFIX", "RL_PREFIX"}
    assert all(v for v in consts.values()), "empty stream/key constant"
    assert len(set(consts.values())) == len(consts), "stream/key constants must be distinct"
    assert consts["STREAM"].startswith("omniwa:")


def test_tables_match_models_and_doc():
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    tables = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    }
    assert tables == EXPECTED_TABLES, f"model drift: {sorted(tables ^ EXPECTED_TABLES)}"
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    missing = [t for t in sorted(tables) if t not in doc]
    assert not missing, f"tables missing from DATABASE_SCHEMA.md: {missing}"


def test_live_env_files_ignored():
    if not shutil.which("git"):
        return
    for rel in ("backend/.env", "docker/.env"):
        assert (REPO / rel).exists(), f"{rel} missing (dev needs it; CI uses service env)"
        proc = subprocess.run(
            ["git", "check-ignore", rel], cwd=REPO, capture_output=True, text=True
        )
        assert proc.returncode == 0, f"{rel} is NOT gitignored — live secrets risk"
