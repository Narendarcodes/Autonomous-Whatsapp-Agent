"""
SQLAlchemy Models
Database table definitions
"""

from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey, JSON, Index
from sqlalchemy.dialects.postgresql import UUID, ENUM as PG_ENUM
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime
import uuid
import enum

from app.db.database import Base


class EventStatus(enum.Enum):
    """Event status enumeration"""
    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class DecisionState(enum.Enum):
    """Pending decision state enumeration"""
    WAITING_FOR_USER = "waiting_for_user"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


# PostgreSQL ENUM types - use values (lowercase) to match DB
EventStatusPG = PG_ENUM(
    'tentative', 'confirmed', 'cancelled',
    name='eventstatus',
    create_type=False  # Already exists in DB
)

DecisionStatePG = PG_ENUM(
    'waiting_for_user', 'resolved', 'cancelled',
    name='decisionstate',
    create_type=False  # Already exists in DB
)


class User(Base):
    """User model for WhatsApp users with Google OAuth"""
    
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wa_phone = Column(String(20), unique=True, nullable=False, index=True)
    google_refresh_token = Column(Text, nullable=True)
    google_access_token = Column(Text, nullable=True)
    last_auth_time = Column(DateTime, nullable=True)
    timezone = Column(String(50), default="UTC", nullable=False)  # User's timezone (e.g., "Asia/Kolkata")
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    events = relationship("EventCache", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="user")
    reminders = relationship("Reminder", back_populates="user", cascade="all, delete-orphan")
    pending_decisions = relationship("PendingDecision", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(id={self.id}, wa_phone={self.wa_phone})>"
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "wa_phone": self.wa_phone,
            "has_google_auth": bool(self.google_refresh_token),
            "last_auth_time": self.last_auth_time.isoformat() if self.last_auth_time else None,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class EventCache(Base):
    """Cache for Google Calendar events"""
    
    __tablename__ = "events_cache"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    google_event_id = Column(String(255), nullable=False, index=True)
    summary = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    location = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False, index=True)
    attendees = Column(JSON, nullable=True)  # Array of attendee emails
    status = Column(EventStatusPG, default='confirmed', nullable=False, index=True)
    
    # Recurring event fields
    is_recurring = Column(Boolean, default=False, nullable=False, index=True)
    recurrence_rule = Column(String(500), nullable=True)  # RRULE string for master events
    recurring_event_id = Column(String(255), nullable=True, index=True)  # Links instances to master event
    instance_date = Column(DateTime, nullable=True)  # Specific instance date for recurring events
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="events")
    reminders = relationship("Reminder", back_populates="event", cascade="all, delete-orphan")
    
    # Property alias for event_id (maps to google_event_id)
    @property
    def event_id(self):
        """Alias for google_event_id for compatibility"""
        return self.google_event_id
    
    @event_id.setter
    def event_id(self, value):
        """Setter for event_id alias"""
        self.google_event_id = value
    
    __table_args__ = (
        Index('idx_event_user_time', 'user_id', 'start_time'),
        Index('idx_event_user_status', 'user_id', 'status'),
    )
    
    def __repr__(self):
        return f"<EventCache(id={self.id}, summary={self.summary}, start_time={self.start_time})>"
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "google_event_id": self.google_event_id,
            "summary": self.summary,
            "description": self.description,
            "location": self.location,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "attendees": self.attendees,
            "status": self.status.value if hasattr(self.status, 'value') else str(self.status),
            "is_recurring": self.is_recurring,
            "recurrence_rule": self.recurrence_rule,
            "recurring_event_id": self.recurring_event_id,
            "instance_date": self.instance_date.isoformat() if self.instance_date else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class Session(Base):
    """Session storage (alternative to Redis)"""
    
    __tablename__ = "sessions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_data = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="sessions")
    
    def __repr__(self):
        return f"<Session(id={self.id}, user_id={self.user_id}, expires_at={self.expires_at})>"
    
    @property
    def is_expired(self) -> bool:
        """Check if session is expired"""
        return datetime.utcnow() > self.expires_at
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "session_data": self.session_data,
            "expires_at": self.expires_at.isoformat(),
            "is_expired": self.is_expired,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class AuditLog(Base):
    """Audit log for tracking actions"""
    
    __tablename__ = "audit_log"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    details = Column(JSON, nullable=True)
    status = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    
    # Relationships
    user = relationship("User", back_populates="audit_logs")
    
    def __repr__(self):
        return f"<AuditLog(id={self.id}, action={self.action}, status={self.status})>"
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "action": self.action,
            "details": self.details,
            "status": self.status,
            "created_at": self.created_at.isoformat()
        }


class Reminder(Base):
    """Reminders for calendar events"""
    
    __tablename__ = "reminders"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events_cache.id", ondelete="CASCADE"), nullable=False, index=True)
    reminder_type = Column(String(50), nullable=False, index=True)  # '15min', '1hour', '1day', 'morning_briefing', 'evening_summary'
    scheduled_time = Column(DateTime, nullable=False, index=True)
    sent = Column(Boolean, default=False, nullable=False, index=True)
    redis_job_id = Column(String(255), nullable=True, index=True)  # Sorted set job ID for cancellation
    created_at = Column(DateTime, default=func.now(), nullable=False)
    sent_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="reminders")
    event = relationship("EventCache", back_populates="reminders")
    
    __table_args__ = (
        Index('idx_reminder_scheduled_sent', 'scheduled_time', 'sent'),
        Index('idx_reminder_user_event', 'user_id', 'event_id'),
    )
    
    def __repr__(self):
        return f"<Reminder(id={self.id}, type={self.reminder_type}, scheduled_time={self.scheduled_time}, sent={self.sent})>"
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "event_id": str(self.event_id),
            "reminder_type": self.reminder_type,
            "scheduled_time": self.scheduled_time.isoformat(),
            "sent": self.sent,
            "redis_job_id": self.redis_job_id,
            "created_at": self.created_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None
        }


class PendingDecision(Base):
    """Pending user decisions for event conflicts"""
    
    __tablename__ = "pending_decisions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events_cache.id", ondelete="CASCADE"), nullable=False, index=True)
    conflict_event_id = Column(UUID(as_uuid=True), ForeignKey("events_cache.id", ondelete="CASCADE"), nullable=False, index=True)
    llm_suggestion = Column(Text, nullable=True)
    state = Column(DecisionStatePG, default='waiting_for_user', nullable=False, index=True)
    user_message = Column(Text, nullable=True)  # Store the conflicting event creation request
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="pending_decisions")
    event = relationship("EventCache", foreign_keys=[event_id])
    conflict_event = relationship("EventCache", foreign_keys=[conflict_event_id])
    
    __table_args__ = (
        Index('idx_pending_user_state', 'user_id', 'state'),
    )
    
    def __repr__(self):
        return f"<PendingDecision(id={self.id}, user_id={self.user_id}, state={self.state})>"
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "event_id": str(self.event_id),
            "conflict_event_id": str(self.conflict_event_id),
            "llm_suggestion": self.llm_suggestion,
            "state": self.state.value if hasattr(self.state, 'value') else str(self.state),
            "user_message": self.user_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None
        }

