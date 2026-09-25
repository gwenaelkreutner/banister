from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Telegram
    telegram_bot_token: str
    telegram_owner_id: int = 0  # Telegram user ID of the bot owner (required)
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = "banister-secret"

    # Database (spec 003) — everything the athlete owns lives under one directory that
    # deleting removes entirely (spec 001 FR-004). `database_url`, when set, overrides the
    # derived path — used by the test suite to run the same schema against PostgreSQL
    # during the portability port (specs/003.../research.md), and kept as an escape hatch
    # for advanced deployments. Production defaults to SQLite under data_dir.
    data_dir: Path = Path("data")
    database_url: str | None = None

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'banister.db').as_posix()}"

    # LLM
    llm_provider: str = "anthropic"  # anthropic | openrouter
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    # Budget de sortie pour les appels LLM rédactionnels (narratif de plan, réponse
    # finale du chat agentique). Défaut haut volontairement : certains modèles
    # OpenRouter consomment beaucoup de tokens de "raisonnement" cachés avant d'émettre
    # du contenu visible — un budget bas se traduit par finish_reason=length et un
    # contenu vide, silencieusement absorbé par le fallback déterministe (vu en réel
    # avec stepfun/step-3.5-flash : 300–2048 insuffisant, 4000 suffisant).
    llm_max_tokens: int = 4000
    chat_model: str = "anthropic/claude-sonnet-4-6"  # modèle via OpenRouter pour le chat agentique

    # App
    app_language: str = "en"

    @field_validator("app_language")
    @classmethod
    def _language_has_catalog(cls, value: str) -> str:
        catalog = (
            Path(__file__).resolve().parent.parent
            / "locales"
            / value
            / "LC_MESSAGES"
            / "banister.mo"
        )
        if not catalog.is_file():
            raise ValueError(f"Unsupported APP_LANGUAGE: {value!r}; no translation catalog found")
        return value
    environment: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # Redis (optionnel)
    redis_url: str | None = None

    # Observabilité LLM (Phoenix, self-hosted — app/observability.py). Off par défaut :
    # un déploiement sans conteneur Phoenix ne doit rien changer au comportement.
    phoenix_enabled: bool = False
    # "phoenix" résout par DNS Docker vers le conteneur du réseau externe partagé
    # "observability" (voir docker-compose.yml) — pas localhost, qui dans ce conteneur
    # ne désigne jamais l'hôte ni le conteneur Phoenix.
    phoenix_collector_endpoint: str = "http://phoenix:6006/v1/traces"
    # Collecteur Phoenix authentifié (optionnel) — pas encore câblé dans
    # app/observability.py (le déploiement self-hosted documenté dans CLAUDE.md n'a pas
    # d'authentification). Accepté ici uniquement pour que Settings() ne lève pas
    # (pydantic-settings a extra="forbid" par défaut) quand la variable est présente en
    # local — trouvé en lançant la suite de tests, sans rapport avec /review ou /goal.
    phoenix_api_key: SecretStr = SecretStr("")

    # intervals.icu (spec 002) — the sole mandatory training data source. Required, not
    # defaulted: FR-003 requires startup to refuse with a message naming this setting
    # when it is absent.
    #
    # On FR-004 ("store the credential encrypted at rest, refuse to persist unencrypted
    # if secure storage is unavailable"): that requirement was written with an
    # OAuth-token-in-a-database model in mind (the app receives a token via a callback
    # and must decide how to store it — the shape the old OAuth-based provider used).
    # This key does not fit that model. The operator writes it directly into .env before
    # the app ever starts; the app only reads it, and never persists it anywhere else.
    # There is no persistence step for FR-004's "refuse to write unencrypted" half to
    # govern, and building keyring/Fernet-style secret storage would not even work in
    # the primary deployment target (docker-compose, spec 001) — a container has no
    # access to an OS keychain, so that storage would always be "unavailable" and the
    # app would always refuse to start.
    # SecretStr is the part of FR-004 that *does* apply here: it stops the raw key from
    # leaking into `repr(settings)`, tracebacks, or an incidental debug log — the actual
    # accidental-exposure risk for a value the app only ever holds in memory. Call
    # `.get_secret_value()` to use it (see verify_intervals_credential() and
    # IntervalsClient).
    intervals_api_key: SecretStr
    # Athlete id is intentionally optional: "0" resolves to whichever athlete the key
    # belongs to (research R2), so nothing needs to be configured for the common case.
    intervals_athlete_id: str = "0"
    # FR-007: five minutes by default, configurable, with a floor that protects the
    # source's published quota (~5000 requests/day) — the floor is enforced in the
    # poller (T033), not here, since Settings has no natural place to raise a startup
    # error for an out-of-range value without duplicating that logic.
    intervals_poll_interval_minutes: int = 5

    # Persona — filename in personas/ without .yaml. Deployer default; per-athlete
    # override lives in users.coach_voice (spec 007). "coach-default" is both the
    # deployer default and guaranteed-present fallback target (FR-026).
    persona: str = "coach-default"

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"

    @property
    def use_webhook(self) -> bool:
        return bool(self.telegram_webhook_url) and not self.is_dev


settings = Settings()
