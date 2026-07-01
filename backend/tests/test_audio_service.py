import pytest
import httpx
import base64
from unittest.mock import AsyncMock, MagicMock
from app.services.audio_service import audio_service
from app.services.preferences_service import preferences_service
from app.core.config import settings

@pytest.mark.asyncio
async def test_transcribe_audio_local(mocker):
    """Verify transcription using the local Whisper container service."""
    # Mock settings STT provider to local (async)
    async def mock_get_owner_preference(key, default=None):
        return "local" if key == "stt_provider" else default

    mocker.patch.object(
        preferences_service, "get_owner_preference", 
        side_effect=mock_get_owner_preference
    )
    
    # Mock httpx client post call
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mocker.patch("app.services.audio_service.httpx.AsyncClient", return_value=mock_client)
    client_instance = mock_client.__aenter__.return_value
    
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "Local transcription response"}
    client_instance.post.return_value = mock_resp

    audio_bytes = b"sample_audio_stream"
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    
    result = await audio_service.transcribe_audio(audio_b64)
    
    assert result == "Local transcription response"
    client_instance.post.assert_called_once()
    assert client_instance.post.call_args[1]["files"]["file"][1] == audio_bytes


@pytest.mark.asyncio
async def test_transcribe_audio_groq(mocker):
    """Verify transcription using the cloud Groq Whisper API (including data headers cleanup)."""
    # Mock settings STT provider to groq (async)
    async def mock_get_owner_preference(key, default=None):
        return "groq" if key == "stt_provider" else default

    mocker.patch.object(
        preferences_service, "get_owner_preference", 
        side_effect=mock_get_owner_preference
    )
    
    old_key = settings.GROQ_API_KEY
    settings.GROQ_API_KEY = "MOCK_GROQ_API_KEY"

    try:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mocker.patch("app.services.audio_service.httpx.AsyncClient", return_value=mock_client)
        client_instance = mock_client.__aenter__.return_value
        
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"text": "Groq transcription response"}
        client_instance.post.return_value = mock_resp

        # Include header prefix in base64 string
        audio_bytes = b"sample_audio_stream_groq"
        audio_b64 = "data:audio/ogg;base64," + base64.b64encode(audio_bytes).decode("utf-8")
        
        result = await audio_service.transcribe_audio(audio_b64)
        
        assert result == "Groq transcription response"
        client_instance.post.assert_called_once()
        headers = client_instance.post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer MOCK_GROQ_API_KEY"
    finally:
        settings.GROQ_API_KEY = old_key


@pytest.mark.asyncio
async def test_text_to_speech_local(mocker):
    """Verify local Kokoro TTS maps generic voices and strips markdown characters."""
    async def mock_pref(key, default=None):
        if key == "tts_provider":
            return "local"
        if key == "tts_voice":
            return "Male"
        return default
        
    mocker.patch.object(preferences_service, "get_owner_preference", side_effect=mock_pref)
    
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mocker.patch("app.services.audio_service.httpx.AsyncClient", return_value=mock_client)
    client_instance = mock_client.__aenter__.return_value
    
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.content = b"local_kokoro_audio"
    client_instance.post.return_value = mock_resp

    # Input has asterisks formatting
    res = await audio_service.text_to_speech("Meeting with *John*")
    
    assert res == base64.b64encode(b"local_kokoro_audio").decode("utf-8")
    
    called_json = client_instance.post.call_args[1]["json"]
    # Check text is cleaned of markdown
    assert called_json["input"] == "Meeting with John"
    # Check voice mapped from generic 'Male' to kokoro profile 'am_adam'
    assert called_json["voice"] == "am_adam"


@pytest.mark.asyncio
async def test_text_to_speech_edge(mocker):
    """Verify Edge TTS maps voices, strips code markdown formatting, and handles temporary files."""
    async def mock_pref(key, default=None):
        if key == "tts_provider":
            return "edge"
        if key == "tts_voice":
            return "Warm"
        return default
        
    mocker.patch.object(preferences_service, "get_owner_preference", side_effect=mock_pref)

    # Mock Edge TTS Communicate class
    mock_comm_class = mocker.patch("app.services.audio_service.edge_tts.Communicate")
    mock_comm_instance = MagicMock()
    mock_comm_instance.save = AsyncMock()
    mock_comm_class.return_value = mock_comm_instance

    # Mock filesystem operations
    mocker.patch("app.services.audio_service.open", mocker.mock_open(read_data=b"edge_tts_audio_bytes"))
    mocker.patch("app.services.audio_service.os.path.exists", return_value=True)
    mocker.patch("app.services.audio_service.os.remove")

    # Input contains backtick and underscore markdown
    res = await audio_service.text_to_speech("Event: `Daily Sync` at _9 AM_")
    
    assert res == base64.b64encode(b"edge_tts_audio_bytes").decode("utf-8")
    
    # Assert Communicate called with cleaned text and correct mapped voice
    mock_comm_class.assert_called_once_with("Event: Daily Sync at 9 AM", "en-US-EmmaNeural")
