# 1. Use Hermes Native Cron for Proactive Jobs

Date: 2026-06-13

## Status

Accepted

## Context

Our previous architecture used a custom `scheduler_worker.py` and Redis delayed jobs to trigger proactive reminders (morning briefings, event reminders). With the migration to Hermes Agent as the core brain, we need to decide how proactive notifications are triggered so that Hermes retains a complete transcript and memory of the conversation.

## Decision

We will use Hermes Agent's built-in cron and scheduling functionality instead of maintaining our own `scheduler_worker.py`. Hermes will natively handle recurring schedules, delays, and cron expressions.

## Consequences

- Completely deletes `scheduler_worker.py` and the custom Redis Sorted Set scheduling layer.
- Simplifies our FastAPI infrastructure.
- Ensures all proactive actions are natively written to Hermes' Skill Documents and memory.
