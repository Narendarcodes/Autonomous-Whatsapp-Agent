-- ==========================================
-- Migration 005: Security ACL + Preferences
-- ==========================================

-- ==================== CHAT ACL ====================
-- Controls which chats the agent actively processes.
-- Modes:
--   allow_all    = agent processes messages + acts
--   silent_log   = agent records to audit but never acts (default)
--   block        = agent drops message immediately, no log
CREATE TABLE IF NOT EXISTS chat_acl (
    chat_id         VARCHAR(128) PRIMARY KEY,
    is_group        BOOLEAN      NOT NULL DEFAULT FALSE,
    mode            VARCHAR(16)  NOT NULL DEFAULT 'silent_log'
                        CHECK (mode IN ('allow_all', 'silent_log', 'block')),
    memory_enabled  BOOLEAN      NOT NULL DEFAULT FALSE,
    voice_enabled   BOOLEAN      NOT NULL DEFAULT FALSE,
    display_name    VARCHAR(128),
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed: the owner's own DM is the only initial allow_all chat.
-- The value will be set by the onboarding flow using OWNER_WA_PHONE.
-- This INSERT is a no-op if already present (idempotent migration).
-- Actual seeding happens in the onboarding script, not here.

CREATE INDEX IF NOT EXISTS idx_chat_acl_mode
    ON chat_acl(mode);

-- ==================== SENDER ACL ====================
-- Per-sender trust level (orthogonal to chat ACL).
CREATE TABLE IF NOT EXISTS sender_acl (
    sender_phone    VARCHAR(32)  PRIMARY KEY,
    trust_level     VARCHAR(16)  NOT NULL DEFAULT 'unknown'
                        CHECK (trust_level IN ('vip', 'normal', 'unknown', 'blocked')),
    display_name    VARCHAR(128),
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sender_acl_trust
    ON sender_acl(trust_level);

-- ==================== USER PREFERENCES ====================
-- Structured per-user preferences read by non-LLM code
-- (scheduler, calendar service, etc.) without needing an LLM call.
-- key examples:
--   meeting_default_duration_minutes = 60
--   morning_briefing_time            = 08:00
--   working_hours_start              = 09:00
--   working_hours_end                = 18:00
--   preferred_timezone               = Asia/Kolkata
--   tone                             = casual
--   language                         = hi (BCP-47)
--   signature                        = "– Sent by my AI"
--   reminder_cadence_work_meeting    = 15min,1hour
--   reminder_cadence_personal        = 1hour
CREATE TABLE IF NOT EXISTS user_preferences (
    id          UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key         VARCHAR(64)  NOT NULL,
    value       TEXT         NOT NULL,
    source      VARCHAR(16)  NOT NULL DEFAULT 'explicit'
                    CHECK (source IN ('explicit', 'inferred', 'default')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, key)
);

CREATE INDEX IF NOT EXISTS idx_prefs_user_key
    ON user_preferences(user_id, key);

-- ==================== PREFERENCE PROPOSALS ====================
-- Tracks which (action_class, sender/chat) pairs the agent has
-- proposed promoting to a higher trust level, to avoid spamming
-- the owner with the same proposal repeatedly.
CREATE TABLE IF NOT EXISTS preference_proposals (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action_class    VARCHAR(64) NOT NULL,
    target_id       VARCHAR(128),           -- sender_phone or chat_id
    confirmation_count INTEGER NOT NULL DEFAULT 0,
    proposed_at     TIMESTAMPTZ,
    accepted        BOOLEAN,
    declined_until  TIMESTAMPTZ,            -- don't re-propose until this time
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, action_class, target_id)
);

CREATE INDEX IF NOT EXISTS idx_proposals_user
    ON preference_proposals(user_id, action_class);
