# omniwa-group-privacy (Hermes-side)

Keeps owner-sensitive data out of WhatsApp **groups** when the Hermes Baileys
bridge handles messages directly (`HERMES_OWNS_WHATSAPP=true`), where the
FastAPI `group_privacy_service` never sees traffic.

## What it does

| Hook | Effect |
|---|---|
| `pre_llm_call` | Injects the GROUP PRIVACY MODE directive into group turns (ephemeral, prompt-cache safe). |
| `transform_llm_output` | Scrubs emails / phone numbers / ≥10-digit tokens from the final reply before it reaches a group. |

Group detection uses the gateway task-local contextvars
(`HERMES_SESSION_CHAT_ID` ending `@g.us`, or `:whatsapp:group:` in
`HERMES_SESSION_KEY`). DMs, CLI, cron, and api_server traffic are untouched.

## Deploy / update

```bash
# from repo root
docker cp hermes-plugin/omniwa-group-privacy whatsapp_hermes:/opt/data/plugins/
docker exec -u root whatsapp_hermes chown -R hermes:hermes /opt/data/plugins
docker restart whatsapp_hermes
```

Enable once (idempotent): `docker exec whatsapp_hermes /opt/hermes/.venv/bin/hermes plugins enable omniwa-group-privacy`

⚠️ Run any `hermes config set` / `plugins enable` as a user that can chown
afterwards — files written as `root` make the gateway (user `hermes`) fail to
parse `/opt/data/config.yaml` and silently fall back to defaults.

## Tests

```bash
docker cp hermes-plugin/omniwa-group-privacy/test_plugin.py \
  whatsapp_hermes:/opt/data/plugins/omniwa-group-privacy/test_plugin.py
docker exec whatsapp_hermes /opt/hermes/.venv/bin/python -m pytest \
  /opt/data/plugins/omniwa-group-privacy/test_plugin.py -q
```

## Related config already applied

```yaml
display:
  platforms:
    whatsapp:
      tool_progress: false   # no tool-name/preview bubbles in chats
      streaming: false       # no live-edited partial replies (leaks pre-scrub text)
```
