"""Preferences service — manages user configuration key-values in the database."""
from sqlalchemy import select
from app.db.database import AsyncSessionLocal
from app.models.models import User, UserPreference


class PreferencesService:
    async def get_owner_preference(self, key: str, default: str | None = None) -> str | None:
        """Get a preference value for the owner user directly."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserPreference.value)
                .join(User)
                .where(User.is_owner == True, UserPreference.key == key)
            )
            val = result.scalar_one_or_none()
            return val if val is not None else default

    async def get(self, user_id: str, key: str, default: str | None = None) -> str | None:
        """Get a preference value by user ID and key."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserPreference.value)
                .where(UserPreference.user_id == user_id, UserPreference.key == key)
            )
            val = result.scalar_one_or_none()
            return val if val is not None else default

    async def set(self, user_id: str, key: str, value: str, source: str = "explicit") -> None:
        """Set or update a preference value by user ID and key."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserPreference)
                .where(UserPreference.user_id == user_id, UserPreference.key == key)
            )
            pref = result.scalar_one_or_none()
            if pref:
                pref.value = str(value)
                pref.source = source
            else:
                pref = UserPreference(
                    user_id=user_id,
                    key=key,
                    value=str(value),
                    source=source
                )
                db.add(pref)
            await db.commit()

    async def get_all(self, user_id: str) -> dict[str, str]:
        """Get all preferences for a specific user ID."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserPreference.key, UserPreference.value)
                .where(UserPreference.user_id == user_id)
            )
            return {row[0]: row[1] for row in result.all()}


preferences_service = PreferencesService()
