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

    # Database
    database_url: str

    # LLM
    llm_provider: str = "anthropic"  # anthropic | openrouter
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    llm_max_tokens: int = 600
    chat_model: str = "anthropic/claude-sonnet-4-6"  # modèle via OpenRouter pour le chat agentique

    # App
    environment: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # Redis (optionnel)
    redis_url: str | None = None

    # Strava OAuth (secondary provider — see docs/STRAVA_COMPLIANCE.md)
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = ""
    strava_state_secret: str = ""
    strava_webhook_verify_token: str = ""

    # Sport provider
    sport_provider: str = "intervals_icu"  # intervals_icu | strava | manual

    # intervals.icu (primary provider)
    intervals_api_key: str = ""
    intervals_athlete_id: str = ""
    intervals_poll_interval_minutes: int = 15

    # Persona
    persona: str = "coach-default"  # filename in personas/ without .yaml

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"

    @property
    def use_webhook(self) -> bool:
        return bool(self.telegram_webhook_url) and not self.is_dev


settings = Settings()
