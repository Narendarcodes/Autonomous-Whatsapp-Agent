"""Audio service — handles Speech-to-Text (STT) and Text-to-Speech (TTS)."""
import base64
import os
import tempfile
import httpx
import edge_tts

from app.core.config import settings
from app.core.logging import get_logger
from app.services.preferences_service import preferences_service

logger = get_logger(__name__)


class AudioService:
    async def transcribe_audio(self, base64_audio: str) -> str:
        """Transcribe base64-encoded audio using selected STT Provider (Groq or Local)."""
        # Clean base64 header if present (e.g. data:audio/ogg;base64,...)
        if "," in base64_audio:
            base64_audio = base64_audio.split(",", 1)[1]
            
        try:
            audio_bytes = base64.b64decode(base64_audio)
        except Exception as exc:
            logger.error("Failed to decode base64 audio: %s", exc)
            return ""

        stt_provider = await preferences_service.get_owner_preference("stt_provider", getattr(settings, "STT_PROVIDER", "groq"))
        
        # 1. LOCAL PROVIDER (Faster-Whisper Container)
        if stt_provider == "local":
            url = f"{settings.LOCAL_STT_URL.rstrip('/')}/audio/transcriptions"
            headers = {"Authorization": "Bearer local"}
            files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
            data = {"model": "whisper-1", "response_format": "json"}
            
            logger.info("Transcribing audio locally via Faster-Whisper container: %s", url)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=headers, files=files, data=data)
                    if resp.status_code == 200:
                        return resp.json().get("text", "").strip()
                    else:
                        logger.error("Local Whisper API returned %d: %s", resp.status_code, resp.text)
            except Exception as exc:
                logger.error("Error calling Local Whisper API: %s", exc)
            return "[Voice message: Local STT failed or timed out]"

        # 2. CLOUD PROVIDER (Groq Whisper API)
        if not settings.GROQ_API_KEY:
            logger.warning("GROQ_API_KEY is not set. Cannot transcribe audio.")
            return "[Voice message: STT not configured because GROQ_API_KEY is missing]"

        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}
        files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
        data = {"model": "whisper-large-v3", "response_format": "json"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, files=files, data=data)
                if resp.status_code == 200:
                    result = resp.json()
                    transcription = result.get("text", "").strip()
                    logger.info("Successfully transcribed voice message via Groq Whisper")
                    return transcription
                else:
                    logger.error("Groq Whisper API returned %d: %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Error calling Groq Whisper API: %s", exc)
            
        return "[Voice message: Transcription failed due to API error]"

    async def text_to_speech(self, text: str, voice: str | None = None) -> str | None:
        """Convert text to speech (ogg base64) using selected TTS Provider (Edge or Local Kokoro)."""
        if not text:
            return None

        # Clean text from markdown formatting
        clean_text = text.replace("*", "").replace("_", "").replace("`", "").strip()
        tts_provider = await preferences_service.get_owner_preference("tts_provider", getattr(settings, "TTS_PROVIDER", "edge"))
        selected_voice = await preferences_service.get_owner_preference("tts_voice", None)

        voice_profile = voice or selected_voice
        if voice_profile in ("Male", "Female", "Warm", "Professional"):
            if tts_provider == "local":
                voice_mapping = {
                    "Male": "am_adam",
                    "Female": "af_bella",
                    "Warm": "af_nicole",
                    "Professional": "am_michael"
                }
            else:
                voice_mapping = {
                    "Male": "en-US-AndrewNeural",
                    "Female": "en-US-AvaNeural",
                    "Warm": "en-US-EmmaNeural",
                    "Professional": "en-US-BrianNeural"
                }
            voice_profile = voice_mapping.get(voice_profile)

        # 1. LOCAL PROVIDER (Kokoro-82M Container)
        if tts_provider == "local":
            url = f"{settings.LOCAL_TTS_URL.rstrip('/')}/audio/speech"
            headers = {
                "Authorization": "Bearer local",
                "Content-Type": "application/json"
            }
            local_voice = voice_profile or getattr(settings, "TTS_VOICE_LOCAL", "af_bella")
            payload = {
                "input": clean_text,
                "voice": local_voice,
                "model": "tts-1"
            }
            
            logger.info("Generating TTS speech locally via Kokoro-82M container: %s", url)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        audio_bytes = resp.content
                        base64_audio = base64.b64encode(audio_bytes).decode("utf-8")
                        return base64_audio
                    else:
                        logger.error("Local Kokoro TTS API returned %d: %s", resp.status_code, resp.text)
            except Exception as exc:
                logger.error("Error calling Local Kokoro TTS API: %s", exc)
            return None

        # 2. CLOUD PROVIDER (Edge-TTS API)
        try:
            cloud_voice = voice_profile or getattr(settings, "TTS_VOICE_CLOUD", "en-US-AvaNeural")
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                communicate = edge_tts.Communicate(clean_text, cloud_voice)
                await communicate.save(tmp_path)
                
                with open(tmp_path, "rb") as f:
                    audio_bytes = f.read()
                    
                base64_audio = base64.b64encode(audio_bytes).decode("utf-8")
                logger.info("Successfully generated TTS speech via Edge-TTS (%d bytes)", len(audio_bytes))
                return base64_audio
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                    
        except Exception as exc:
            logger.error("Edge-TTS conversion failed: %s", exc)
            
        return None


audio_service = AudioService()
