"""Configuration du système d'évaluation offline."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # ignore les champs Telegram/DB non pertinents pour l'éval
    )

    # Provider LLM (reprend les mêmes clés que l'app principale)
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""

    # Tokens pour les 2 passes LLM
    llm_critic_max_tokens: int = 1500
    llm_verifier_max_tokens: int = 1000


eval_settings = EvalSettings()

# ── Pondération du score composite ────────────────────────────────────────────
# Doit sommer à 1.0. La sécurité récupération est la plus critique.
COMPOSITE_WEIGHTS: dict[str, float] = {
    "securite_recuperation": 0.30,
    "progressivite_charge": 0.25,
    "respect_profil_utilisateur": 0.20,
    "coherence_physiologique": 0.15,
    "adequation_objectif": 0.10,
}

# Seuils de classification des scores composites
SCORE_CRITICAL = 4.0   # < 4 → plan dangereux
SCORE_CONCERNING = 6.0  # 4-6 → plan préoccupant
SCORE_ACCEPTABLE = 8.0  # 6-8 → plan acceptable
# >= 8 → excellent

# Seuil pour détecter une dimension LLM problématique (cluster d'erreurs)
LOW_DIMENSION_THRESHOLD = 6.0

# Parallélisme LLM (semaphore)
LLM_MAX_CONCURRENT = 3
