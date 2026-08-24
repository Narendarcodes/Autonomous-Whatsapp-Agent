"""Intake runtime wiring — singletons shared by lifespan and router.

Production composition root for the Inbox seam:
    get_inbox()        -> StreamsInbox wired from settings
    start_consumer()   -> StreamConsumer running MessagePipeline
    stop_consumer()    -> graceful shutdown on lifespan exit
"""
from __future__ import annotations

import logging
import os

from app.core.config import settings
from app.intake.stages import MessagePipeline
from app.intake.streams import ConsumerConfig, StreamConsumer, StreamsInbox

logger = logging.getLogger(__name__)

_inbox: StreamsInbox | None = None
_consumer: StreamConsumer | None = None


def get_inbox() -> StreamsInbox:
    global _inbox
    if _inbox is None:
        _inbox = StreamsInbox(
            rate_limit_requests=settings.RATE_LIMIT_REQUESTS,
            rate_limit_window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
            max_pending_per_chat=5,
        )
    return _inbox


def _consumer_name() -> str:
    # container hostname keeps consumer identity stable across restarts,
    # which makes PENDING re-claim and idle detection predictable.
    return (os.getenv("HOSTNAME") or "omniwa-c1")[:64]


async def start_consumer() -> None:
    global _consumer
    if _consumer is not None:
        return
    _consumer = StreamConsumer(
        MessagePipeline(),
        ConsumerConfig(consumer_name=_consumer_name()),
    )
    await _consumer.start()


async def stop_consumer() -> None:
    global _consumer
    if _consumer is not None:
        await _consumer.stop()
        _consumer = None


def reset_for_tests() -> None:
    """Tear down singletons between test modules (not for production use)."""
    global _inbox, _consumer
    _inbox = None
    _consumer = None
