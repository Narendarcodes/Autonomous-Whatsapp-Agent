"""Application configuration loaded from environment variables."""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "omniWA"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    BASE_URL: str = "http://localhost:8000"
    TIMEZONE: str = "Asia/Kolkata"

    # PostgreSQL
    POSTGRES_USER: str = "calendaruser"
    POSTGRES_PASSWORD: str = "calendarpass"
    POSTGRES_DB: str = "calendar_agent"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "redispass"
    REDIS_DB: int = 0

    # OpenWA
    OWNER_WA_PHONE: str = ""
    BOT_RELATIONSHIP_MODE: str = "self_chat"

    # Hermes Agent
    HERMES_BASE_URL: str = "http://hermes:8642"
    HERMES_API_KEY: str = "hermes_api_key_change_me"
    # When True, outbound WhatsApp replies go through Hermes' native Baileys bridge
    # (Evolution API container dropped). The dispatch session-id IS the chat target,
    # so Hermes sends the model's response itself; omniWA stops calling Evolution.
    HERMES_OWNS_WHATSAPP: bool = False
    # Shared hermes_data volume mount (bridge.log / creds.json live here)
    HERMES_DATA_DIR: str = "/opt/hermes_data"
    HERMES_HEALTH_URL: str = "http://hermes:8642"

    # LiteLLM proxy
    LITELLM_BASE_URL: str = "http://litellm:4000"
    LITELLM_MASTER_KEY: str = "litellm_master_key_change_me"

    # Additional LLM provider keys (all optional — LiteLLM uses whichever are set)
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    NVIDIA_NIM_API_KEY: str = ""

    # Security ACL
    PREFERENCE_PROMOTION_THRESHOLD: int = 5   # confirmations before proposing auto
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.3
    GITHUB_TOKEN: str = ""
    GOOGLE_AI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OLLAMA_HOST: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "mistral:7b-instruct"

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/oauth/callback"
    GOOGLE_SERVICE_ACCOUNT_FILE: str = "/app/google-service-account.json"
    TOKEN_ENCRYPTION_KEY: str = ""
    ADMIN_PASSWORD: str = "admin123"
    SESSION_SECRET_KEY: str = "super_secret_session_key_naru_change_me"

    # Audio settings (STT: groq | local, TTS: edge | local)
    STT_PROVIDER: str = "groq"
    TTS_PROVIDER: str = "edge"
    LOCAL_STT_URL: str = "http://whisper-api:8000/v1"
    LOCAL_TTS_URL: str = "http://kokoro-api:8000/v1"
    TTS_VOICE_CLOUD: str = "en-US-AvaNeural"
    TTS_VOICE_LOCAL: str = "af_bella"

    # Agent
    AGENT_MAX_ITERATIONS: int = 5
    AGENT_TIMEOUT_SECONDS: int = 30
    CONVERSATION_MAX_MESSAGES: int = 50
    CONVERSATION_TTL_SECONDS: int = 86400
    RATE_LIMIT_REQUESTS: int = 20
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Permission flow
    PERMISSION_REQUIRED_FOR_CREATE: bool = False
    PERMISSION_REQUIRED_FOR_DELETE: bool = True
    PERMISSION_REQUIRED_FOR_GROUP_REPLY: bool = True
    PERMISSION_TIMEOUT_MINUTES: int = 15

    # Proactive
    REMINDER_15MIN_ENABLED: bool = True
    REMINDER_1HOUR_ENABLED: bool = True
    REMINDER_1DAY_ENABLED: bool = True
    MORNING_BRIEFING_TIME: str = "08:00"
    EVENING_SUMMARY_TIME: str = "20:00"
    CONFLICT_CHECK_INTERVAL_MINUTES: int = 30
    WEEKLY_INSIGHTS_DAY: str = "monday"
    WEEKLY_INSIGHTS_TIME: str = "09:00"
    PROACTIVE_NUDGES_ENABLED: bool = True

    # HTTP
    HTTP_CONNECT_TIMEOUT: float = 10.0
    HTTP_READ_TIMEOUT: float = 30.0
    HTTP_WRITE_TIMEOUT: float = 30.0
    HTTP_POOL_TIMEOUT: float = 10.0

    # Retry
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_BASE_DELAY: float = 1.0
    RETRY_MAX_DELAY: float = 10.0

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
