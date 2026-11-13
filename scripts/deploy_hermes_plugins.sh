#!/usr/bin/env bash
# Deploy omniWA's Hermes-side pieces (soul + plugins) into the running hermes
# container and ENABLE them.
#
# Why this exists: current Hermes images treat user plugins as opt-in
# (`plugins.enabled` in /opt/data/config.yaml). A plugin copied into
# /opt/data/plugins but never enabled is silently ignored — that is how
# omniwa-assist (added after the first boot) never ran.
#
# Usage:  ./scripts/deploy_hermes_plugins.sh [container-name]
set -euo pipefail

C="${1:-whatsapp_hermes}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES=/opt/hermes/.venv/bin/hermes

echo "-> soul"
docker cp "$ROOT/hermes-plugin/SPIRIT_SOUL_OMNIWA.md" "$C:/opt/data/SOUL.md"

for p in omniwa-guard omniwa-group-privacy omniwa-assist; do
  echo "-> plugin $p"
  docker exec "$C" mkdir -p /opt/data/plugins
  docker cp "$ROOT/hermes-plugin/$p" "$C:/opt/data/plugins/"
done
docker exec -u root "$C" chown -R hermes:hermes /opt/data/plugins /opt/data/SOUL.md

for p in omniwa-guard omniwa-group-privacy omniwa-assist; do
  # run as the hermes user so config.yaml keeps the right owner
  docker exec -u hermes "$C" "$HERMES" plugins enable "$p" || true
done

echo "-> plugin unit tests (throwaway container from the same Hermes image)"
# The live Hermes venv ships without pip/pytest, so test in a disposable
# container built from the exact image the gateway runs. A failure here is
# reported but never leaves the gateway un-restarted.
IMG="$(docker inspect -f '{{.Config.Image}}' "$C")"
if ! docker run --rm -u root --entrypoint sh -v "$ROOT/hermes-plugin:/plug:ro" "$IMG" -c '
  set -e
  PY=/opt/hermes/.venv/bin/python
  $PY -m ensurepip >/dev/null 2>&1 || true
  $PY -m pip install -q --disable-pip-version-check pytest pytest-asyncio >/dev/null
  # copy all first: the plugins cooperate through their sibling directories
  for p in omniwa-guard omniwa-group-privacy omniwa-assist; do cp -r /plug/$p /tmp/$p; done
  for p in omniwa-guard omniwa-group-privacy omniwa-assist; do
    (cd /tmp/$p && $PY -m pytest -q -p no:cacheprovider test_plugin.py)
  done'; then
  echo "WARNING: plugin tests failed or could not run (see output above)" >&2
  TESTS_FAILED=1
fi

echo "-> restart"
docker restart "$C" >/dev/null
[ "${TESTS_FAILED:-0}" = 1 ] && echo "done, BUT plugin tests failed." >&2
echo "done. NOTE: omniwa-assist only sees non-mention group messages when"
echo "      require_mention is OFF (dashboard → Connection Mode); with it ON the"
echo "      adapter drops them before the plugin runs."
