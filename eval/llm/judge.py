"""Évaluateur LLM en 2 passes (Critic + Verifier) pour les plans d'entraînement.

Flux :
  Pass 1 (Critic)   : Analyse plan + profil → scorecard JSON
  Pass 2 (Verifier) : Reçoit plan + profil + critique → valide/ajuste → JSON final

Le score composite est calculé à partir des scores du Verifier avec les poids définis dans config.py.
En cas d'échec du Verifier, la sortie du Critic est utilisée directement.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field

from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema
from eval.config import COMPOSITE_WEIGHTS, eval_settings
from eval.llm.prompts import build_critic_messages, build_verifier_messages

_SCORE_DIMENSIONS = list(COMPOSITE_WEIGHTS.keys())


@dataclass
class LLMEvaluation:
    scores: dict[str, int]
    composite_score: float
    points_forts: list[str]
    points_critiques: list[str]
    verdict_global: str
    verifier_adjustments: list[str]
    error: str | None = None  # rempli si une ou les 2 passes ont échoué
    critic_raw: dict = field(default_factory=dict)
    verifier_raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scores": self.scores,
            "composite_score": self.composite_score,
            "points_forts": self.points_forts,
            "points_critiques": self.points_critiques,
            "verdict_global": self.verdict_global,
            "verifier_adjustments": self.verifier_adjustments,
            "error": self.error,
        }


async def evaluate(
    profile: AthleteProfileSchema,
    plan: TrainingPlanSchema,
    semaphore: asyncio.Semaphore | None = None,
) -> LLMEvaluation:
    """Lance les 2 passes LLM et retourne l'évaluation finale.

    Args:
        profile: Profil athlète utilisé pour générer le plan.
        plan: Plan généré à évaluer.
        semaphore: Semaphore optionnel pour limiter le parallélisme.
    """
    if semaphore:
        async with semaphore:
            return await _run_evaluation(profile, plan)
    return await _run_evaluation(profile, plan)


async def _run_evaluation(
    profile: AthleteProfileSchema, plan: TrainingPlanSchema
) -> LLMEvaluation:
    provider = _get_provider()

    # ── Pass 1 : Critic ───────────────────────────────────────────────────────
    sys_critic, user_critic = build_critic_messages(profile, plan)
    try:
        critic_text = await provider.generate(
            sys_critic, user_critic, max_tokens=eval_settings.llm_critic_max_tokens
        )
        critic_json = _extract_json(critic_text)
        _validate_scorecard(critic_json)
    except Exception as exc:
        return _failure_result(f"Critic échoué : {exc}")

    # ── Pass 2 : Verifier ─────────────────────────────────────────────────────
    sys_verifier, user_verifier = build_verifier_messages(profile, plan, critic_json)
    try:
        verifier_text = await provider.generate(
            sys_verifier, user_verifier, max_tokens=eval_settings.llm_verifier_max_tokens
        )
        verifier_json = _extract_json(verifier_text)
        _validate_scorecard(verifier_json)
        final_json = verifier_json
    except Exception:
        # Si le verifier échoue, on utilise la sortie du critic directement
        final_json = critic_json
        final_json.setdefault("verifier_adjustments", [])

    # ── Score composite ───────────────────────────────────────────────────────
    scores = final_json["scores"]
    composite = round(
        sum(scores.get(dim, 5) * weight for dim, weight in COMPOSITE_WEIGHTS.items()),
        2,
    )

    return LLMEvaluation(
        scores=scores,
        composite_score=composite,
        points_forts=final_json.get("points_forts", []),
        points_critiques=final_json.get("points_critiques", []),
        verdict_global=final_json.get("verdict_global", ""),
        verifier_adjustments=final_json.get("verifier_adjustments", []),
        critic_raw=critic_json,
        verifier_raw=final_json,
    )


# ── Utilitaires privés ────────────────────────────────────────────────────────

def _get_provider():
    """Instancie directement le provider LLM sans passer par app.llm.factory
    (qui nécessite toutes les variables d'env Telegram/DB)."""
    if eval_settings.llm_provider == "anthropic":
        from app.llm.providers.anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=eval_settings.anthropic_api_key,
            model=eval_settings.llm_model,
        )
    from app.llm.providers.openrouter import OpenRouterProvider
    return OpenRouterProvider(
        api_key=eval_settings.openrouter_api_key,
        model=eval_settings.llm_model,
    )


def _extract_json(text: str) -> dict:
    """Extrait un objet JSON depuis la réponse LLM (gère les blocs markdown)."""
    # Cherche un bloc ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return json.loads(match.group(1).strip())

    # Cherche un objet JSON brut (commence par { )
    match = re.search(r"(\{[\s\S]*\})", text)
    if match:
        return json.loads(match.group(1).strip())

    raise ValueError(f"Aucun JSON trouvé dans la réponse LLM : {text[:200]!r}")


def _validate_scorecard(data: dict) -> None:
    """Vérifie que le JSON contient les champs minimaux attendus."""
    if "scores" not in data:
        raise ValueError("Champ 'scores' manquant dans la réponse LLM")
    for dim in _SCORE_DIMENSIONS:
        if dim not in data["scores"]:
            raise ValueError(f"Dimension '{dim}' manquante dans scores")
        score = data["scores"][dim]
        if not isinstance(score, (int, float)) or not (0 <= score <= 10):
            raise ValueError(f"Score '{dim}' invalide : {score!r}")


def _failure_result(error_msg: str) -> LLMEvaluation:
    """Retourne un résultat d'évaluation vide en cas d'erreur LLM."""
    return LLMEvaluation(
        scores={dim: 0 for dim in _SCORE_DIMENSIONS},
        composite_score=0.0,
        points_forts=[],
        points_critiques=[],
        verdict_global="",
        verifier_adjustments=[],
        error=error_msg,
    )
