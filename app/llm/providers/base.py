from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Interface commune pour tous les providers LLM."""

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 600,
    ) -> str:
        """Génère une réponse textuelle."""
        ...
