ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC' NOT NULL;
UPDATE users SET timezone = 'Asia/Kolkata' WHERE timezone = 'UTC';
