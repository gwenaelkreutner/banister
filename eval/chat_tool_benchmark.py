"""Small, side-effect-free tool decision benchmark for the chat models.

Run without --live to inspect the cases. Live mode calls OpenRouter, but never
executes application tools or reads/writes an athlete database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import date

import httpx
from openai import APIStatusError, AsyncOpenAI

from app.config import settings
from app.engine.atl_ctl import FitnessMetrics
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
    TrainingPlanSchema,
)
from app.llm.chat_client import (
    _no_tool_definition,
    _parse_text_tool_calls,
    _validate_tool_arguments,
)
from app.llm.tools import build_system_prompt, tools_for_mode


@dataclass(frozen=True)
class Case:
    message: str
    expected: str
    mode: str = "goal"


CASES = [
    # The first twelve are the paid pilot's paired sample.
    Case("J'ai mangé une banane au goûter.", "log_meal"),
    Case("J'ai dîné de pâtes et de légumes, ajoute-le à ma journée.", "log_meal"),
    Case("Annule la dernière entrée de repas, je me suis trompé.", "undo_last_meal_entry"),
    Case("Combien de calories ai-je enregistrées hier ?", "get_calorie_history"),
    Case("J'ai mal au genou depuis hier, prends-le en compte.", "update_injury_status"),
    Case("Retiens que je préfère rouler le matin.", "update_coach_memory"),
    Case("Allège mon plan pour toute la semaine, je suis fatigué.", "propose_plan_modification"),
    Case("Demain j'ai une réunion, décale cette séance.", "propose_session_adjustment"),
    Case("Quelle est ma séance de demain ?", "get_upcoming_sessions"),
    Case("Bonjour, comment vas-tu ?", "respond_without_tool"),
    Case("Combien de calories contient une banane en général ?", "respond_without_tool"),
    Case("Propose-moi une séance de vélo pour aujourd'hui.",
         "get_freestyle_session_suggestion", "freestyle"),
    Case("Montre l'évolution de mon CTL sur 30 jours.", "get_fitness_history"),
    Case("Quelle est ma tendance de charge sur six semaines ?", "get_training_trend"),
    Case("Donne le détail de ma séance du 20 septembre.", "get_session_detail"),
    Case("Comment a évolué ma VFC cette semaine ?", "get_wellness_history"),
    Case("Quand ai-je parlé de ma précédente blessure ?", "memory_query"),
    Case("J'ai mangé une pomme et un yaourt ce matin.", "log_meal"),
    Case("J'ai pris un café après le déjeuner, note-le aussi.", "log_meal"),
    Case("Ce soir j'ai mangé du riz, du poulet et une salade.", "log_meal"),
    Case("Supprime mon dernier repas saisi aujourd'hui.", "undo_last_meal_entry"),
    Case("Quel est mon programme ce week-end ?", "get_upcoming_sessions"),
    Case("Je veux une séance moins intense que celle proposée.",
         "get_freestyle_session_suggestion", "freestyle"),
    Case("Je voudrais rouler 90 minutes aujourd'hui.",
         "get_freestyle_session_suggestion", "freestyle"),
    Case("Je peux publier la séance proposée ?", "respond_without_tool", "freestyle"),
    Case("Que signifie TSS ?", "respond_without_tool"),
    Case("J'ai une banane dans mon sac pour demain.", "respond_without_tool"),
    Case("Je n'ai pas mangé de banane, c'était une question.", "respond_without_tool"),
    Case("Garde en mémoire que mon épaule droite me gêne régulièrement.", "update_coach_memory"),
    Case("Sur les 7 derniers jours, comment a évolué mon sommeil ?", "get_wellness_history"),
]


def _system(mode: str) -> str:
    profile = AthleteProfileSchema(
        objective=ObjectiveProfile(type="fitness", target_date=None),
        availability=AvailabilityProfile(
            hours_per_week=6, preferred_days=["tuesday", "thursday", "saturday"]
        ),
        level="intermediate", structured_plan_history=True,
        equipment=EquipmentProfile(power_meter=True, ftp=220, ftp_source="declared"),
        physio=PhysioProfile(
            age=34, hr_max=186, hr_max_source="declared",
            hr_rest=58, hr_rest_source="declared",
        ),
        coaching_mode="power", health_constraints=False,
    )
    plan = None
    if mode == "goal":
        plan = TrainingPlanSchema(
            weeks=[], zones={}, initial_weekly_tss=200, peak_weekly_tss=300,
            weeks_count=4, coaching_mode="power", start_date=date.today(),
        )
    return build_system_prompt(
        first_name="Camille", profile=profile,
        metrics=FitnessMetrics(atl=28, ctl=32, tsb=4), recent_logs=[],
        plan=plan, today=date.today(),
    )


async def _metadata(client: httpx.AsyncClient, generation_id: str) -> tuple[float | None, str]:
    for _ in range(3):
        response = await client.get(
            "https://openrouter.ai/api/v1/generation", params={"id": generation_id},
        )
        if response.is_success:
            data = response.json().get("data", {})
            raw_cost = data.get("total_cost")
            return (
                float(raw_cost) if raw_cost is not None else None,
                data.get("provider_name") or "unknown",
            )
        await asyncio.sleep(0.5)
    return None, "unknown"


async def _run(max_usd: float, case_count: int, repeats: int) -> None:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for --live")
    models = ("deepseek/deepseek-v4-flash-0731", "qwen/qwen3.5-flash-02-23")
    llm = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key)
    spent = 0.0
    results = []
    for index, case in enumerate(CASES[:case_count], 1):
        tools = [*tools_for_mode(case.mode), _no_tool_definition()]
        for repeat in range(repeats):
            for model in models:
                    # Reserve more than the listed worst case for these short requests.
                    if spent + 0.005 > max_usd:
                        print(f"STOP budget: ${spent:.4f} spent; paired comparison incomplete")
                        return
                    started = time.monotonic()
                    try:
                        response = await llm.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": _system(case.mode)},
                                {"role": "user", "content": case.message},
                            ],
                            tools=tools, tool_choice="required", max_tokens=4096,
                            extra_body={"reasoning": {"enabled": False}},
                            extra_headers={"X-OpenRouter-Metadata": "enabled"},
                        )
                    except APIStatusError as error:
                        print(f"case={index} model={model} API error={error.status_code}")
                        return
                    choice = response.choices[0]
                    native = choice.message.tool_calls or []
                    text_calls = _parse_text_tool_calls(choice.message.content or "")
                    names = [call.function.name for call in native]
                    if not names:
                        names = [call["name"] for call in text_calls]
                    try:
                        valid = all(
                            _validate_tool_arguments(
                                call.function.name,
                                json.loads(call.function.arguments), tools,
                            ) is None for call in native
                        )
                    except (TypeError, ValueError):
                        valid = False
                    routing = (response.model_extra or {}).get("openrouter_metadata") or {}
                    provider = routing.get("provider_name") or routing.get("provider") or "unknown"
                    spent += 0.005
                    row = {
                        "case": index, "run": repeat + 1, "model": model,
                        "expected": case.expected, "selected": names,
                        "correct": names == [case.expected] and valid,
                        "finish": choice.finish_reason, "provider": provider,
                        "generation_id": response.id,
                        "routing_keys": sorted(routing),
                        "latency_s": round(time.monotonic() - started, 2),
                        "usd": None,
                    }
                    results.append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"}, timeout=15,
    ) as metadata_client:
        metadata = await asyncio.gather(*(
            _metadata(metadata_client, row["generation_id"]) for row in results
        ))
    for row, (cost, provider) in zip(results, metadata, strict=True):
        row["usd"] = cost
        if provider != "unknown":
            row["provider"] = provider
    for model in models:
        sample = [row for row in results if row["model"] == model]
        total = sum(row["usd"] or 0 for row in sample)
        providers = sorted({row["provider"] for row in sample})
        print(
            f"{model}: {sum(row['correct'] for row in sample)}/{len(sample)} correct, "
            f"${total:.4f}, providers={providers}"
        )
    actual = sum(row["usd"] or 0 for row in results)
    print(f"Total billed: ${actual:.4f}; reserved: ${spent:.4f}; ceiling ${max_usd:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run the 48 paid, read-only calls")
    parser.add_argument("--max-usd", type=float, default=0.40)
    parser.add_argument("--cases", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.max_usd > 0.40:
        parser.error("This pilot is capped at $0.40 (below the authorised €0.50)")
    if not 1 <= args.cases <= 12 or not 1 <= args.repeats <= 2:
        parser.error("The pilot is limited to 12 cases and two repeats")
    if args.live:
        asyncio.run(_run(args.max_usd, args.cases, args.repeats))
    else:
        for index, case in enumerate(CASES, 1):
            print(f"{index:02d} {case.mode:9s} {case.expected:36s} {case.message}")


if __name__ == "__main__":
    main()
