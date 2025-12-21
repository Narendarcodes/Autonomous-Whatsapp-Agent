-- Migration: 003_recurring_events.sql
-- Add recurring event support to events_cache

ALTER TABLE events_cache ADD COLUMN is_recurring BOOLEAN DEFAULT FALSE;
ALTER TABLE events_cache ADD COLUMN recurrence_rule TEXT;
ALTER TABLE events_cache ADD COLUMN recurring_event_id VARCHAR(255);
ALTER TABLE events_cache ADD COLUMN instance_date DATE;

CREATE INDEX idx_events_recurring ON events_cache(recurring_event_id) WHERE recurring_event_id IS NOT NULL;
CREATE INDEX idx_events_instance_date ON events_cache(instance_date) WHERE instance_date IS NOT NULL;
CREATE INDEX idx_events_is_recurring ON events_cache(is_recurring) WHERE is_recurring = TRUE;

ALTER TABLE events_cache ADD CONSTRAINT check_recurring_consistency 
  CHECK (
    (is_recurring = TRUE AND recurring_event_id IS NULL AND instance_date IS NULL) OR
    (recurring_event_id IS NOT NULL AND instance_date IS NOT NULL) OR
    (is_recurring = FALSE AND recurring_event_id IS NULL AND instance_date IS NULL)
  );
