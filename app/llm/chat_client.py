"""
Client agentique pour le chat — utilise openai SDK avec base_url OpenRouter.
Flexible : n'importe quel modèle supportant le tool_use via OpenRouter.

Compatibilité étendue : détecte les tool calls au format texte (TOOLCALL>[...])
émis par les modèles qui ne supportent pas le function calling natif OpenAI.
"""

import json
import logging
import re

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

# Regex pour détecter TOOLCALL>[...] émis par les modèles sans function calling natif
_TEXT_TOOLCALL_RE = re.compile(r'TOOLCALL>\[(.+?)(?:\]>|(?=\s*$))', re.DOTALL)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
        )
    return _client


def _parse_text_tool_calls(content: str) -> list[dict]:
    """
    Parse les tool calls au format texte TOOLCALL>[{"name":..., "arguments":{...}}]>
    émis par les modèles qui ne supportent pas le function calling natif.
    Retourne une liste de dicts {name, arguments} ou liste vide.
    """
    if "TOOLCALL>" not in content:
        return []

    start = content.find("TOOLCALL>[")
    if start == -1:
        return []

    raw = content[start + len("TOOLCALL>["):]
    # Nettoyer les terminateurs >  ]> optionnels
    raw = raw.rstrip()
    if raw.endswith("]>"):
        raw = raw[:-2]
    elif raw.endswith(">"):
        raw = raw[:-1]
    if raw.endswith("]"):
        raw = raw[:-1]

    try:
        parsed = json.loads(f"[{raw.strip()}]")
        return [
            c for c in parsed
            if isinstance(c, dict) and "name" in c and "arguments" in c
        ]
    except (json.JSONDecodeError, Exception):
        logger.warning("[LLM TEXT TOOL] Impossible de parser: %.200s", raw)
        return []


async def run_agentic_loop(
    system: str,
    messages: list[dict],
    tools: list[dict],
    tool_executor,  # callable(name: str, args: dict) -> dict
    model: str | None = None,
    max_iterations: int = 3,
) -> tuple[str, str | None, dict | None]:
    """
    Exécute la boucle agentique tool_use → tool_result jusqu'à end_turn.

    Retourne (response_text, tool_used_name | None, last_tool_result | None).
    """
    client = _get_client()
    effective_model = model or settings.chat_model

    all_messages = list(messages)
    tool_used = None
    last_tool_result: dict | None = None

    for iteration in range(max_iterations):
        logger.info("[LLM →] agentic iter=%d/%d | model=%s | messages=%d | tools=%d",
                    iteration + 1, max_iterations, effective_model, len(all_messages) + 1, len(tools))
        logger.debug("[LLM AGENTIC SYSTEM]\n%s", system)
        logger.debug("[LLM AGENTIC MESSAGES]\n%s",
                     json.dumps(all_messages[-4:], ensure_ascii=False, indent=2))

        try:
            response = await client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "system", "content": system}] + all_messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=4096,
            )
        except Exception:
            logger.exception("Erreur appel LLM agentique")
            raise

        choice = response.choices[0]
        logger.info("[LLM ←] agentic | finish=%s | model=%s", choice.finish_reason, effective_model)

        # finish=length peut survenir quand un tool call JSON est tronqué :
        # tool_calls est parfois présent mais finish_reason != "tool_calls"
        has_tool_calls = bool(choice.message.tool_calls)
        if choice.finish_reason == "tool_calls" or (choice.finish_reason == "length" and has_tool_calls):
            if choice.finish_reason == "length":
                logger.warning("[LLM WARN] finish=length avec tool_calls — tool call potentiellement tronqué, tentative")
        elif choice.finish_reason != "tool_calls":
            content = (choice.message.content or "").strip()

            if not content:
                logger.warning("[LLM WARN] contenu vide (finish=%s, iter=%d) — fallback sans tools",
                               choice.finish_reason, iteration + 1)
                # Fallback : réessai sans tools, contexte limité aux 4 derniers messages
                try:
                    fb_resp = await client.chat.completions.create(
                        model=effective_model,
                        messages=[{"role": "system", "content": system}] + all_messages[-4:],
                        max_tokens=400,
                    )
                    content = (fb_resp.choices[0].message.content or "").strip()
                    if content:
                        logger.info("[LLM FALLBACK OK] contenu récupéré | iter=%d", iteration + 1)
                except Exception as fb_exc:
                    logger.warning("[LLM FALLBACK ERROR] %s", fb_exc)

                if not content:
                    raise RuntimeError(f"LLM response empty (finish={choice.finish_reason})")

            # Détection : modèle sans function calling natif → tool call en texte brut
            text_calls = _parse_text_tool_calls(content)
            if text_calls:
                logger.info("[LLM TEXT TOOL] %d tool call(s) détecté(s) dans le texte", len(text_calls))
                tool_results_for_prompt = []

                for tc in text_calls:
                    name = tc["name"]
                    args = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
                    tool_used = name
                    logger.info("[LLM TOOL (text) →] %s | args: %.200s", name, str(args))
                    try:
                        result = await tool_executor(name, args)
                        last_tool_result = result
                        logger.info("[LLM TOOL (text) ←] %s | result: %.200s", name, str(result))
                    except Exception as e:
                        logger.warning("Erreur tool (text) %s: %s", name, e)
                        result = {"error": str(e)}
                        last_tool_result = result
                    tool_results_for_prompt.append((name, result))

                # Appel final sans tools — présenter les résultats comme contexte texte
                result_ctx = "\n".join(
                    f"[{name}] {json.dumps(res, ensure_ascii=False, default=str)}"
                    for name, res in tool_results_for_prompt
                )
                final_messages = all_messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": (
                        f"Résultats des outils :\n{result_ctx}\n\n"
                        "Réponds maintenant à l'athlète en expliquant ce qui est proposé."
                    )},
                ]
                logger.info("[LLM →] agentic final (text-tool) | model=%s", effective_model)
                final_resp = await client.chat.completions.create(
                    model=effective_model,
                    messages=[{"role": "system", "content": system}] + final_messages,
                    max_tokens=600,
                )
                final_content = (final_resp.choices[0].message.content or "").strip()
                if not final_content:
                    raise RuntimeError("LLM final response empty after text tool call")
                logger.info("[LLM REPLY] %.300s", final_content)
                logger.debug("[LLM FULL REPLY]\n%s", final_content)
                return final_content, tool_used, last_tool_result

            # Réponse texte normale
            logger.info("[LLM REPLY] %.300s", content)
            logger.debug("[LLM FULL REPLY]\n%s", content)
            return content, tool_used, last_tool_result

        # Traiter les tool calls natifs
        tool_calls = choice.message.tool_calls or []
        if not tool_calls:
            return choice.message.content or "", tool_used, last_tool_result

        # Ajouter le message assistant avec les tool calls
        all_messages.append({
            "role": "assistant",
            "content": choice.message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        # Exécuter chaque tool call
        for tc in tool_calls:
            tool_used = tc.function.name
            logger.info("[LLM TOOL →] %s | args: %.200s", tc.function.name, tc.function.arguments)
            try:
                args = json.loads(tc.function.arguments)
                result = await tool_executor(tc.function.name, args)
                last_tool_result = result
                logger.info("[LLM TOOL ←] %s | result: %.200s", tc.function.name, str(result))
            except Exception as e:
                logger.warning("Erreur tool %s: %s", tc.function.name, e)
                result = {"error": str(e)}
                last_tool_result = result

            all_messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False, default=str),
                "tool_call_id": tc.id,
            })

    # Fallback : dernier appel sans tools si on a épuisé les itérations
    logger.info("[LLM →] agentic fallback | model=%s | messages=%d", effective_model, len(all_messages) + 1)
    response = await client.chat.completions.create(
        model=effective_model,
        messages=[{"role": "system", "content": system}] + all_messages,
        max_tokens=400,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("LLM fallback response empty after max iterations")
    logger.info("[LLM REPLY] %.300s", content)
    return content, tool_used, last_tool_result
