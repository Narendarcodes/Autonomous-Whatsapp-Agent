> 📦 **Historical snapshot.** Written before the Aug 2026 v3 intake refactor (ADR-0007),
> Alembic adoption (#9) and the outbound seam. Some findings may already be resolved —
> see CONTEXT.md for current state.

# Critical Bugs Found - Async/Sync Mismatch

## Issue Summary
The `calendar_service.py` is using **synchronous SQLAlchemy** methods but being called with **async sessions** from the agent_worker.

## Errors:
1. `RuntimeWarning: coroutine 'AsyncSession.commit' was never awaited`
2. `'AsyncSession' object has no attribute 'query'`
3. Missing `timezone` column in User SELECT query

## Files Affected:
- `backend/app/services/calendar_service.py` (lines 122, 190, 271, 321, 314-321, 411-414)
- `backend/app/services/message_router.py` (lines 191, 231)
- `backend/app/services/oauth_service.py` (lines 229, 285)

## Quick Fix Required:

### 1. Fix calendar_service.py
Replace all:
- `db.query(Model)` → `await db.execute(select(Model))`
- `db.commit()` → `await db.commit()`
- `db.delete(obj)` → `await db.delete(obj)`
- `.first()` → `result.scalar_one_or_none()`

### 2. Fix Missing Timezone Column
The User model has `timezone` but the SELECT query doesn't include it.

**Solution:** Restart the database container to reload the schema:
```bash
cd docker
docker-compose restart postgres
docker-compose restart backend agent_worker
```

### 3. Update Migration
Run the timezone migration:
```bash
cd docker
docker-compose exec -T postgres psql -U calendaruser -d calendar_agent -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'Asia/Kolkata' NOT NULL;"
```

## Status:
- ❌ calendar_service.py needs async refactor
- ❌ message_router.py needs async refactor  
- ✅ oauth_service.py mostly fixed (some sync calls remain)
- ⚠️ timezone column added to model but not in database

## Recommendation:
Since this is a major refactor (100+ line changes), I recommend:
1. Run the timezone migration first
2. Restart all services
3. Test basic functionality
4. Then schedule a proper async refactor session

The agent IS working (you saw it respond), but caching and some features are broken due to these async issues.
