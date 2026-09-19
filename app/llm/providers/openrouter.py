import json
import logging

import httpx
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace

from app.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# Appel httpx brut, pas de SDK instrumentable — pas de span émis si Phoenix est
# désactivé (register() n'a jamais été appelé → tracer no-op, coût ~nul).
tracer = trace.get_tracer(__name__)


class OpenRouterProvider(LLMProvider):
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, model: str = "anthropic/claude-sonnet-4-6"):
        self.api_key = api_key
        self.model = model

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 600,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://banister.app",
            "X-Title": "Banister",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
        }

        logger.info("[LLM →] OpenRouter | model=%s | sys=%d chars | user=%d chars",
                    self.model, len(system_prompt), len(user_message))
        logger.debug("[LLM PROMPT]\n--- SYSTEM ---\n%s\n--- USER ---\n%s",
                     system_prompt, user_message)

        with tracer.start_as_current_span(
            "OpenRouterProvider.generate",
            attributes={
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                SpanAttributes.LLM_MODEL_NAME: self.model,
                SpanAttributes.LLM_PROVIDER: "openrouter",
                SpanAttributes.INPUT_VALUE: json.dumps(
                    {"system": system_prompt, "user": user_message}, ensure_ascii=False
                ),
                SpanAttributes.INPUT_MIME_TYPE: "application/json",
            },
        ) as span:
            return await self._post_with_retries(payload, headers, max_tokens, span)

    async def _post_with_retries(self, payload: dict, headers: dict, max_tokens: int, span) -> str:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(self.BASE_URL, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    choice = data["choices"][0]
                    content = choice["message"]["content"]
                    finish_reason = choice.get("finish_reason", "?")
                    usage = data.get("usage", {})
                    if content is None:
                        logger.warning("[LLM] Contenu null reçu (finish_reason=%s) — modèle=%s max_tokens=%d",
                                       finish_reason, self.model, max_tokens)
                        raise ValueError(f"LLM returned null content (finish_reason={finish_reason})")
                    logger.info("[LLM ←] OpenRouter | model=%s | tokens=%s | finish=%s | %d chars | %.200s",
                                self.model, usage.get("total_tokens", "?"), finish_reason, len(content), content)
                    if finish_reason == "length":
                        logger.warning("[LLM] Réponse tronquée (finish_reason=length) — max_tokens=%d atteint. "
                                       "Augmente max_tokens ou réduis le prompt.", max_tokens)
                    logger.debug("[LLM RESPONSE]\n%s", content)
                    span.set_attribute(SpanAttributes.OUTPUT_VALUE, content)
                    span.set_attribute(
                        SpanAttributes.LLM_TOKEN_COUNT_PROMPT, usage.get("prompt_tokens", 0)
                    )
                    span.set_attribute(
                        SpanAttributes.LLM_TOKEN_COUNT_COMPLETION, usage.get("completion_tokens", 0)
                    )
                    span.set_attribute(
                        SpanAttributes.LLM_TOKEN_COUNT_TOTAL, usage.get("total_tokens", 0)
                    )
                    return content
            except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
                if attempt < 2:
                    import asyncio
                    logger.warning("[LLM] OpenRouter tentative %d/3 échouée : %s", attempt + 1, e)
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        return ""
