-- Migration: Add timezone column to users table
-- Date: 2025-11-26

-- Add timezone column with default UTC
ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC' NOT NULL;

-- Update existing users to Asia/Kolkata (IST)
-- You can change this to your preferred timezone
UPDATE users SET timezone = 'Asia/Kolkata' WHERE timezone = 'UTC';

-- Add comment
COMMENT ON COLUMN users.timezone IS 'User timezone in IANA format (e.g., Asia/Kolkata, America/New_York)';
