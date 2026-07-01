# ADR 0006: Permission System for Multi-User Bot

**Date**: 2026-06-13  
**Status**: ACCEPTED  
**Context**: Bot was replying to all users. Need to gate access so only authorized users get responses.

## Problem

Initially, bot replied to anyone who texted it. This is:
1. **Expensive** — Every message costs tokens (LLM API calls)
2. **Uncontrolled** — Strangers can use your bot for free
3. **Confusing** — No onboarding for new users
4. **Unscalable** — Can't manage who has access

## Solution

**Two-tier permission system:**
- **Owner** (`is_owner=true`) — Must scan QR + OAuth. Can grant permissions to others.
- **Authorized User** (`has_permission=true`) — Gets replies only after owner approves.
- **Pending User** (`has_permission=false`) — Gets "Setup mode" message, must wait for approval.

### Implementation

**Database**: Added `has_permission` BOOLEAN column to `users` table. Default: FALSE. Auto-set to TRUE for `is_owner=true`.

**Webhook**: Permission check runs BEFORE Hermes dispatch:
```python
if not is_owner and not user.has_permission:
    send_text("🔒 This bot is currently in setup mode.")
    return
```

**Dashboard** (`/dashboard`): Owner sees all users, grants/revokes with one click.

**API** (`/permissions`): 
- `GET /permissions` — List users
- `POST /permissions/grant?phone=...` — Grant permission
- `POST /permissions/revoke?phone=...` — Revoke permission

## Rationale

1. **Cost Control** — Owner explicitly approves each user
2. **Privacy** — No unsolicited automated replies
3. **User Onboarding** — Clear flow: request → approval → access
4. **Scalability** — Owner can manage 1-1000 users easily
5. **Audit Trail** — All permission changes logged in audit_log

## Alternatives Considered

1. **No Permission System** — Anyone gets replies (too expensive, not scalable)
2. **Whitelist-Only** — Only hardcoded phone numbers (too rigid, no onboarding)
3. **Paid Tier** — Users pay to use bot (adds payment complexity)
4. **Auto-Approval** — Auto-grant based on domain/group (security risk)

## Consequences

✅ **Positive**:
- Clear bot access control
- Owner visibility into who's using it
- Easy to revoke access if needed
- Cheap baseline (no replies until approved)
- Complies with data protection (don't process unsolicited requests)

⚠️ **Negative**:
- New users must wait for owner approval
- Owner must manually manage permissions
- Adds complexity to webhook flow
- Requires `/dashboard` maintenance

## Migration

**For existing users**: All users who texted before this change have `has_permission=false` by default. Owner must visit `/dashboard` and grant permissions.

**For owner**: Auto-set `has_permission=true` when `is_owner=true` (done in migration).

## Related

- **ADR 0004**: DPDP Compliance (group privacy filter)
- **ADR 0005**: Webhook dispatcher (async task to Hermes)

## Future Enhancements

1. **Invite Links** — Owner sends link, user clicks to auto-approve
2. **Time-Based Permissions** — Revoke after N days of inactivity
3. **Rate Limiting** — Different limits per user (free vs premium)
4. **Audit Dashboard** — Owner sees usage stats per user
