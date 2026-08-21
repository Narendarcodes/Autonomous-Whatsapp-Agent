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
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)  # NULL = legacy single-tenant rows
    wa_phone: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    has_permission: Mapped[bool] = mapped_column(Boolean, default=False)  # Owner grants permission to use bot
    trust_level: Mapped[str] = mapped_column(String(16), default="trusted")
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


class ChatACL(Base):
    __tablename__ = "chat_acl"

    chat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(16), default="silent_log")
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SenderACL(Base):
    __tablename__ = "sender_acl"

    sender_phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    trust_level: Mapped[str] = mapped_column(String(16), default="unknown")
    display_name: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="explicit")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_pref_key"),)


class PreferenceProposal(Base):
    __tablename__ = "preference_proposals"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    action_class: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    confirmation_count: Mapped[int] = mapped_column(default=0)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    declined_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "action_class", "target_id", name="uq_proposal"),
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[str] = mapped_column(String(128), index=True)  # DM phone or group JID
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # groq, openrouter, gemini
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Tenant(Base):
    """A business customer of omniWA. One tenant = one WhatsApp number + dashboard + token namespace."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)  # Hermes profile key
    whatsapp_session_ref: Mapped[str | None] = mapped_column(String(128))  # Hermes wa session/profile ref
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DashboardUser(Base):
    """Human who logs into the web dashboard (per-tenant). argon2 password hash."""

    __tablename__ = "dashboard_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_tenant_email"),)


class CustomerGoogleToken(Base):
    """Per-customer Google OAuth tokens — encrypted at rest, tenant-scoped.

    omniWA owns these credentials; Hermes never holds a shared global token
    (prevents the cross-tenant Workspace leak).
    """

    __tablename__ = "customer_google_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    user_wa_phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"))
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(Text)  # space-joined granted scopes
    email: Mapped[str | None] = mapped_column(String(255))  # Google account email
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_wa_phone", name="uq_tenant_phone_token"),
        Index("idx_cgt_tenant_phone", "tenant_id", "user_wa_phone"),
    )

