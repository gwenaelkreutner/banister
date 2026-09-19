"""
Tracing LLM (Phoenix, self-hosted — voir CLAUDE.md § Observabilité).

auto_instrument=True patche les SDK anthropic/openai installés (via leurs paquets
openinference-instrumentation-*) dès l'appel à register() — donc tout appel fait par
app/llm/providers/anthropic.py et app/llm/chat_client.py (AsyncOpenAI) est tracé sans
changement de code là-bas. Seul l'appel httpx brut de providers/openrouter.py est
instrumenté à la main (voir ce fichier).
"""

import logging

logger = logging.getLogger(__name__)


def setup_observability() -> None:
    """Active le tracing Phoenix si PHOENIX_ENABLED=true. Ne doit jamais faire échouer
    le démarrage de l'app : un collecteur absent/injoignable dégrade en no-op (le
    BatchSpanProcessor d'OpenTelemetry droppe les spans en arrière-plan, sans lever)."""
    from app.config import settings

    if not settings.phoenix_enabled:
        logger.info("[OBSERVABILITY] Phoenix désactivé (PHOENIX_ENABLED=false)")
        return

    try:
        from phoenix.otel import register

        register(
            project_name="banister",
            endpoint=settings.phoenix_collector_endpoint,
            auto_instrument=True,
            batch=True,
        )
        logger.info(
            "[OBSERVABILITY] Phoenix tracing actif → %s", settings.phoenix_collector_endpoint
        )
    except Exception:
        logger.exception("[OBSERVABILITY] Échec d'initialisation Phoenix — tracing désactivé")
