# WhatsApp Calendar Agent - Database Schema

## Overview
The system uses a hybrid database architecture:
- **PostgreSQL**: Persistent relational data (users, events, decisions)
- **Redis**: Caching, sessions, message queues, and job scheduling

---

## PostgreSQL Schema

### 1. **users** table
Stores user information and Google OAuth credentials.

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wa_phone VARCHAR(20) UNIQUE NOT NULL,  -- WhatsApp phone (e.g., "916300354385")
    google_refresh_token TEXT,              -- Encrypted OAuth refresh token
    google_access_token TEXT,               -- Short-lived OAuth access token
    last_auth_time TIMESTAMP,               -- Last OAuth authentication
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_users_wa_phone ON users(wa_phone);
CREATE INDEX idx_users_is_active ON users(is_active);
```

**Key Fields:**
- `wa_phone`: Primary identifier for WhatsApp users (must match incoming messages)
- `google_refresh_token`: Used to obtain new access tokens
- `is_active`: Soft delete flag

---

### 2. **events_cache** table
Caches Google Calendar events for faster retrieval and conflict detection.

```sql
CREATE TABLE events_cache (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    google_event_id VARCHAR(255) NOT NULL,  -- Google Calendar event ID
    summary TEXT,                            -- Event title
    description TEXT,
    location TEXT,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    attendees JSONB,                         -- Array of attendee emails
    status eventstatus DEFAULT 'confirmed',  -- ENUM: tentative, confirmed, cancelled
    
    -- Recurring event fields (added in migration 003)
    is_recurring BOOLEAN DEFAULT FALSE,
    recurrence_rule TEXT,                    -- RRULE string
    recurring_event_id VARCHAR(255),         -- Master event ID
    instance_date DATE,                      -- For recurring instances
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, google_event_id)
);

-- Indexes
CREATE INDEX idx_events_user_id ON events_cache(user_id);
CREATE INDEX idx_events_start_time ON events_cache(start_time);
CREATE INDEX idx_events_google_id ON events_cache(google_event_id);
CREATE INDEX idx_events_user_time ON events_cache(user_id, start_time);
CREATE INDEX idx_event_status ON events_cache(status);
CREATE INDEX idx_events_recurring ON events_cache(recurring_event_id);
CREATE INDEX idx_events_instance_date ON events_cache(instance_date);
```

**Key Features:**
- Stores both one-time and recurring events
- `recurring_event_id`: Links instances to master event
- `instance_date`: Specific date for recurring instances

---

### 3. **reminders** table
Scheduled proactive reminders for events.

```sql
CREATE TABLE reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES events_cache(id) ON DELETE CASCADE,
    reminder_type VARCHAR(50) NOT NULL,      -- e.g., "15min", "1hour", "1day", "morning"
    scheduled_time TIMESTAMP NOT NULL,       -- When to send reminder
    sent BOOLEAN NOT NULL DEFAULT FALSE,
    redis_job_id VARCHAR(255),               -- Job ID in Redis Sorted Set
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    sent_at TIMESTAMP
);

-- Indexes
CREATE INDEX idx_reminder_user ON reminders(user_id);
CREATE INDEX idx_reminder_event ON reminders(event_id);
CREATE INDEX idx_reminder_scheduled_sent ON reminders(scheduled_time, sent);
CREATE INDEX idx_reminder_redis_job ON reminders(redis_job_id);
```

**Reminder Types:**
- `15min`: 15 minutes before event
- `1hour`: 1 hour before event
- `1day`: 1 day before event
- `morning`: Morning of event day (8 AM)
- `evening`: Evening before event (6 PM)

---

### 4. **pending_decisions** table
Stores user decisions for event conflict resolution.

```sql
CREATE TABLE pending_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES events_cache(id) ON DELETE CASCADE,
    conflict_event_id UUID NOT NULL REFERENCES events_cache(id) ON DELETE CASCADE,
    llm_suggestion TEXT,                     -- AI's recommended action
    user_message TEXT,                       -- Original message that caused conflict
    state decisionstate NOT NULL DEFAULT 'waiting_for_user',  -- ENUM
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW() NOT NULL,
    resolved_at TIMESTAMP
);

-- ENUM
CREATE TYPE decisionstate AS ENUM ('waiting_for_user', 'resolved', 'cancelled');

-- Indexes
CREATE INDEX idx_pending_user_state ON pending_decisions(user_id, state);
```

**Workflow:**
1. Conflict detected → Create `pending_decision` with `state='waiting_for_user'`
2. User responds → Update `state='resolved'` and `resolved_at`
3. Timeout/cancel → Update `state='cancelled'`

---

### 5. **sessions** table
Optional session storage (alternative to Redis).

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_data JSONB NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_expires_at ON sessions(expires_at);
```

---

### 6. **audit_log** table
Tracks all important actions for debugging.

```sql
CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,  -- e.g., 'create_event', 'oauth_auth'
    details JSONB,
    status VARCHAR(50),            -- success, failure, pending
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_user_id ON audit_log(user_id);
CREATE INDEX idx_audit_created_at ON audit_log(created_at);
CREATE INDEX idx_audit_action ON audit_log(action);
```

---

## Redis Data Structures

### 1. **Message Queue** (Redis Stream)
Queues incoming WhatsApp messages for processing by agent workers.

```
Stream: message_queue
Consumer Group: agent_workers
Consumer: agent_worker_{environment}_{timestamp}

Message Format:
{
  "user_id": "UUID",
  "wa_phone": "916300354385",
  "message_text": "Create meeting tomorrow at 3pm",
  "timestamp": "2025-11-26T12:00:00Z"
}
```

**Commands:**
- `XADD message_queue * user_id ... wa_phone ... message_text ...`
- `XREADGROUP GROUP agent_workers consumer_name COUNT 1 BLOCK 5000 STREAMS message_queue >`

---

### 2. **Session Storage** (Hash)
Stores user session data with TTL.

```
Key: session:{wa_phone}
Type: Hash
TTL: 3600 seconds (1 hour)

Fields:
{
  "last_message": "Create event",
  "context": {...},
  "state": "active"
}
```

**Commands:**
- `HSET session:916300354385 last_message "Create event"`
- `EXPIRE session:916300354385 3600`

---

### 3. **Conversation History** (List)
Stores recent conversation messages for context.

```
Key: conversation:{wa_phone}
Type: List (newest first)
TTL: 3600 seconds
Max Length: 10 messages

Format:
[
  {"role": "user", "content": "hi", "timestamp": "..."},
  {"role": "assistant", "content": "Hello!", "timestamp": "..."}
]
```

**Commands:**
- `LPUSH conversation:916300354385 '{"role":"user","content":"hi"}'`
- `LTRIM conversation:916300354385 0 9`
- `LRANGE conversation:916300354385 0 -1`

---

### 4. **Cache** (String with TTL)
Generic caching for API responses, OAuth tokens, etc.

```
Key: cache:{key}
Type: String (JSON serialized)
TTL: Variable (300-3600 seconds)

Examples:
- cache:oauth:916300354385
- cache:events:916300354385:today
```

**Commands:**
- `SETEX cache:oauth:916300354385 3600 "access_token_value"`
- `GET cache:oauth:916300354385`

---

### 5. **Rate Limiting** (String with TTL)
Prevents abuse by limiting requests per user.

```
Key: rate_limit:{wa_phone}
Type: String (counter)
TTL: 60 seconds (window)
Max: 10 requests per window

Commands:
- INCR rate_limit:916300354385
- EXPIRE rate_limit:916300354385 60
```

---

### 6. **Delayed Jobs** (Sorted Set)
Schedules proactive reminders and notifications.

```
Key: delayed_jobs
Type: Sorted Set
Score: Unix timestamp (when to execute)

Member Format:
{
  "job_id": "UUID",
  "type": "reminder",
  "user_id": "UUID",
  "event_id": "UUID",
  "reminder_type": "15min"
}
```

**Commands:**
- `ZADD delayed_jobs 1732665600 '{"job_id":"...","type":"reminder",...}'`
- `ZRANGEBYSCORE delayed_jobs -inf {current_time} LIMIT 0 100`
- `ZREM delayed_jobs '{"job_id":"..."}'`

---

### 7. **Message Status Tracking** (String with TTL)
Tracks WhatsApp message delivery status.

```
Key: msg_status:{message_id}
Type: String
TTL: 86400 seconds (24 hours)

Values: "sent", "delivered", "read", "failed"
```

**Commands:**
- `SETEX msg_status:wamid.ABC123 86400 "delivered"`

---

### 8. **Application Logs** (Pub/Sub)
Broadcasts logs from all services to Live Log Viewer.

```
Channel: app_logs
Type: Pub/Sub

Message Format:
{
  "timestamp": "2025-11-26T12:00:00Z",
  "level": "INFO",
  "message": "📨 Processing message from 916300354385"
}
```

**Commands:**
- `PUBLISH app_logs '{"timestamp":"...","level":"INFO","message":"..."}'`
- `SUBSCRIBE app_logs`

---

## Data Flow Example

### User sends "Create meeting tomorrow at 3pm"

1. **Webhook receives message** → Stores in Redis Stream `message_queue`
2. **Agent Worker** → Reads from stream, queries `users` table by `wa_phone`
3. **User found** → Loads conversation history from Redis `conversation:{wa_phone}`
4. **LLM processes** → Calls `create_event` tool
5. **Event created** → Inserts into `events_cache` table
6. **Reminders scheduled** → Inserts into `reminders` table + Redis `delayed_jobs`
7. **Response sent** → Stores in conversation history, ACKs message in stream

---

## Maintenance

### Cleanup Functions
```sql
-- Clean expired sessions, old logs, old cache
SELECT cleanup_expired_data();

-- Delete sessions older than expiry
DELETE FROM sessions WHERE expires_at < CURRENT_TIMESTAMP;

-- Delete old audit logs (keep 30 days)
DELETE FROM audit_log WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '30 days';

-- Delete old cached events (keep 7 days)
DELETE FROM events_cache WHERE updated_at < CURRENT_TIMESTAMP - INTERVAL '7 days';
```

### Redis Cleanup
```bash
# Trim message queue (keep last 10,000)
XTRIM message_queue MAXLEN ~ 10000

# Remove expired delayed jobs
ZREMRANGEBYSCORE delayed_jobs -inf {past_timestamp}
```

---

## Connection Details

### PostgreSQL
```
Host: postgres (Docker network) / localhost (host)
Port: 5432
Database: calendar_agent
User: calendaruser
Password: calendarpass (from .env)
```

### Redis
```
Host: redis (Docker network) / localhost (host)
Port: 6379
Password: redispass (from .env)
Database: 0
```
