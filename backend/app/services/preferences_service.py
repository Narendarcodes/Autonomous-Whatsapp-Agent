"""Preferences service — read/write per-user structured preferences.

Preferences are key/value rows in user_preferences table.
Keys are documented in migrations/005_security_and_preferences.sql.
"""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.models.models import PreferenceProposal, User, UserPreference

logger = get_logger(__name__)

# Default fallback values for known keys
DEFAULTS: dict[str, str] = {
    "meeting_default_duration_minutes": "60",
    "morning_briefing_time": settings.MORNING_BRIEFING_TIME,
    "evening_summary_time": settings.EVENING_SUMMARY_TIME,
    "reminder_15min_enabled": "true",
    "reminder_1hour_enabled": "true",
    "reminder_1day_enabled": "true",
    "tone": "casual",
    "language": "en",
}


class PreferencesService:
    async def get(self, user_id: str, key: str, default: str | None = None) -> str | None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserPreference).where(
                    UserPreference.user_id == user_id,
                    UserPreference.key == key,
                )
            )
            pref = result.scalar_one_or_none()
            if pref:
                return pref.value
            return default or DEFAULTS.get(key)

    async def set(
        self,
        user_id: str,
        key: str,
        value: str,
        source: str = "explicit",
    ) -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserPreference).where(
                    UserPreference.user_id == user_id,
                    UserPreference.key == key,
                )
            )
            pref = result.scalar_one_or_none()
            if pref:
                pref.value = value
                pref.source = source
            else:
                pref = UserPreference(user_id=user_id, key=key, value=value, source=source)
                db.add(pref)
            await db.commit()

    async def get_all(self, user_id: str) -> dict[str, str]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            )
            rows = result.scalars().all()
            return {r.key: r.value for r in rows}

    async def record_confirmation(
        self,
        db: AsyncSession,
        user_id: str,
        action_class: str,
        target_id: str,
    ) -> int:
        """Increment confirmation count. Return new count."""
        result = await db.execute(
            select(PreferenceProposal).where(
                PreferenceProposal.user_id == user_id,
                PreferenceProposal.action_class == action_class,
                PreferenceProposal.target_id == target_id,
            )
        )
        proposal = result.scalar_one_or_none()
        if proposal is None:
            proposal = PreferenceProposal(
                user_id=user_id,
                action_class=action_class,
                target_id=target_id,
            )
            db.add(proposal)
        proposal.confirmation_count += 1
        await db.commit()
        return proposal.confirmation_count

    async def should_propose_promotion(
        self,
        db: AsyncSession,
        user_id: str,
        action_class: str,
        target_id: str,
    ) -> bool:
        """Return True if the agent should suggest auto-promoting this pair."""
        from datetime import datetime, timezone
        result = await db.execute(
            select(PreferenceProposal).where(
                PreferenceProposal.user_id == user_id,
                PreferenceProposal.action_class == action_class,
                PreferenceProposal.target_id == target_id,
            )
        )
        proposal = result.scalar_one_or_none()
        if proposal is None:
            return False
        # Already proposed and accepted or in cooling-off period
        if proposal.accepted:
            return False
        if proposal.declined_until and proposal.declined_until > datetime.now(timezone.utc):
            return False
        # Not yet proposed and count hit threshold
        if proposal.proposed_at is None:
            return proposal.confirmation_count >= settings.PREFERENCE_PROMOTION_THRESHOLD
        return False


preferences_service = PreferencesService()
