"""OutboundRouter selection matrix — the seam's contract (candidate 2)."""
import pytest

from app.outbound.base import DeliveryResult, WhatsAppOutbound
from app.outbound.router import OutboundRouter


class FakeAdapter(WhatsAppOutbound):
    def __init__(self, name: str, ok: bool = True, explode: bool = False):
        self.name = name
        self.ok = ok
        self.explode = explode
        self.calls: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str, *, session_hint=None) -> DeliveryResult:
        if self.explode:
            raise RuntimeError("transport exploded")
        self.calls.append((chat_id, text))
        return DeliveryResult(ok=self.ok)


def make_router(mode="self_chat", primary=None, agent=None):
    async def resolver():
        return mode

    return OutboundRouter(primary=primary or FakeAdapter("primary"),
                          agent=agent or FakeAdapter("agent"),
                          mode_resolver=resolver)


@pytest.mark.asyncio
async def test_agent_session_hint_routes_to_agent():
    r = make_router()
    await r.send("1555@c.us", "hi", session_hint="agent-session")
    assert r._agent.calls and not r._primary.calls


@pytest.mark.asyncio
async def test_dual_number_mode_routes_to_agent():
    r = make_router(mode="dual_number")
    await r.send("1555@c.us", "hi", session_hint="my-session")
    assert r._agent.calls and not r._primary.calls


@pytest.mark.asyncio
async def test_default_routes_to_primary():
    r = make_router(mode="self_chat")
    await r.send("1555@c.us", "hi", session_hint="my-session")
    assert r._primary.calls and not r._agent.calls


@pytest.mark.asyncio
async def test_adapter_failure_is_a_value_not_exception():
    primary = FakeAdapter("primary", ok=False)
    r = make_router(primary=primary)
    result = await r.send("1555@c.us", "hi")
    assert result.ok is False
    assert len(primary.calls) == 1  # dispatch attempted exactly once


@pytest.mark.asyncio
async def test_adapter_exception_never_propagates():
    primary = FakeAdapter("primary", explode=True)
    r = make_router(primary=primary)
    result = await r.send("1555@c.us", "hi")
    assert result.ok is False
    assert "raised" in (result.detail or "")


@pytest.mark.asyncio
async def test_empty_target_rejected_without_dispatch():
    r = make_router()
    result = await r.send("", "hi")
    assert result.ok is False
    assert r._primary.calls == [] and r._agent.calls == []
