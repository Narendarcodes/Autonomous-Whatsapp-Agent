"""SQLAlchemy ORM models."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    wa_phone: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    google_access_token_enc: Mapped[str | None] = mapped_column(Text)
    google_refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    google_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventCache(Base):
    __tablename__ = "events_cache"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    google_event_id: Mapped[str] = mapped_column(String(256), index=True)
    summary: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(512))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attendees: Mapped[list | None] = mapped_column(JSON, default=list)
    meet_link: Mapped[str | None] = mapped_column(Text)
    source_chat: Mapped[str | None] = mapped_column(String(128))  # group JID or sender phone
    source_message_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "google_event_id", name="uq_user_event"),
        Index("idx_events_start", "user_id", "start_time"),
    )


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    event_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("events_cache.id", ondelete="CASCADE"))
    reminder_type: Mapped[str] = mapped_column(String(32))  # 15min, 1hour, 1day, briefing, summary, conflict, weekly
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed|cancelled
    payload: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PendingDecision(Base):
    """Permission requests awaiting owner confirmation via DM."""

    __tablename__ = "pending_decisions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    short_code: Mapped[str] = mapped_column(String(8), unique=True, index=True)  # e.g. "A1B2"
    action_type: Mapped[str] = mapped_column(String(32))  # create_event|delete_event|group_reply|update_event
    proposed_action: Mapped[dict] = mapped_column(JSON)
    source_chat: Mapped[str | None] = mapped_column(String(128))  # where the trigger came from
    status: Mapped[str] = mapped_column(String(16), default="awaiting")  # awaiting|approved|rejected|expired
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[str] = mapped_column(String(128), index=True)  # DM phone or group JID
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
