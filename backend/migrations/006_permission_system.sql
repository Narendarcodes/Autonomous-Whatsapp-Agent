-- ==========================================
-- Migration 006: Permission System for Users
-- ==========================================
-- Adds permission control so bot only replies to authorized users
-- Owner (is_owner=true) always gets permission
-- Other users need explicit grant from owner

ALTER TABLE users 
ADD COLUMN IF NOT EXISTS has_permission BOOLEAN NOT NULL DEFAULT FALSE;

-- Owner should always have permission
UPDATE users SET has_permission = TRUE WHERE is_owner = TRUE;

CREATE INDEX IF NOT EXISTS idx_users_permission 
    ON users(has_permission);
