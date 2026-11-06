"""Tools offered to the agent (toolset `banister`). No training logic here: Banister computes.

Approving a proposal and withdrawing a publication are deliberately NOT tools. They only exist
as buttons handled in `__init__.py`, so the model can never write to the calendar by itself.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from collections import deque

from .client import BanisterError, call

send_card = None  # set by register(): approval card sent by code, buttons included

# What the agent received lately, so the final reply can be checked against it.
_RETRIEVED_MAX_AGE_SECONDS = 6 * 3600
_retrieved: deque = deque(maxlen=40)


def retrieved_values() -> list[dict]:
    oldest = time.time() - _RETRIEVED_MAX_AGE_SECONDS
    return [{"kind": kind, "result": result} for at, kind, result in _retrieved if at >= oldest]


def _remember(kind: str, result):
    _retrieved.append((time.time(), kind, result))
    return result


def _tool(fn):
    def handler(args: dict, **_kw) -> str:
        try:
            return json.dumps(fn(args or {}), ensure_ascii=False)
        except BanisterError as exc:
            return json.dumps({"error": exc.detail}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return handler


def _context(a):
    return _remember("context", call("GET", "/context", params={"days": a.get("days"), "ahead": a.get("ahead")}))


def _fitness(a):
    params = {"days": a.get("days"), "granularity": a.get("granularity")}
    return _remember("fitness", call("GET", "/fitness", params=params))


def _trend(a):
    return _remember("trend", call("GET", "/trend", params={"days": a.get("days")}))


def _wellness(a):
    params = {"days": a.get("days"), "metrics": a["metrics"], "granularity": a.get("granularity")}
    return _remember("wellness", call("GET", "/wellness", params=params))


def _ride(a):
    day = urllib.parse.quote(str(a["date"]).strip(), safe="")
    return _remember("ride", call("GET", f"/rides/{day}"))


def _memory(a):
    params = {"keyword": a.get("keyword"), "category": a.get("category"), "days": a.get("days")}
    return call("GET", "/memory", params=params)


def _add_note(a):
    return call("POST", "/memory/notes", {"category": a["category"], "note": a["note"]})


def _find(a):
    keys = ("workout_type", "family", "phase", "max_minutes", "detail")
    params = {k: a.get(k) for k in keys}
    params["q"] = a.get("query")
    if params["detail"] is not None:
        params["detail"] = "true" if params["detail"] else "false"
    return call("GET", "/sessions/catalog", params=params)


def _recommendation(a):
    params = {k: a.get(k) for k in ("workout_type", "max_minutes", "duration_minutes")}
    return _remember("recommendation", call("GET", "/sessions/recommendation", params=params))


def _draft(a) -> dict:
    keys = ("date", "template_id", "duration_minutes", "steps", "workout_type", "title")
    return {k: a[k] for k in keys if a.get(k) not in (None, "", [])}


def _evaluate(a):
    return _remember("session", call("POST", "/sessions/evaluate", _draft(a)))


def _propose(a):
    result = _remember("session", call("POST", "/proposals", _draft(a)))
    sent = bool(send_card and send_card(
        result["card_text"],
        [(result["card_buttons"]["approve"], f"ok:{result['proposal_id']}"),
         (result["card_buttons"]["decline"], f"no:{result['proposal_id']}")],
    ))
    light = {k: v for k, v in result.items() if k not in ("card_text", "card_buttons", "steps")}
    return {**light, "card_sent": sent}


def _publications(_a):
    return call("GET", "/publications")


def _request_withdrawal(a):
    publication_id = urllib.parse.quote(str(a["publication_id"]).strip(), safe="")
    card = call("GET", f"/publications/{publication_id}/withdrawal-card")
    sent = bool(send_card and send_card(
        card["card_text"],
        [(card["card_buttons"]["withdraw"], f"wd:{card['publication_id']}"),
         (card["card_buttons"]["keep"], f"keep:{card['publication_id']}")],
    ))
    return {"publication_id": card["publication_id"], "card_sent": sent}


_STEP = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["warmup", "work", "recovery", "cooldown", "steady"]},
        "duration_minutes": {"type": "integer", "minimum": 1},
        "zone_code": {"type": "string", "enum": ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"]},
    },
    "required": ["kind", "duration_minutes", "zone_code"],
}
_STEPS = {
    "type": "array",
    "description": (
        "Ordered structure for a session you compose. Each item is a step, or "
        "{\"repeat\": n (>= 2), \"steps\": [steps]}. Intensity is a zone, never watts."
    ),
    "items": {
        "type": "object",
        "properties": {
            **_STEP["properties"],
            "repeat": {"type": "integer", "minimum": 2},
            "steps": {"type": "array", "items": _STEP},
        },
    },
}
_DRAFT = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "Day of the session, YYYY-MM-DD, in the athlete's time zone."},
        "template_id": {"type": "string", "description": "A library session from banister_find_sessions. Use this or steps."},
        "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 600, "description": "With template_id: fit the template to this duration."},
        "steps": _STEPS,
        "workout_type": {"type": "string", "enum": ["recovery", "endurance", "intervals", "long_ride"], "description": "Required with steps."},
        "title": {"type": "string", "description": "Short name for the calendar, e.g. 'Group ride, easy on the climbs'."},
    },
    "required": ["date"],
}
_DAYS = {"type": "integer", "minimum": 7, "maximum": 90}
_GRANULARITY = {"type": "string", "enum": ["daily", "weekly"]}


def _schema(name: str, description: str, properties: dict | None = None, required=()) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
    }


TOOLS = [
    (_schema(
        "banister_context",
        "The athlete today: dated form (CTL/ATL/TSB), recovery signals, guardrail findings, recent rides, planned sessions, profile. Read this first when training is discussed.",
        {"days": {"type": "integer", "minimum": 1, "maximum": 28, "description": "Recent rides window."},
         "ahead": {"type": "integer", "minimum": 1, "maximum": 14, "description": "Planned sessions window."}},
    ), _tool(_context)),
    (_schema(
        "banister_fitness_history", "CTL/ATL/TSB history from the data source.",
        {"days": {"type": "integer", "minimum": 7, "maximum": 365}, "granularity": _GRANULARITY},
    ), _tool(_fitness)),
    (_schema("banister_training_trend", "Weekly training totals.", {"days": _DAYS}), _tool(_trend)),
    (_schema(
        "banister_wellness_history", "History of wellness metrics such as HRV, resting heart rate or sleep.",
        {"days": _DAYS, "granularity": _GRANULARITY,
         "metrics": {"type": "array", "items": {"type": "string"}, "description": "Unknown names are refused with the valid list."}},
        ["metrics"],
    ), _tool(_wellness)),
    (_schema(
        "banister_ride", "Everything known about the ride(s) of a past day.",
        {"date": {"type": "string", "description": "YYYY-MM-DD"}}, ["date"],
    ), _tool(_ride)),
    (_schema(
        "banister_memory", "Durable notes about the athlete and the dated coach journal.",
        {"keyword": {"type": "string"}, "category": {"type": "string"},
         "days": {"type": "integer", "minimum": 1, "maximum": 400}},
    ), _tool(_memory)),
    (_schema(
        "banister_add_note", "Remember something durable about the athlete's training. This journal is the single coach memory: keep training notes here.",
        {"category": {"type": "string", "enum": ["fatigue", "motivation", "physique", "event", "preference"]},
         "note": {"type": "string", "maxLength": 120}},
        ["category", "note"],
    ), _tool(_add_note)),
    (_schema(
        "banister_find_sessions", "Search the session library. Returns several candidates to compare; use detail=true to read their intent and structure.",
        {"workout_type": {"type": "string", "enum": ["recovery", "endurance", "intervals", "long_ride"]},
         "family": {"type": "string"},
         "phase": {"type": "string", "enum": ["base", "build", "peak", "taper"]},
         "query": {"type": "string", "description": "Words that must all appear in the description, in English."},
         "max_minutes": {"type": "integer", "minimum": 15, "maximum": 600},
         "detail": {"type": "boolean"}},
    ), _tool(_find)),
    (_schema(
        "banister_recommendation", "The engine's own pick from today's state. A reference to compare with, not an obligation.",
        {"workout_type": {"type": "string", "enum": ["recovery", "endurance", "intervals", "long_ride"]},
         "max_minutes": {"type": "integer", "minimum": 15, "maximum": 300},
         "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 300}},
    ), _tool(_recommendation)),
    (_schema(
        "banister_evaluate_session", "Compute a session without saving anything: duration, estimated TSS, watt or heart rate targets, warnings and guardrail findings. Use it to compare options; findings describe today's state, not a forecast.",
        _DRAFT["properties"], ["date"],
    ), _tool(_evaluate)),
    (_schema(
        "banister_propose_session", "Show the athlete an approval card for a session. Only the athlete's tap on the card adds it to the calendar: you cannot publish, and must not say it is published. card_sent=false means no card could be shown here.",
        _DRAFT["properties"], ["date"],
    ), _tool(_propose)),
    (_schema("banister_publications", "Sessions published through Banister and not withdrawn."), _tool(_publications)),
    (_schema(
        "banister_request_withdrawal", "Ask the athlete, with a card, to confirm removing a published session. Only their tap removes it.",
        {"publication_id": {"type": "string"}}, ["publication_id"],
    ), _tool(_request_withdrawal)),
]
