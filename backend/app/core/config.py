"""
Application Configuration
Manages all environment variables and settings
"""

from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # ==================== APP SETTINGS ====================
    APP_NAME: str = "WhatsApp AI Calendar Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"
    BASE_URL: str = "http://localhost:8000"  # Base URL for the API (for OAuth callbacks, etc.)
    
    # ==================== DATABASE SETTINGS ====================
    POSTGRES_USER: str = "calendaruser"
    POSTGRES_PASSWORD: str = "change_me_postgres_password"
    POSTGRES_DB: str = "calendar_agent"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    
    @property
    def DATABASE_URL(self) -> str:
        """Construct database URL"""
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        """Construct async database URL for asyncpg"""
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    # ==================== REDIS SETTINGS ====================
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "change_me_redis_password"
    REDIS_DB: int = 0
    
    @property
    def REDIS_URL(self) -> str:
        """Construct Redis URL"""
        if self.REDIS_PASSWORD:
            return f"redis://default:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    # ==================== LLM SETTINGS ====================
    # GitHub Models Settings (Production)
    USE_GITHUB_MODELS: bool = True
    GITHUB_TOKEN: Optional[str] = None  # GitHub Personal Access Token
    GITHUB_MODEL: str = "gpt-4o-mini"  # GitHub Models model name
    GITHUB_TIMEOUT: int = 30  # Timeout in seconds
    GITHUB_API_URL: str = "https://models.inference.ai.azure.com"
    
    # ==================== WHATSAPP SETTINGS ====================
    WHATSAPP_TOKEN: str
    WHATSAPP_PHONE_ID: str
    WHATSAPP_VERIFY_TOKEN: str
    WHATSAPP_APP_SECRET: Optional[str] = None  # For webhook signature validation
    WHATSAPP_API_VERSION: str = "v21.0"
    WHATSAPP_API_URL: str = "https://graph.facebook.com"
    
    @property
    def WHATSAPP_SEND_MESSAGE_URL(self) -> str:
        """Construct WhatsApp send message URL"""
        return f"{self.WHATSAPP_API_URL}/{self.WHATSAPP_API_VERSION}/{self.WHATSAPP_PHONE_ID}/messages"
    
    # ==================== GOOGLE OAUTH SETTINGS ====================
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/oauth/callback"
    GOOGLE_SCOPES: list = [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events"
    ]
    
    # ==================== SESSION SETTINGS ====================
    SESSION_TTL: int = 3600  # 1 hour
    CONVERSATION_MAX_MESSAGES: int = 10
    CONVERSATION_TTL: int = 3600  # 1 hour
    
    # ==================== RATE LIMITING ====================
    RATE_LIMIT_REQUESTS: int = 10  # requests per window
    RATE_LIMIT_WINDOW: int = 60  # seconds
    
    # ==================== CACHE SETTINGS ====================
    CACHE_TTL_SHORT: int = 300  # 5 minutes
    CACHE_TTL_MEDIUM: int = 1800  # 30 minutes
    CACHE_TTL_LONG: int = 3600  # 1 hour
    
    # ==================== AGENT SETTINGS ====================
    AGENT_MAX_ITERATIONS: int = 5
    AGENT_TIMEOUT: int = 30
    
    # ==================== TIMEOUT SETTINGS ====================
    # HTTP Client Timeouts (seconds)
    HTTP_CONNECT_TIMEOUT: float = 10.0
    HTTP_READ_TIMEOUT: float = 30.0
    HTTP_WRITE_TIMEOUT: float = 30.0
    HTTP_POOL_TIMEOUT: float = 10.0
    
    # WhatsApp API Timeouts
    WHATSAPP_TIMEOUT: float = 30.0
    
    # Google Calendar API Timeouts
    CALENDAR_API_TIMEOUT: float = 30.0
    
    # Circuit Breaker Settings
    CIRCUIT_FAILURE_THRESHOLD: int = 5
    CIRCUIT_SUCCESS_THRESHOLD: int = 2
    CIRCUIT_TIMEOUT_SECONDS: int = 60
    
    # Retry Settings
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_BASE_DELAY: float = 1.0
    RETRY_MAX_DELAY: float = 10.0
    
    # Redis Timeouts
    REDIS_CONNECT_TIMEOUT: float = 5.0
    REDIS_SOCKET_TIMEOUT: float = 10.0  # Must be > block timeout for stream consumers (5s)
    
    # ==================== LOGGING SETTINGS ====================
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Global settings instance
settings = get_settings()
