import asyncio
import logging
from functools import partial

import anthropic

from app.core.localization import with_language_rule
from app.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 600,
    ) -> str:
        system_prompt = with_language_rule(system_prompt)
        logger.info("[LLM →] Anthropic | model=%s | sys=%d chars | user=%d chars",
                    self.model, len(system_prompt), len(user_message))
        logger.debug("[LLM PROMPT]\n--- SYSTEM ---\n%s\n--- USER ---\n%s",
                     system_prompt, user_message)
        # Le SDK Anthropic est synchrone — on l'exécute dans un thread
        loop = asyncio.get_event_loop()
        fn = partial(self._call_api, system_prompt, user_message, max_tokens)
        return await loop.run_in_executor(None, fn)

    def _call_api(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        for attempt in range(3):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
                content = response.content[0].text
                logger.info("[LLM ←] Anthropic | model=%s | in=%d out=%d tokens | %d chars | %.200s",
                            self.model, response.usage.input_tokens, response.usage.output_tokens,
                            len(content), content)
                logger.debug("[LLM RESPONSE]\n%s", content)
                return content
            except anthropic.RateLimitError:
                if attempt < 2:
                    import time
                    logger.warning("[LLM] Anthropic rate limit, tentative %d/3", attempt + 1)
                    time.sleep(2 ** attempt)
                else:
                    raise
        return ""
