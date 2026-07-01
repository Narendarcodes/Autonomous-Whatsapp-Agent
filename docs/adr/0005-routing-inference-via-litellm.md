# 5. Routing LLM Inference via LiteLLM

Date: 2026-06-13

## Status

Accepted

## Context

To ensure the agent engine (Hermes) maintains high availability while targeting a zero-cost infrastructure, we need a mechanism to gracefully failover between free-tier API providers if one hits rate limits or goes down.

## Decision

We will run LiteLLM alongside Hermes as a local proxy container. Hermes will be configured to point its completion requests to `http://litellm:4000/v1` instead of directly to an internet provider provider.

LiteLLM will be configured with a fallback chain focusing on zero-cost tiers:
1. GitHub Models (Primary)
2. Google AI Studio (Gemini 2.0 Flash)
3. Groq

## Consequences

- 100% uptime for reasoning despite using free-tier APIs.
- Centralized tracking of token usage and API limits.
- If we ever need to introduce paid models later, we can do so in LiteLLM's `config.yaml` without touching Hermes or FastAPI.