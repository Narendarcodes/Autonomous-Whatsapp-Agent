"""LLM agent engine with function calling.

Supports GitHub Models, Google AI Studio (Gemini), Anthropic Claude, and Ollama
through a single provider-agnostic chat loop.
"""
import json
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.redis_client import cache_get, cache_set
from app.models.models import User
from app.tools.registry import TOOL_SCHEMAS, execute_tool

logger = get_logger(__name__)


SYSTEM_PROMPT_TEMPLATE = """You are a personal WhatsApp assistant for {owner_name}.
The current date/time is {now} ({tz}).

Capabilities:
- Read the user's Google Calendar
- Create, update, delete calendar events
- Detect scheduling conflicts
- Schedule events with Google Meet video links when video calls are mentioned

Behavior rules:
1. Be terse. WhatsApp messages should be short (1-4 lines typically).
2. When the user mentions a time (e.g. "tomorrow 7pm", "Friday 3pm"), convert it to an ISO 8601 datetime in the user's timezone before calling tools.
3. If the user mentions "video call", "virtual meeting", "Google Meet", "online meeting", set create_meet_link=true.
4. When creating an event without an explicit duration, default to 1 hour.
5. After tool execution, always send a confirmation message summarizing what was done. Include any Google Meet link prominently.
6. If a tool returns an error, explain it briefly and suggest a next step.
7. {group_context}
"""


def _build_system_prompt(user: User, is_group: bool, group_id: str | None = None) -> str:
    group_context = (
        f"This message arrived in group chat {group_id}. Be aware that your reply will be visible to all group members. Stay on topic and concise."
        if is_group
        else "This message is a 1-on-1 chat with the user. You may be more personal."
    )
    return SYSTEM_PROMPT_TEMPLATE.format(
        owner_name=user.display_name or "the user",
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        tz=user.timezone or settings.TIMEZONE,
        group_context=group_context,
    )


async def _load_conversation(chat_id: str) -> list[dict]:
    raw = await cache_get(f"conv:{chat_id}")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


async def _save_conversation(chat_id: str, messages: list[dict]) -> None:
    # Keep only the last N messages
    trimmed = messages[-settings.CONVERSATION_MAX_MESSAGES :]
    await cache_set(f"conv:{chat_id}", json.dumps(trimmed), settings.CONVERSATION_TTL_SECONDS)


# ============================ PROVIDERS ============================

async def _call_github_models(messages: list[dict], tools: list[dict]) -> dict:
    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": settings.LLM_TEMPERATURE,
    }
    async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()


async def _call_google_ai(messages: list[dict], tools: list[dict]) -> dict:
    """Call Google AI Studio (Gemini) — we use the OpenAI-compatible endpoint."""
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.GOOGLE_AI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": settings.LLM_TEMPERATURE,
    }
    async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()


async def _call_llm(messages: list[dict], tools: list[dict]) -> dict:
    provider = settings.LLM_PROVIDER
    if provider == "github":
        return await _call_github_models(messages, tools)
    if provider == "google":
        return await _call_google_ai(messages, tools)
    raise NotImplementedError(f"LLM provider '{provider}' not implemented yet")


# ============================ AGENT LOOP ============================

class AgentEngine:
    async def process_message(
        self,
        db: AsyncSession,
        user: User,
        chat_id: str,
        message_text: str,
        is_group: bool = False,
        group_id: str | None = None,
    ) -> str:
        """Run the LLM tool-calling loop and return a reply string."""
        history = await _load_conversation(chat_id)

        messages: list[dict] = [
            {"role": "system", "content": _build_system_prompt(user, is_group, group_id)}
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": message_text})

        for iteration in range(settings.AGENT_MAX_ITERATIONS):
            try:
                response = await _call_llm(messages, TOOL_SCHEMAS)
            except Exception as exc:
                logger.exception("LLM call failed: %s", exc)
                return "I had trouble reaching my brain. Try again in a moment."

            choice = response["choices"][0]["message"]
            messages.append(choice)

            tool_calls = choice.get("tool_calls") or []
            if not tool_calls:
                reply = choice.get("content") or ""
                # Persist (user + assistant)
                history.append({"role": "user", "content": message_text})
                history.append({"role": "assistant", "content": reply})
                await _save_conversation(chat_id, history)
                return reply.strip() or "OK."

            # Execute each tool call sequentially
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await execute_tool(name, args, db, user, source_chat=chat_id)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result),
                    }
                )

        logger.warning("Max iterations reached for chat %s", chat_id)
        return "I tried several steps but couldn't finalize. Please rephrase or try again."


agent_engine = AgentEngine()
