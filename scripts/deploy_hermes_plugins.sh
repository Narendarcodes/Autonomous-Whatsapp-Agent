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

for p in omniwa-group-privacy omniwa-assist; do
  echo "-> plugin $p"
  docker exec "$C" mkdir -p /opt/data/plugins
  docker cp "$ROOT/hermes-plugin/$p" "$C:/opt/data/plugins/"
done
docker exec -u root "$C" chown -R hermes:hermes /opt/data/plugins /opt/data/SOUL.md

for p in omniwa-group-privacy omniwa-assist; do
  # run as the hermes user so config.yaml keeps the right owner
  docker exec -u hermes "$C" "$HERMES" plugins enable "$p" || true
done

echo "-> plugin unit tests inside the container"
for p in omniwa-group-privacy omniwa-assist; do
  docker exec -u hermes "$C" /opt/hermes/.venv/bin/python -m pytest -q "/opt/data/plugins/$p/test_plugin.py"
done

echo "-> restart"
docker restart "$C" >/dev/null
echo "done. NOTE: omniwa-assist only sees non-mention group messages when"
echo "      require_mention is OFF (dashboard → Connection Mode); with it ON the"
echo "      adapter drops them before the plugin runs."
