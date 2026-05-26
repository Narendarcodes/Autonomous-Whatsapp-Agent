"""Hermes Agent client — replaces the custom LLM agent_engine.py.

Hermes Agent (github.com/NousResearch/hermes-agent) is a self-hosted
OpenAI-compatible agent runtime with persistent memory.

Key difference from the old engine:
- NO tools= in the request. Hermes calls our tools via the MCP server.
- Memory persists across sessions via X-Hermes-Session-Id header.
- The LLM itself is resolved by LiteLLM (the model fallback chain).

Public interface is identical to the old agent_engine.py:
    agent_engine.process_message(db, user, chat_id, message, is_group, group_id) -> str
"""
import json
from datetime import datetime

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.redis_client import cache_get, cache_set
from app.models.models import User

logger = get_logger(__name__)

SYSTEM_PROMPT = """\
You are a personal WhatsApp assistant. Current time: {now} ({tz}).

Rules:
- Keep replies SHORT (1-4 lines). WhatsApp is not email.
- Convert relative times ("tomorrow 7pm", "next Friday") to ISO 8601 in {tz} before calling tools.
- For video calls / virtual meetings, set create_meet_link=true.
- Default meeting duration: 1 hour unless the user specifies otherwise.
- After any tool call, confirm what was done in plain language. Include Meet links prominently.
- {context}
"""


class HermesEngine:
    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=f"{settings.HERMES_BASE_URL}/v1",
                api_key=settings.HERMES_API_KEY,
                timeout=settings.AGENT_TIMEOUT_SECONDS,
            )
        return self._client

    async def process_message(
        self,
        db: AsyncSession,
        user: User,
        chat_id: str,
        message_text: str,
        is_group: bool = False,
        group_id: str | None = None,
    ) -> str:
        context = (
            f"This message is from group chat {group_id}. Reply will be visible to all members — be concise and on-topic."
            if is_group
            else "This is a 1-on-1 chat with the owner. You may be more personal."
        )

        system = SYSTEM_PROMPT.format(
            now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tz=user.timezone or settings.TIMEZONE,
            context=context,
        )

        # Load conversation history for non-group chats (groups stay stateless)
        messages: list[dict] = [{"role": "system", "content": system}]
        if not is_group:
            history = await self._load_history(chat_id)
            messages.extend(history)
        messages.append({"role": "user", "content": message_text})

        client = self._get_client()
        try:
            response = await client.chat.completions.create(
                model="hermes-llm",          # LiteLLM virtual model name
                messages=messages,
                temperature=settings.LLM_TEMPERATURE,
                extra_headers={
                    # Hermes uses this to retrieve the right memory context
                    "X-Hermes-Session-Id": f"chat-{chat_id}",
                },
            )
        except Exception as exc:
            logger.exception("Hermes API call failed: %s", exc)
            return "I'm having trouble thinking right now. Try again in a moment."

        reply = (response.choices[0].message.content or "").strip()
        if not reply:
            return "OK."

        # Persist history for DMs
        if not is_group:
            await self._save_history(
                chat_id,
                messages + [{"role": "assistant", "content": reply}],
            )

        return reply

    async def _load_history(self, chat_id: str) -> list[dict]:
        raw = await cache_get(f"conv:{chat_id}")
        if not raw:
            return []
        try:
            history = json.loads(raw)
            # Drop system messages from stored history (we prepend fresh each call)
            return [m for m in history if m.get("role") != "system"]
        except Exception:
            return []

    async def _save_history(self, chat_id: str, messages: list[dict]) -> None:
        trimmed = [m for m in messages if m.get("role") != "system"]
        trimmed = trimmed[-settings.CONVERSATION_MAX_MESSAGES:]
        await cache_set(
            f"conv:{chat_id}",
            json.dumps(trimmed),
            settings.CONVERSATION_TTL_SECONDS,
        )


agent_engine = HermesEngine()
