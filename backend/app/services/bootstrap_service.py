"""Idempotent, NON-destructive owner synchronisation (candidate 5 / #9).

Replaces the raw-SQL + row-deleting block that used to run inside the FastAPI
lifespan. One function, four rules:

1. An existing is_owner row is authoritative for runtime settings.
2. If settings.OWNER_WA_PHONE changed and another user already holds the new
   phone, the rename is REFUSED (legacy code DELETED that user) — the DB
   owner stays and a warning names the conflict.
3. With no owner row, the settings phone defines the owner: an existing holder
   is PROMOTED, otherwise the row is created.
4. Everyone else ends up is_owner=false.

Implementation notes:
- Demotion excludes by PRIMARY KEY, never by phone string: sessions here run
  with autoflush=False, so a just-renamed row would otherwise still match a
  phone-based WHERE clause and get demoted alongside everyone else.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import User
from app.services.phone_utils import normalize_phone_number

logger = logging.getLogger(__name__)


async def ensure_owner_record(db: AsyncSession) -> str:
    """Ensure exactly one owner record. Returns the authoritative owner phone."""
    result = await db.execute(
        select(User)
        .where(User.is_owner == True)  # noqa: E712 - tolerate corrupt multi-owner state
        .order_by(User.created_at)
        .limit(1)
    )
    current_owner = result.scalar_one_or_none()
    desired = normalize_phone_number(settings.OWNER_WA_PHONE) or settings.OWNER_WA_PHONE.lstrip("+")

    if current_owner is not None:
        if desired and current_owner.wa_phone != desired:
            dup_result = await db.execute(select(User).where(User.wa_phone == desired))
            holder = dup_result.scalar_one_or_none()
            if holder is not None and holder.id != current_owner.id:
                logger.warning(
                    "Bootstrap: refusing owner rename %s -> %s; target phone belongs to "
                    "another user (legacy code DELETED that row). Keeping DB owner.",
                    current_owner.wa_phone, desired,
                )
                settings.OWNER_WA_PHONE = current_owner.wa_phone
            else:
                current_owner.wa_phone = desired
                current_owner.has_permission = True
                settings.OWNER_WA_PHONE = desired
                logger.info("Bootstrap: renamed owner phone to %s", desired)
        else:
            settings.OWNER_WA_PHONE = current_owner.wa_phone

        target_id = current_owner.id
    else:
        dup_result = await db.execute(select(User).where(User.wa_phone == desired))
        holder = dup_result.scalar_one_or_none()
        if holder is not None:
            holder.is_owner = True
            holder.has_permission = True
            target_id = holder.id
            logger.info("Bootstrap: promoted existing user %s to owner", desired)
        else:
            new_owner = User(wa_phone=desired, is_owner=True, has_permission=True,
                             display_name="You (Owner)")
            db.add(new_owner)
            await db.flush()          # assign PK for the demotion exclusion
            target_id = new_owner.id
            logger.info("Bootstrap: created owner user %s", desired)
        settings.OWNER_WA_PHONE = desired

    await db.execute(
        update(User).where(User.id != target_id).values(is_owner=False)
    )
    await db.commit()
    return settings.OWNER_WA_PHONE
