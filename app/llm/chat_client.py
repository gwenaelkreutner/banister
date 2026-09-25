"""
Client agentique pour le chat — utilise openai SDK avec base_url OpenRouter.
Flexible : n'importe quel modèle supportant le tool_use via OpenRouter.

Compatibilité étendue : détecte les tool calls au format texte (TOOLCALL>[...])
émis par les modèles qui ne supportent pas le function calling natif OpenAI.
"""

import asyncio
import json
import logging
import re

from openai import AsyncOpenAI

from app.config import settings
from app.core.localization import t

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

_NO_TOOL_NAME = "respond_without_tool"


def _no_tool_definition() -> dict:
    return {
        "type": "function",
        "function": {
            "name": _NO_TOOL_NAME,
            "description": t("llm.tools.respond_without_tool.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": t("llm.tools.respond_without_tool.answer_description"),
                    },
                },
                "required": ["answer"],
            },
        },
    }

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
    parallel_tool_names: frozenset[str] = frozenset(),
    on_tool_event=None,  # async callable(name, "started" | "finished" | "failed")
) -> tuple[str, str | None, dict | None, dict, list[dict]]:
    """
    Exécute la boucle agentique tool_use → tool_result jusqu'à end_turn.

    Un tour de chat peut déclencher plusieurs appels API (itération avec tool call,
    fallback sur contenu vide, appel final après tool call en texte, fallback de fin de
    boucle) — `usage` cumule les tokens de TOUS ces appels, pas juste le dernier.

    Retourne (response_text, tool_used_name | None, last_tool_result | None, usage,
    tool_calls_log). `usage` = {"prompt_tokens", "completion_tokens", "total_tokens",
    "calls"} — tous à 0 si l'API n'a jamais renvoyé de champ `usage` (ne devrait pas
    arriver avec OpenRouter/OpenAI, mais mieux vaut 0 que planter sur un provider qui
    l'omettrait). `tool_calls_log` = liste de {"name", "args", "result"} pour CHAQUE
    tool call exécuté ce tour (`last_tool_result` n'en garde que le dernier — un tour
    qui enregistre plusieurs repas dans la même conversation a besoin des autres aussi,
    pour construire une confirmation qui ne dépend pas de ce que le LLM dit avoir fait).
    """
    client = _get_client()
    effective_model = model or settings.chat_model

    all_messages = list(messages)
    decision_tools = [*tools, _no_tool_definition()]
    tool_used = None
    last_tool_result: dict | None = None
    tool_calls_log: list[dict] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    def _track_usage(response) -> None:
        u = getattr(response, "usage", None)
        if u is None:
            return
        usage_total["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
        usage_total["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
        usage_total["total_tokens"] += getattr(u, "total_tokens", 0) or 0
        usage_total["calls"] += 1

    for iteration in range(max_iterations):
        logger.info("[LLM →] agentic iter=%d/%d | model=%s | messages=%d | tools=%d",
                    iteration + 1, max_iterations, effective_model, len(all_messages) + 1, len(decision_tools))
        logger.debug("[LLM AGENTIC SYSTEM]\n%s", system)
        logger.debug("[LLM AGENTIC MESSAGES]\n%s",
                     json.dumps(all_messages[-4:], ensure_ascii=False, indent=2))

        try:
            response = await client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "system", "content": system}] + all_messages,
                tools=decision_tools,
                tool_choice="required" if iteration == 0 else "auto",
                max_tokens=4096,
                # Found live (2026-09-18): with reasoning left on, deepseek-v4-flash
                # burns 1000-2000+ hidden reasoning tokens on this exact production
                # context (long system prompt, real history, 6-8 tools) and then
                # answers in plain text with finish_reason="stop" and NO tool call at
                # all — confirmed by replaying the real request both ways. Disabling
                # reasoning only for this tool-decision call restores tool_calls
                # reliably and costs far fewer tokens; OpenRouter's `reasoning` field
                # is a unified param other providers/models simply ignore, so this is
                # safe to apply unconditionally here.
                extra_body={"reasoning": {"enabled": False}},
            )
        except Exception:
            logger.exception("Erreur appel LLM agentique")
            raise
        _track_usage(response)

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
                    _track_usage(fb_resp)
                    content = (fb_resp.choices[0].message.content or "").strip()
                    if content:
                        logger.info("[LLM FALLBACK OK] contenu récupéré | iter=%d", iteration + 1)
                except Exception as fb_exc:
                    logger.warning("[LLM FALLBACK ERROR] %s", fb_exc)

                if not content:
                    raise RuntimeError(f"LLM response empty (finish={choice.finish_reason})")

            # Détection : modèle sans function calling natif → tool call en texte brut
            text_calls = _parse_text_tool_calls(content)
            if len(text_calls) == 1 and text_calls[0]["name"] == _NO_TOOL_NAME:
                arguments = text_calls[0].get("arguments")
                answer = arguments.get("answer", "") if isinstance(arguments, dict) else ""
                if on_tool_event:
                    await on_tool_event(_NO_TOOL_NAME, "started")
                    await on_tool_event(_NO_TOOL_NAME, "finished")
                answer = (
                    answer if isinstance(answer, str) and answer
                    else t("llm.chat_client.no_tool_used_fallback")
                )
                return answer, tool_used, last_tool_result, usage_total, tool_calls_log
            if text_calls:
                logger.info("[LLM TEXT TOOL] %d tool call(s) détecté(s) dans le texte", len(text_calls))
                tool_results_for_prompt = []

                for tc in text_calls:
                    name = tc["name"]
                    args = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
                    if name == _NO_TOOL_NAME:
                        if on_tool_event:
                            await on_tool_event(name, "started")
                            await on_tool_event(name, "finished")
                        continue
                    tool_used = name
                    if on_tool_event:
                        await on_tool_event(name, "started")
                    logger.info("[LLM TOOL (text) →] %s | args: %.200s", name, str(args))
                    try:
                        result = await tool_executor(name, args)
                        last_tool_result = result
                        logger.info("[LLM TOOL (text) ←] %s | result: %.200s", name, str(result))
                    except Exception as e:
                        logger.warning("Erreur tool (text) %s: %s", name, e)
                        result = {"error": str(e)}
                        last_tool_result = result
                    tool_calls_log.append({"name": name, "args": args, "result": result})
                    if on_tool_event:
                        status = (
                            "failed" if result.get("error") or result.get("ok") is False
                            else "finished"
                        )
                        await on_tool_event(name, status)
                    tool_results_for_prompt.append((name, result))

                # Appel final sans tools — présenter les résultats comme contexte texte
                result_ctx = "\n".join(
                    f"[{name}] {json.dumps(res, ensure_ascii=False, default=str)}"
                    for name, res in tool_results_for_prompt
                )
                final_messages = all_messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": t(
                        "llm.chat_client.tool_results_followup", context=result_ctx
                    )},
                ]
                logger.info("[LLM →] agentic final (text-tool) | model=%s", effective_model)
                final_resp = await client.chat.completions.create(
                    model=effective_model,
                    messages=[{"role": "system", "content": system}] + final_messages,
                    max_tokens=settings.llm_max_tokens,
                )
                _track_usage(final_resp)
                final_content = (final_resp.choices[0].message.content or "").strip()
                if not final_content:
                    raise RuntimeError("LLM final response empty after text tool call")
                logger.info("[LLM REPLY] %.300s", final_content)
                logger.debug("[LLM FULL REPLY]\n%s", final_content)
                logger.info("[LLM USAGE] %s", usage_total)
                return final_content, tool_used, last_tool_result, usage_total, tool_calls_log

            # Réponse texte normale
            logger.info("[LLM REPLY] %.300s", content)
            logger.debug("[LLM FULL REPLY]\n%s", content)
            logger.info("[LLM USAGE] %s", usage_total)
            return content, tool_used, last_tool_result, usage_total, tool_calls_log

        # Traiter les tool calls natifs
        tool_calls = choice.message.tool_calls or []
        if not tool_calls:
            logger.info("[LLM USAGE] %s", usage_total)
            return (
                choice.message.content or "", tool_used, last_tool_result,
                usage_total, tool_calls_log,
            )

        if len(tool_calls) == 1 and tool_calls[0].function.name == _NO_TOOL_NAME:
            try:
                answer = json.loads(tool_calls[0].function.arguments).get("answer", "")
            except (AttributeError, TypeError, ValueError):
                answer = ""
            if on_tool_event:
                await on_tool_event(_NO_TOOL_NAME, "started")
                await on_tool_event(_NO_TOOL_NAME, "finished")
            answer = (
                answer if isinstance(answer, str) and answer
                else t("llm.chat_client.no_tool_used_fallback")
            )
            return answer, tool_used, last_tool_result, usage_total, tool_calls_log

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

        async def execute_native_tool(tc):
            name = tc.function.name
            if name == _NO_TOOL_NAME:
                if on_tool_event:
                    await on_tool_event(name, "started")
                    await on_tool_event(name, "finished")
                return name, {}, {"ok": True}, tc.id
            if on_tool_event:
                await on_tool_event(name, "started")
            logger.info("[LLM TOOL →] %s | args: %.200s", name, tc.function.arguments)
            args: dict = {}
            try:
                args = json.loads(tc.function.arguments)
                result = await tool_executor(name, args)
                logger.info("[LLM TOOL ←] %s | result: %.200s", name, str(result))
            except Exception as e:
                logger.warning("Erreur tool %s: %s", name, e)
                result = {"error": str(e)}
            if on_tool_event:
                status = (
                    "failed" if result.get("error") or result.get("ok") is False
                    else "finished"
                )
                await on_tool_event(name, status)
            return name, args, result, tc.id

        if all(tc.function.name in parallel_tool_names for tc in tool_calls):
            executed_tools = await asyncio.gather(*(execute_native_tool(tc) for tc in tool_calls))
        else:
            executed_tools = [await execute_native_tool(tc) for tc in tool_calls]

        for name, args, result, tool_call_id in executed_tools:
            if name != _NO_TOOL_NAME:
                tool_used = name
                last_tool_result = result
                tool_calls_log.append({"name": name, "args": args, "result": result})
            all_messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False, default=str),
                "tool_call_id": tool_call_id,
            })

    # Fallback : dernier appel sans tools si on a épuisé les itérations
    logger.info("[LLM →] agentic fallback | model=%s | messages=%d", effective_model, len(all_messages) + 1)
    response = await client.chat.completions.create(
        model=effective_model,
        messages=[{"role": "system", "content": system}] + all_messages,
        max_tokens=settings.llm_max_tokens,
    )
    _track_usage(response)
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("LLM fallback response empty after max iterations")
    logger.info("[LLM REPLY] %.300s", content)
    logger.info("[LLM USAGE] %s", usage_total)
    return content, tool_used, last_tool_result, usage_total, tool_calls_log
