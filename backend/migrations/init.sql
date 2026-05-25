-- WhatsApp AI Calendar Agent Database Schema
-- PostgreSQL 15+ initialization script

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==================== USERS TABLE ====================
-- Stores user information and Google OAuth tokens
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wa_phone VARCHAR(20) UNIQUE NOT NULL,  -- WhatsApp phone number (normalized)
    google_refresh_token TEXT,  -- Google OAuth refresh token (encrypted)
    google_access_token TEXT,  -- Google OAuth access token (short-lived)
    last_auth_time TIMESTAMP,  -- Last OAuth authentication time
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast phone lookup
CREATE INDEX IF NOT EXISTS idx_users_wa_phone ON users(wa_phone);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

-- ==================== EVENTS CACHE TABLE ====================
-- Cache recent calendar events for faster retrieval
CREATE TABLE IF NOT EXISTS events_cache (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    google_event_id VARCHAR(255) NOT NULL,  -- Google Calendar event ID
    summary TEXT,  -- Event title
    description TEXT,  -- Event description
    location TEXT,  -- Event location
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    attendees JSONB,  -- Array of attendee emails
    status VARCHAR(50) DEFAULT 'confirmed',  -- confirmed, tentative, cancelled
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, google_event_id)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_events_user_id ON events_cache(user_id);
CREATE INDEX IF NOT EXISTS idx_events_start_time ON events_cache(start_time);
CREATE INDEX IF NOT EXISTS idx_events_google_id ON events_cache(google_event_id);
CREATE INDEX IF NOT EXISTS idx_events_user_time ON events_cache(user_id, start_time);

-- ==================== SESSIONS TABLE ====================
-- Alternative to Redis for session storage (optional)
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_data JSONB NOT NULL,  -- Flexible JSON storage
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for user lookup and expiry cleanup
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

-- ==================== AUDIT LOG TABLE ====================
-- Track all important actions for debugging
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,  -- e.g., 'create_event', 'oauth_auth', 'message_received'
    details JSONB,  -- Additional context
    status VARCHAR(50),  -- success, failure, pending
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for querying logs by user and time
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- ==================== TRIGGERS ====================
-- Auto-update updated_at timestamp

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to all tables with updated_at
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_events_cache_updated_at BEFORE UPDATE ON events_cache
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_sessions_updated_at BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ==================== CLEANUP FUNCTION ====================
-- Function to clean up expired sessions and old cache

CREATE OR REPLACE FUNCTION cleanup_expired_data()
RETURNS void AS $$
BEGIN
    -- Delete expired sessions
    DELETE FROM sessions WHERE expires_at < CURRENT_TIMESTAMP;
    
    -- Delete old audit logs (keep last 30 days)
    DELETE FROM audit_log WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '30 days';
    
    -- Delete old cached events (keep last 7 days)
    DELETE FROM events_cache WHERE updated_at < CURRENT_TIMESTAMP - INTERVAL '7 days';
END;
$$ LANGUAGE plpgsql;

-- ==================== INITIAL DATA ====================
-- Insert system user (optional)

-- INSERT INTO users (wa_phone, is_active) VALUES ('system', FALSE);

-- ==================== GRANTS ====================
-- Grant permissions (adjust based on your user)

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO calendaruser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO calendaruser;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO calendaruser;

-- ==================== COMPLETION ====================
DO $$
BEGIN
    RAISE NOTICE '✅ Database schema initialized successfully!';
    RAISE NOTICE '📊 Tables created: users, events_cache, sessions, audit_log';
    RAISE NOTICE '🔧 Triggers and functions configured';
END $$;
