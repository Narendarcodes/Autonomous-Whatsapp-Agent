# AGENTS.md

This file gives AI coding agents the rules and context they need to be useful here. Read top to bottom on session start.

## Project

WhatsApp AI Agent — a self-hosted personal assistant that ties a user's
own WhatsApp account (via OpenWA, a whatsapp-web.js based service) to
Google Calendar (and over time other Google services). FastAPI + async
SQLAlchemy + Postgres + Redis Streams. The LLM does function calling
against Calendar tools; a separate scheduler worker handles proactive
reminders and briefings; a permission service DMs the owner for approval
on risky actions.

See `README.md` for full setup. See `CONTEXT.md` (once created) for
domain glossary.

## Agent skills

### Issue tracker

Issues and PRDs live on GitHub at `Narendarcodes/Autonomous-Whatsapp-Agent`.
See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical label names (`needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo. `CONTEXT.md` and `docs/adr/` live at the repo root.
See `docs/agents/domain.md`.
