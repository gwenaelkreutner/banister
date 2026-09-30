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
_MUTATING_TOOLS = frozenset({
    "update_injury_status", "update_coach_memory", "log_meal", "undo_last_meal_entry",
})


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


def _validate_tool_arguments(name: str, args: object, definitions: list[dict]) -> str | None:
    """Check the JSON Schema subset used by our tool definitions before dispatch."""
    definition = next(
        (tool["function"] for tool in definitions if tool["function"]["name"] == name), None
    )
    if definition is None:
        return f"Unknown tool: {name}"
    if not isinstance(args, dict):
        return "Tool arguments must be an object"
    if "parameters" not in definition:
        return None
    schema = definition.get("parameters") or {}
    properties = schema.get("properties", {})
    missing = [key for key in schema.get("required", []) if key not in args]
    if missing:
        return f"Missing required arguments: {', '.join(missing)}"
    for key, value in args.items():
        if key not in properties:
            return f"Unknown argument: {key}"
        field = properties[key]
        kind = field.get("type")
        if kind == "integer" and type(value) is not int:
            return f"{key} must be an integer"
        if kind == "string" and not isinstance(value, str):
            return f"{key} must be a string"
        if kind == "array" and not isinstance(value, list):
            return f"{key} must be an array"
        if "enum" in field and value not in field["enum"]:
            return f"{key} has an invalid value"
        if kind == "integer":
            if "minimum" in field and value < field["minimum"]:
                return f"{key} is below its minimum"
            if "maximum" in field and value > field["maximum"]:
                return f"{key} exceeds its maximum"
        if kind == "array":
            if "maxItems" in field and len(value) > field["maxItems"]:
                return f"{key} has too many items"
            item_schema = field.get("items", {})
            for item in value:
                if item_schema.get("type") == "string" and not isinstance(item, str):
                    return f"{key} has an invalid item type"
                if "enum" in item_schema and item not in item_schema["enum"]:
                    return f"{key} has an invalid item value"
    return None


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
    mutation_results: dict[tuple[str, str], dict] = {}
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
        logger.info(
            "[LLM →] agentic iter=%d/%d | model=%s | messages=%d | tools=%d",
            iteration + 1, max_iterations, effective_model,
            len(all_messages) + 1, len(decision_tools),
        )
        logger.debug("[LLM AGENTIC SYSTEM]\n%s", system)
        logger.debug("[LLM AGENTIC MESSAGES]\n%s",
                     json.dumps(all_messages[-4:], ensure_ascii=False, indent=2))

        # Some OpenRouter model/provider combinations return plain text even when
        # tool_choice=required. A text answer is not an executed tool decision.
        for decision_attempt in range(2 if iteration == 0 else 1):
            decision_model = (
                settings.chat_tool_fallback_model or effective_model
                if iteration == 0 and decision_attempt == 1
                else effective_model
            )
            try:
                response = await client.chat.completions.create(
                    model=decision_model,
                    messages=[{"role": "system", "content": system}] + all_messages,
                    tools=decision_tools,
                    tool_choice="required" if iteration == 0 else "auto",
                    max_tokens=1024 if decision_model != effective_model else 4096,
                    # Reasoning on this model consumed the tool-decision budget in
                    # production; keep the existing setting for all attempts.
                    extra_body={"reasoning": {"enabled": False}},
                )
            except Exception:
                logger.exception("Erreur appel LLM agentique | model=%s", decision_model)
                if iteration == 0 and decision_attempt == 1:
                    return t("llm.chat.no_tool_executed"), None, None, usage_total, tool_calls_log
                raise
            _track_usage(response)
            choice = response.choices[0]
            if iteration != 0:
                break
            content = (choice.message.content or "").strip()
            if choice.message.tool_calls or _parse_text_tool_calls(content):
                break
            logger.warning(
                "[LLM TOOL CONTRACT] required returned no tool | attempt=%d/2 "
                "finish=%s model=%s generation=%s",
                decision_attempt + 1, choice.finish_reason, decision_model,
                getattr(response, "id", None),
            )
        else:
            return t("llm.chat.no_tool_executed"), None, None, usage_total, tool_calls_log
        logger.info("[LLM ←] agentic | finish=%s | model=%s", choice.finish_reason, effective_model)

        # finish=length peut survenir quand un tool call JSON est tronqué :
        # tool_calls est parfois présent mais finish_reason != "tool_calls"
        has_tool_calls = bool(choice.message.tool_calls)
        if has_tool_calls:
            if choice.finish_reason == "length":
                logger.warning(
                    "[LLM WARN] finish=length avec tool_calls — "
                    "tool call potentiellement tronqué, tentative"
                )
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
                logger.info(
                    "[LLM TEXT TOOL] %d tool call(s) détecté(s) dans le texte", len(text_calls)
                )
                tool_results_for_prompt = []

                for tc in text_calls:
                    name = tc["name"]
                    args = tc["arguments"]
                    if name == _NO_TOOL_NAME:
                        if on_tool_event:
                            await on_tool_event(name, "started")
                            await on_tool_event(name, "finished")
                        continue
                    tool_used = name
                    if on_tool_event:
                        await on_tool_event(name, "started")
                    logger.info("[LLM TOOL (text) →] %s | args: %.200s", name, str(args))
                    validation_error = _validate_tool_arguments(name, args, decision_tools)
                    signature = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                    duplicate = name in _MUTATING_TOOLS and signature in mutation_results
                    if validation_error:
                        result = {"ok": False, "error": validation_error}
                    elif duplicate:
                        result = mutation_results[signature]
                    else:
                        try:
                            result = await tool_executor(name, args)
                            logger.info(
                                "[LLM TOOL (text) ←] %s | result: %.200s", name, str(result)
                            )
                        except Exception as e:
                            logger.warning("Erreur tool (text) %s: %s", name, e)
                            result = {"error": str(e)}
                        if name in _MUTATING_TOOLS:
                            mutation_results[signature] = result
                    last_tool_result = result
                    if not duplicate:
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
                return name, {}, {"ok": True}, tc.id, False
            if on_tool_event:
                await on_tool_event(name, "started")
            logger.info("[LLM TOOL →] %s | args: %.200s", name, tc.function.arguments)
            args: dict = {}
            duplicate = False
            try:
                args = json.loads(tc.function.arguments)
                validation_error = _validate_tool_arguments(name, args, decision_tools)
                if validation_error:
                    result = {"ok": False, "error": validation_error}
                else:
                    signature = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                    duplicate = name in _MUTATING_TOOLS and signature in mutation_results
                    if duplicate:
                        result = mutation_results[signature]
                    else:
                        result = await tool_executor(name, args)
                        logger.info("[LLM TOOL ←] %s | result: %.200s", name, str(result))
                        if name in _MUTATING_TOOLS:
                            mutation_results[signature] = result
            except Exception as e:
                logger.warning("Erreur tool %s: %s", name, e)
                result = {"error": str(e)}
            if on_tool_event:
                status = (
                    "failed" if result.get("error") or result.get("ok") is False
                    else "finished"
                )
                await on_tool_event(name, status)
            return name, args, result, tc.id, not duplicate

        if all(tc.function.name in parallel_tool_names for tc in tool_calls):
            executed_tools = await asyncio.gather(*(execute_native_tool(tc) for tc in tool_calls))
        else:
            executed_tools = [await execute_native_tool(tc) for tc in tool_calls]

        for name, args, result, tool_call_id, record in executed_tools:
            if name != _NO_TOOL_NAME:
                tool_used = name
                last_tool_result = result
                if record:
                    tool_calls_log.append({"name": name, "args": args, "result": result})
            all_messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False, default=str),
                "tool_call_id": tool_call_id,
            })

    # Fallback : dernier appel sans tools si on a épuisé les itérations
    logger.info(
        "[LLM →] agentic fallback | model=%s | messages=%d",
        effective_model, len(all_messages) + 1,
    )
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
