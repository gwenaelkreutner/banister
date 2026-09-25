from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.routers import recap
from app.config import settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", "No data yet"), ("fr", "Pas encore de données")],
)
async def test_empty_recap_uses_installation_language(monkeypatch, language, expected):
    monkeypatch.setattr(settings, "app_language", language)
    loading = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(answer=AsyncMock(return_value=loading))
    monkeypatch.setattr(
        recap,
        "compute_weekly_recap",
        AsyncMock(return_value=SimpleNamespace(has_data=False)),
    )

    await recap.cmd_recap(message, session=None, user=SimpleNamespace(onboarding_completed=True))

    assert expected in message.answer.await_args_list[-1].args[0]
    loading.delete.assert_awaited_once()
