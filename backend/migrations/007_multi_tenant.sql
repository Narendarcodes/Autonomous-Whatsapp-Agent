-- 007_multi_tenant.sql — Multi-tenant schema: tenants, dashboard_users, customer_google_tokens
-- Adds tenant_id to users. Legacy rows keep NULL tenant_id (= default tenant).

-- ============ TENANTS ============
CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    slug VARCHAR(64) NOT NULL UNIQUE,
    whatsapp_session_ref VARCHAR(128),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ DASHBOARD USERS (argon2 password hashes) ============
CREATE TABLE IF NOT EXISTS dashboard_users (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(512) NOT NULL,
    is_owner BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tenant_email UNIQUE (tenant_id, email)
);
CREATE INDEX IF NOT EXISTS idx_dashboard_users_tenant ON dashboard_users(tenant_id);

-- ============ CUSTOMER GOOGLE TOKENS (encrypted, tenant-scoped) ============
CREATE TABLE IF NOT EXISTS customer_google_tokens (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_wa_phone VARCHAR(32) NOT NULL,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT,
    token_expiry TIMESTAMPTZ,
    scopes TEXT,
    email VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tenant_phone_token UNIQUE (tenant_id, user_wa_phone)
);
CREATE INDEX IF NOT EXISTS idx_cgt_tenant_phone ON customer_google_tokens(tenant_id, user_wa_phone);

-- ============ USERS: add tenant_id (NULL = legacy single-tenant) ============
ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
