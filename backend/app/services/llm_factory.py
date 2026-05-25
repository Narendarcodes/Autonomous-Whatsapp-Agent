"""
LLM Factory
Provides a centralized way to get the configured LLM service instance
"""

from functools import lru_cache

from app.core.config import settings
from app.core.logging import logger
from app.services.llm_service import LLMService


@lru_cache()
def get_llm_service() -> LLMService:
    """
    Get the configured LLM service instance.
    Uses USE_GITHUB_MODELS setting to determine which service to return.
    
    Returns:
        LLMService: Configured LLM service (GitHub Models or Ollama)
    """
    if settings.USE_GITHUB_MODELS:
        from app.services.github_models_service import github_models_service
        logger.info("🤖 Using GitHub Models as LLM provider")
        return github_models_service
    else:
        from app.services.ollama_service import ollama_service
        logger.info("🤖 Using Ollama as LLM provider")
        return ollama_service


# Convenience alias for cleaner imports
llm_service = get_llm_service()
