import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from app.models.models import User, EventCache
from app.services.calendar_service import calendar_service

@pytest.fixture
def mock_calendar_api(mocker):
    """Fixture to mock credentials loading and Google Calendar API resource calls."""
    # Return dummy credentials to prevent GoogleAuth credentials check fails
    mocker.patch("app.services.calendar_service.load_user_credentials", return_value=MagicMock())
    
    # Mock build
    mock_build = mocker.patch("app.services.calendar_service.build")
    
    mock_service = MagicMock()
    mock_events_resource = MagicMock()
    
    mock_service.events.return_value = mock_events_resource
    mock_build.return_value = mock_service
    
    return mock_events_resource


@pytest.mark.asyncio
async def test_list_upcoming_events(db_session, mock_calendar_api):
    """Verify listing upcoming events from primary calendar."""
    user = User(wa_phone="12345")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Setup mocked list response
    mock_list_call = MagicMock()
    mock_list_call.execute.return_value = {"items": [{"id": "evt1", "summary": "Sync Call"}]}
    mock_calendar_api.list.return_value = mock_list_call

    events = await calendar_service.list_upcoming_events(db_session, user)
    assert len(events) == 1
    assert events[0]["summary"] == "Sync Call"
    mock_calendar_api.list.assert_called_once()


@pytest.mark.asyncio
async def test_create_event_and_cache(db_session, mock_calendar_api):
    """Verify event registration on Google API, extracting Meet links, and writing DB cache."""
    user = User(wa_phone="12345", timezone="Asia/Kolkata")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Setup insert mock
    mock_insert_call = MagicMock()
    mock_insert_call.execute.return_value = {
        "id": "gcal_abc123",
        "htmlLink": "http://gcal/abc123",
        "conferenceData": {
            "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-def-ghi"}]
        }
    }
    mock_calendar_api.insert.return_value = mock_insert_call

    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(hours=1)

    result = await calendar_service.create_event(
        db_session,
        user=user,
        summary="Test Calendar Meeting",
        start_time=start,
        end_time=end,
        create_meet_link=True
    )

    assert result is not None
    assert result["google_event_id"] == "gcal_abc123"
    assert result["meet_link"] == "https://meet.google.com/abc-def-ghi"

    # Verify event was cached into db
    from sqlalchemy import select
    db_res = await db_session.execute(select(EventCache).where(EventCache.google_event_id == "gcal_abc123"))
    cached = db_res.scalar_one_or_none()
    assert cached is not None
    assert cached.summary == "Test Calendar Meeting"
    assert cached.meet_link == "https://meet.google.com/abc-def-ghi"


@pytest.mark.asyncio
async def test_delete_event(db_session, mock_calendar_api):
    """Test removing event from Google Calendar API and purging it from local DB cache."""
    user = User(wa_phone="12345")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Seed an EventCache entry to delete
    cached = EventCache(
        user_id=user.id,
        google_event_id="gcal_del",
        summary="Delete Sync",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(hours=1),
        status="confirmed"
    )
    db_session.add(cached)
    await db_session.commit()

    # Mock delete call response
    mock_delete_call = MagicMock()
    mock_delete_call.execute.return_value = None
    mock_calendar_api.delete.return_value = mock_delete_call

    success = await calendar_service.delete_event(db_session, user, "gcal_del")
    assert success is True
    
    # Verify DB cache entry is deleted
    from sqlalchemy import select
    db_res = await db_session.execute(select(EventCache).where(EventCache.google_event_id == "gcal_del"))
    assert db_res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_find_conflicts(db_session):
    """Verify detection of overlapping events, including border boundary adjacent cases."""
    user = User(wa_phone="12345")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    base_time = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    
    # Seed an event from 10:00 to 11:00
    existing = EventCache(
        user_id=user.id,
        google_event_id="evt_exist",
        summary="Existing Sync",
        start_time=base_time,
        end_time=base_time + timedelta(hours=1),
        status="confirmed"
    )
    db_session.add(existing)
    await db_session.commit()

    # 1. Conflict check: 10:30 to 11:30 (overlaps existing 10:00-11:00)
    conflicts = await calendar_service.find_conflicts(
        db_session, user, base_time + timedelta(minutes=30), base_time + timedelta(hours=1, minutes=30)
    )
    assert len(conflicts) == 1
    assert conflicts[0].google_event_id == "evt_exist"

    # 2. Adjacent Border Check: 09:00 to 10:00 (ends exactly when existing starts, should NOT conflict)
    conflicts_before = await calendar_service.find_conflicts(
        db_session, user, base_time - timedelta(hours=1), base_time
    )
    assert len(conflicts_before) == 0

    # 3. Adjacent Border Check: 11:00 to 12:00 (starts exactly when existing ends, should NOT conflict)
    conflicts_after = await calendar_service.find_conflicts(
        db_session, user, base_time + timedelta(hours=1), base_time + timedelta(hours=2)
    )
    assert len(conflicts_after) == 0
