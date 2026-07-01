import pytest
from app.models.models import User
from app.services.preferences_service import preferences_service
from app.core.config import settings

@pytest.mark.asyncio
async def test_preferences_crud(db_session):
    """Test standard CRUD operations for user preferences, including defaults and updates."""
    # Seed a test user
    user = User(wa_phone="1234567890", is_owner=True, has_permission=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # 1. Test set preference
    await preferences_service.set(user.id, "theme", "dark", source="test")
    
    # 2. Test get preference
    val = await preferences_service.get(user.id, "theme")
    assert val == "dark"

    # 3. Test get non-existent returns default
    val_default = await preferences_service.get(user.id, "missing", "default_val")
    assert val_default == "default_val"

    # 4. Test get_all
    all_prefs = await preferences_service.get_all(user.id)
    assert all_prefs == {"theme": "dark"}

    # 5. Test update existing preference
    await preferences_service.set(user.id, "theme", "light", source="test2")
    val_updated = await preferences_service.get(user.id, "theme")
    assert val_updated == "light"


@pytest.mark.asyncio
async def test_owner_preference_lookup(db_session):
    """Test getting preferences by matching the owner phone number from config settings."""
    owner_phone = "919999999999"
    user = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Set owner phone in config settings to match our test user JID/phone
    old_owner_phone = settings.OWNER_WA_PHONE
    settings.OWNER_WA_PHONE = owner_phone
    try:
        # Set a preference for the owner
        await preferences_service.set(user.id, "bot_name", "Hermes-AI")

        # Get owner preference
        val_owner = await preferences_service.get_owner_preference("bot_name")
        assert val_owner == "Hermes-AI"
        
        # Test default fallback for owner
        val_owner_missing = await preferences_service.get_owner_preference("timezone", "UTC")
        assert val_owner_missing == "UTC"
    finally:
        settings.OWNER_WA_PHONE = old_owner_phone
