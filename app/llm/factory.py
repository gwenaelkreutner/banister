from functools import lru_cache

from app.config import settings
from app.llm.providers.base import LLMProvider


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    """Retourne le provider LLM configuré (singleton)."""
    if settings.llm_provider == "anthropic":
        from app.llm.providers.anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
        )
    elif settings.llm_provider == "openrouter":
        from app.llm.providers.openrouter import OpenRouterProvider
        return OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            model=settings.llm_model,
        )
    else:
        raise ValueError(f"Provider LLM inconnu : {settings.llm_provider}")
