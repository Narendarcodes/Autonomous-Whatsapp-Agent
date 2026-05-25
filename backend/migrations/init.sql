-- ==========================================
-- WhatsApp AI Agent - Initial Schema
-- ==========================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==================== USERS ====================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wa_phone VARCHAR(32) UNIQUE NOT NULL,
    display_name VARCHAR(128),
    timezone VARCHAR(64) DEFAULT 'Asia/Kolkata',
    is_owner BOOLEAN DEFAULT FALSE,
    google_access_token_enc TEXT,
    google_refresh_token_enc TEXT,
    google_token_expiry TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_wa_phone ON users(wa_phone);

-- ==================== EVENTS CACHE ====================
CREATE TABLE IF NOT EXISTS events_cache (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    google_event_id VARCHAR(256) NOT NULL,
    summary VARCHAR(512) NOT NULL,
    description TEXT,
    location VARCHAR(512),
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    attendees JSONB DEFAULT '[]'::jsonb,
    meet_link TEXT,
    source_chat VARCHAR(128),
    source_message_id VARCHAR(256),
    status VARCHAR(32) DEFAULT 'confirmed',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, google_event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_start ON events_cache(user_id, start_time);
CREATE INDEX IF NOT EXISTS idx_events_google_id ON events_cache(google_event_id);

-- ==================== REMINDERS ====================
CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id UUID REFERENCES events_cache(id) ON DELETE CASCADE,
    reminder_type VARCHAR(32) NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    status VARCHAR(16) DEFAULT 'pending',
    payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reminders_scheduled ON reminders(scheduled_at) WHERE status = 'pending';

-- ==================== PENDING DECISIONS (Permission flow) ====================
CREATE TABLE IF NOT EXISTS pending_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    short_code VARCHAR(8) UNIQUE NOT NULL,
    action_type VARCHAR(32) NOT NULL,
    proposed_action JSONB NOT NULL,
    source_chat VARCHAR(128),
    status VARCHAR(16) DEFAULT 'awaiting',
    expires_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_short_code ON pending_decisions(short_code);
CREATE INDEX IF NOT EXISTS idx_pending_expires ON pending_decisions(expires_at) WHERE status = 'awaiting';

-- ==================== AUDIT LOG ====================
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

-- ==================== SESSIONS ====================
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chat_id VARCHAR(128) NOT NULL,
    is_group BOOLEAN DEFAULT FALSE,
    last_message_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_chat ON sessions(chat_id);
