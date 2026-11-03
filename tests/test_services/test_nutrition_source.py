"""Banister reads nutrition from wherever the athlete keeps it, and survives its absence."""
import uuid
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.db.models.user import User
from app.db.repositories import wellness_repo
from app.llm.chat import _tool_get_nutrition_summary, _verify_action_claim
from app.llm.tools import TOOL_DEFINITIONS
from app.services import nutrition_source


def _patch_transport(monkeypatch, handler):
    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(nutrition_source.httpx, "AsyncClient", client)
    monkeypatch.setattr(settings, "nutrition_api_url", "http://nutrition:8012")


@pytest.mark.asyncio
async def test_without_a_service_reads_intervals_wellness(db_session, monkeypatch):
    monkeypatch.setattr(settings, "nutrition_api_url", "")
    monkeypatch.setattr(nutrition_source, "paris_today", lambda: date(2026, 11, 3))
    user = User(telegram_id=7171, first_name="Test")
    db_session.add(user)
    await db_session.flush()
    await wellness_repo.upsert(
        db_session, user.id, date(2026, 11, 3), kcal_consumed=2100,
        protein_g=110.0, hydration_volume_l=1.5,
    )
    result = await _tool_get_nutrition_summary({"days": 2}, user=user, session=db_session)
    assert result["available"] is True and result["source"] == "intervals_wellness"
    empty, logged = result["days"]
    assert empty["kcal"] is None and empty["status"] == "empty"
    assert logged["kcal"] == 2100 and logged["protein_g"] == 110.0
    assert logged["hydration_ml"] == 1500


@pytest.mark.asyncio
async def test_service_contract(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, query=dict(request.url.params),
                    auth=request.headers.get("authorization"), method=request.method)
        return httpx.Response(200, json=[
            {"date": "2026-11-02", "status": "empty", "kcal": None},
            {"date": "2026-11-03", "kcal": 2400, "protein_g": 120, "extra_field": 1},
        ])

    _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(settings, "nutrition_api_token", SecretStr("secret"))
    result = await nutrition_source.daily_summary(None, uuid.uuid4(), 2)
    assert seen["path"] == "/v1/days" and seen["method"] == "GET"
    assert set(seen["query"]) == {"from", "to"} and seen["auth"] == "Bearer secret"
    assert result["days"][0]["kcal"] is None
    assert result["days"][1]["kcal"] == 2400 and "extra_field" not in result["days"][1]
    assert result["days"][1]["status"] is None  # optional field stays absent, not invented


@pytest.mark.asyncio
async def test_no_token_sends_no_header(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(settings, "nutrition_api_token", SecretStr(""))
    await nutrition_source.daily_summary(None, uuid.uuid4(), 1)
    assert seen["auth"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(401),
    lambda r: httpx.Response(200, content=b"not json"),
    lambda r: httpx.Response(200, json={"not": "a list"}),
    lambda r: httpx.Response(200, json=["not an object"]),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")),
])
async def test_failing_source_never_breaks_the_coach(monkeypatch, handler):
    _patch_transport(monkeypatch, handler)
    result = await nutrition_source.daily_summary(None, uuid.uuid4(), 7)
    assert result["available"] is False and "days" not in result


def test_banister_does_not_own_meal_logging():
    names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}
    assert "get_nutrition_summary" in names
    assert not names & {"log_meal", "undo_last_meal_entry", "get_calorie_history"}


def test_action_claim_without_tool_still_corrected():
    assert "rien n'a été enregistré" in _verify_action_claim("C'est noté, c'est enregistré.", [])
