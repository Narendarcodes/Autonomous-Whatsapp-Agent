"""#9: owner synchronisation is idempotent and NON-destructive.

Pattern note: seed everything with one add_all+commit, then exercise
ensure_owner_record exactly like production does (fresh session state,
autoflush off).
"""
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.models import User
from app.services.bootstrap_service import ensure_owner_record


@pytest_asyncio.fixture(autouse=True)
async def _fresh_users_table(test_engine):
    """Schema is created per-test by test_engine; clear rows defensively."""
    async with AsyncSessionLocal() as db:
        await db.execute(text("TRUNCATE users CASCADE"))
        await db.commit()
    yield


async def users_snapshot(db):
    res = await db.execute(select(User.wa_phone, User.is_owner))
    return sorted(res.all())


async def owner_phone_of(db, wa_phone):
    return await db.scalar(select(User).where(User.wa_phone == wa_phone))


# ------------------------------------------------------------------ tests


@pytest.mark.asyncio
async def test_renames_owner_when_target_phone_free(db_session, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_WA_PHONE", "15559990001")
    db_session.add(User(wa_phone="15550001111", is_owner=True, has_permission=True))
    await db_session.commit()

    result = await ensure_owner_record(db_session)

    assert result == "15559990001"
    renamed = await owner_phone_of(db_session, "15559990001")
    assert renamed is not None and renamed.is_owner is True
    assert await owner_phone_of(db_session, "15550001111") is None


@pytest.mark.asyncio
async def test_rename_refused_when_target_held_by_another_user(db_session, monkeypatch):
    """THE regression: legacy startup DELETED the conflicting row here."""
    monkeypatch.setattr(settings, "OWNER_WA_PHONE", "15559990002")
    db_session.add_all([
        User(wa_phone="15550001111", is_owner=True, has_permission=True),
        User(wa_phone="15559990002", is_owner=False, has_permission=False,
             display_name="Innocent Bystander"),
    ])
    await db_session.commit()

    result = await ensure_owner_record(db_session)

    assert result == "15550001111"                       # keeps DB owner
    bystander = await owner_phone_of(db_session, "15559990002")
    assert bystander is not None                          # NOT deleted
    assert bystander.is_owner is False                    # demoted only
    original = await owner_phone_of(db_session, "15550001111")
    assert original is not None and original.is_owner is True


@pytest.mark.asyncio
async def test_promotes_existing_holder_when_no_owner_row(db_session, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_WA_PHONE", "15559990003")
    db_session.add(User(wa_phone="15559990003", is_owner=False))
    await db_session.commit()

    result = await ensure_owner_record(db_session)

    assert result == "15559990003"
    promoted = await owner_phone_of(db_session, "15559990003")
    assert promoted.is_owner is True and promoted.has_permission is True


@pytest.mark.asyncio
async def test_creates_owner_row_from_scratch(db_session, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_WA_PHONE", "15559990004")

    result = await ensure_owner_record(db_session)

    assert result == "15559990004"
    created = await owner_phone_of(db_session, "15559990004")
    assert created is not None and created.is_owner is True


@pytest.mark.asyncio
async def test_demotes_everyone_else(db_session, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_WA_PHONE", "15559990005")
    db_session.add_all([
        User(wa_phone="15559990005", is_owner=True, has_permission=True),
        User(wa_phone="15550006666", is_owner=False),
        User(wa_phone="15550007777", is_owner=False),
    ])
    await db_session.commit()
    # simulate a stale duplicate-owner flag on another row
    rogue = await owner_phone_of(db_session, "15550007777")
    rogue.is_owner = True
    await db_session.commit()

    result = await ensure_owner_record(db_session)

    assert result == "15559990005"
    owners = [p for p, o in await users_snapshot(db_session) if o]
    assert owners == ["15559990005"]
