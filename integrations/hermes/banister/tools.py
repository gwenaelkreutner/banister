"""Tools offered to the agent (toolset `banister`). No training logic here: Banister computes.

Anything that changes the calendar or the plan is deliberately NOT a tool: the model can only
show a card, whose buttons are handled in `messaging.py`, on the athlete's tap.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from collections import deque

from .client import BanisterError, call

send_card = None  # set by register(): sends a card from Banister, buttons included

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


def _with_card(result: dict) -> dict:
    """Show the card and keep it out of what the model reads."""
    card = result.get("card")
    sent = bool(card and send_card and send_card(card))
    return {**{k: v for k, v in result.items() if k not in ("card", "steps")}, "card_sent": sent}


def _propose(a):
    return _with_card(_remember("session", call("POST", "/proposals", _draft(a))))


def _publications(_a):
    return call("GET", "/publications")


def _request_withdrawal(a):
    publication_id = urllib.parse.quote(str(a["publication_id"]).strip(), safe="")
    return _with_card(call("GET", f"/publications/{publication_id}/withdrawal-card"))


def _plan(a):
    return _remember("plan", call("GET", "/plan", params={"week": a.get("week")}))


def _propose_plan(a):
    body = {"goal": a["goal"], "target_date": a.get("target_date")}
    return _with_card(_remember("plan_proposal", call("POST", "/plan/proposals/new", body)))


def _propose_leaving_plan(_a):
    return _with_card(call("POST", "/plan/proposals/leave"))


def _propose_week_change(a):
    keys = ("reason", "modification_type", "week_offset")
    body = {k: a[k] for k in keys}
    return _with_card(call("POST", "/plan/proposals/week", body))


def _propose_session_change(a):
    keys = ("constraint_type", "day_offset", "action")
    body = {k: a[k] for k in keys}
    return _with_card(call("POST", "/plan/proposals/session", body))


def _request_plan_publication(_a):
    return _with_card(call("POST", "/plan/calendar/publish-request"))


def _request_plan_withdrawal(_a):
    return _with_card(call("POST", "/plan/calendar/withdraw-request"))


def _recap(_a):
    return _remember("recap", call("GET", "/recap"))


def _power_profile(_a):
    return call("GET", "/power-profile")


def _injury(a):
    keys = ("location", "severity", "estimated_recovery_days")
    return call("PUT", "/injury", {k: a[k] for k in keys})


def _reminders(a):
    keys = ("enabled", "hour", "minute")
    body = {k: a[k] for k in keys if a.get(k) is not None}
    return call("PUT", "/reminders", body) if body else call("GET", "/reminders")


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
    (_schema(
        "banister_week_recap", "This week's figures: load, sessions, trend, intensity distribution.",
    ), _tool(_recap)),
    (_schema(
        "banister_power_profile", "How the power curve moved over recent weeks, and what the athlete can sustain. Slower: several calls to the data source.",
    ), _tool(_power_profile)),
    (_schema(
        "banister_report_injury", "Record an injury the athlete reports. Banister restricts zones accordingly.",
        {"location": {"type": "string", "enum": ["knee", "back", "shoulder", "hip", "ankle", "other"]},
         "severity": {"type": "string", "enum": ["mild", "moderate", "severe"]},
         "estimated_recovery_days": {"type": "integer", "minimum": 1, "maximum": 90}},
        ["location", "severity", "estimated_recovery_days"],
    ), _tool(_injury)),
    (_schema(
        "banister_reminders", "Read the morning session reminder settings, or change them. They apply when a plan is active.",
        {"enabled": {"type": "boolean"},
         "hour": {"type": "integer", "minimum": 0, "maximum": 23},
         "minute": {"type": "integer", "minimum": 0, "maximum": 59}},
    ), _tool(_reminders)),
    (_schema(
        "banister_plan", "The active training plan: every week's outline, and one week in detail with its sessions by date. mode=freestyle means no plan.",
        {"week": {"type": "integer", "minimum": 1, "description": "Week to detail. Default: the current one."}},
    ), _tool(_plan)),
    (_schema(
        "banister_propose_plan", "Compute a new plan for a goal and show the athlete a card to start it. It replaces the active plan if any. Only their tap starts it.",
        {"goal": {"type": "string", "enum": ["event", "fitness", "performance", "other"]},
         "target_date": {"type": "string", "description": "YYYY-MM-DD, for an event."}},
        ["goal"],
    ), _tool(_propose_plan)),
    (_schema(
        "banister_propose_leaving_plan", "Show the athlete a card to leave the plan and switch to freestyle coaching.",
    ), _tool(_propose_leaving_plan)),
    (_schema(
        "banister_propose_week_change", "Compute a lighter version of a plan week and show the athlete a card to apply it.",
        {"reason": {"type": "string", "enum": ["fatigue", "injury", "event", "preference", "illness"]},
         "modification_type": {"type": "string", "enum": ["reduce_intensity", "reduce_volume", "skip_session", "swap_to_recovery"]},
         "week_offset": {"type": "integer", "minimum": 0, "maximum": 2, "description": "0 = this week."}},
        ["reason", "modification_type", "week_offset"],
    ), _tool(_propose_week_change)),
    (_schema(
        "banister_propose_session_change", "Adjust one planned session of the coming days and show the athlete a card to apply it.",
        {"constraint_type": {"type": "string", "enum": ["meeting", "weather_bad", "tired_today", "personal"]},
         "day_offset": {"type": "integer", "minimum": 0, "maximum": 6, "description": "0 = today."},
         "action": {"type": "string", "enum": ["skip", "shift", "reduce_50", "indoor"]}},
        ["constraint_type", "day_offset", "action"],
    ), _tool(_propose_session_change)),
    (_schema(
        "banister_request_plan_publication", "Show the athlete a card to add the plan's coming sessions to their calendar.",
    ), _tool(_request_plan_publication)),
    (_schema(
        "banister_request_plan_withdrawal", "Show the athlete a card to remove the plan's published sessions from their calendar.",
    ), _tool(_request_plan_withdrawal)),
    (_schema("banister_publications", "Sessions published through Banister and not withdrawn."), _tool(_publications)),
    (_schema(
        "banister_request_withdrawal", "Ask the athlete, with a card, to confirm removing a published session. Only their tap removes it.",
        {"publication_id": {"type": "string"}}, ["publication_id"],
    ), _tool(_request_withdrawal)),
]
