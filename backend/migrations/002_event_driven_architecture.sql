-- Migration: Add Event-Driven Architecture Tables
-- Version: 2.0
-- Date: 2025-11-26

-- Add status ENUM to events_cache table
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'eventstatus') THEN
        CREATE TYPE eventstatus AS ENUM ('tentative', 'confirmed', 'cancelled');
    END IF;
END$$;

-- Alter events_cache to use ENUM for status
ALTER TABLE events_cache 
    ALTER COLUMN status TYPE eventstatus 
    USING status::eventstatus;

-- Set default to 'confirmed'
ALTER TABLE events_cache 
    ALTER COLUMN status SET DEFAULT 'confirmed'::eventstatus;

-- Add index on status
CREATE INDEX IF NOT EXISTS idx_event_status ON events_cache(status);

-- Add composite indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_event_user_time ON events_cache(user_id, start_time);
CREATE INDEX IF NOT EXISTS idx_event_user_status ON events_cache(user_id, status);
CREATE INDEX IF NOT EXISTS idx_event_end_time ON events_cache(end_time);

-- Create reminders table
CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES events_cache(id) ON DELETE CASCADE,
    reminder_type VARCHAR(50) NOT NULL,
    scheduled_time TIMESTAMP NOT NULL,
    sent BOOLEAN NOT NULL DEFAULT FALSE,
    redis_job_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    sent_at TIMESTAMP
);

-- Indexes for reminders
CREATE INDEX IF NOT EXISTS idx_reminder_user ON reminders(user_id);
CREATE INDEX IF NOT EXISTS idx_reminder_event ON reminders(event_id);
CREATE INDEX IF NOT EXISTS idx_reminder_type ON reminders(reminder_type);
CREATE INDEX IF NOT EXISTS idx_reminder_scheduled ON reminders(scheduled_time);
CREATE INDEX IF NOT EXISTS idx_reminder_sent ON reminders(sent);
CREATE INDEX IF NOT EXISTS idx_reminder_scheduled_sent ON reminders(scheduled_time, sent);
CREATE INDEX IF NOT EXISTS idx_reminder_user_event ON reminders(user_id, event_id);
CREATE INDEX IF NOT EXISTS idx_reminder_redis_job ON reminders(redis_job_id);

-- Create decision state ENUM
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'decisionstate') THEN
        CREATE TYPE decisionstate AS ENUM ('waiting_for_user', 'resolved', 'cancelled');
    END IF;
END$$;

-- Create pending_decisions table
CREATE TABLE IF NOT EXISTS pending_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES events_cache(id) ON DELETE CASCADE,
    conflict_event_id UUID NOT NULL REFERENCES events_cache(id) ON DELETE CASCADE,
    llm_suggestion TEXT,
    user_message TEXT,
    state decisionstate NOT NULL DEFAULT 'waiting_for_user'::decisionstate,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW() NOT NULL,
    resolved_at TIMESTAMP
);

-- Indexes for pending_decisions
CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_decisions(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_event ON pending_decisions(event_id);
CREATE INDEX IF NOT EXISTS idx_pending_conflict ON pending_decisions(conflict_event_id);
CREATE INDEX IF NOT EXISTS idx_pending_state ON pending_decisions(state);
CREATE INDEX IF NOT EXISTS idx_pending_user_state ON pending_decisions(user_id, state);
CREATE INDEX IF NOT EXISTS idx_pending_created ON pending_decisions(created_at);

-- Add trigger for pending_decisions updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_pending_decisions_updated_at ON pending_decisions;
CREATE TRIGGER update_pending_decisions_updated_at
    BEFORE UPDATE ON pending_decisions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Comments for documentation
COMMENT ON TABLE reminders IS 'Scheduled reminders for calendar events (15min, 1hour, 1day, morning/evening)';
COMMENT ON TABLE pending_decisions IS 'Pending user decisions for event conflict resolution';
COMMENT ON COLUMN reminders.redis_job_id IS 'Job ID in Redis Sorted Set for cancellation';
COMMENT ON COLUMN pending_decisions.llm_suggestion IS 'LLM''s recommended action for conflict resolution';
COMMENT ON COLUMN pending_decisions.user_message IS 'Original message that triggered the conflict';

-- Grant permissions (adjust as needed for your setup)
-- GRANT ALL PRIVILEGES ON TABLE reminders TO calendaruser;
-- GRANT ALL PRIVILEGES ON TABLE pending_decisions TO calendaruser;

-- Verify migration
SELECT 
    'Migration completed successfully. Tables created: reminders, pending_decisions' AS status,
    (SELECT COUNT(*) FROM reminders) AS reminder_count,
    (SELECT COUNT(*) FROM pending_decisions) AS pending_decision_count;
