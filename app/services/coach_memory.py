"""Coach memory: short notes kept in the athlete profile, and the dated journal behind them.

Shared by the built-in chat tools (`app/llm/chat.py`) and the agent API (`app/api/agent.py`),
so both write the same two places and read through the same limits.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.db.models.user import User


async def update_coach_memory(args: dict, user: User, session: AsyncSession) -> dict:
    """Mémorise une observation durable dans coach_memory ou athlete_notes.

    add_note écrit à DEUX endroits en une seule opération (Enduragent parity review,
    2026-09-20, décision owner actée) : coach_memory (JSON capé à 15, court/moyen terme
    toujours injecté dans le system prompt — inchangé) ET coach_journal_entries
    (append-only, source="llm") — une note évincée au-delà de 15 reste queryable
    indéfiniment via l'outil memory_query au lieu d'être perdue. Le LLM ne décide
    toujours que du texte/catégorie, jamais de la mécanique de double écriture
    (Principe III)."""
    from datetime import date as _date

    from app.db.repositories import journal_repo, profile_repo

    action = args.get("action")
    profile_orm = await profile_repo.get_by_user_id(session, user.id)
    if profile_orm is None:
        return {"ok": False, "error": t("llm.tools.profile_not_found")}

    if action == "add_note":
        category = args.get("category", "preference")
        note_text = (args.get("note") or "")[:120]
        memory: list = list(profile_orm.coach_memory or [])

        if len(memory) >= 15:
            same_cat = [i for i, n in enumerate(memory) if n.get("category") == category]
            if same_cat:
                idx = min(same_cat, key=lambda i: memory[i].get("date", ""))
            else:
                idx = min(range(len(memory)), key=lambda i: memory[i].get("date", ""))
            memory[idx] = {"date": str(_date.today()), "category": category, "note": note_text}
        else:
            memory.append({"date": str(_date.today()), "category": category, "note": note_text})

        await profile_repo.update_coach_memory(session, profile_orm, memory)
        await journal_repo.create(
            session,
            user_id=user.id,
            entry_date=_date.today(),
            category=category,
            source="llm",
            text=note_text,
        )
        return {"ok": True, "action": "add_note", "category": category}

    elif action == "update_athlete_notes":
        key = args.get("key", "")
        value = args.get("value", "")
        if not key:
            return {"ok": False, "error": t("llm.tools.missing_key")}
        notes: dict = dict(profile_orm.athlete_notes or {})
        notes[key] = value
        await profile_repo.update_athlete_notes(session, profile_orm, notes)
        return {"ok": True, "action": "update_athlete_notes", "key": key}

    return {"ok": False, "error": t("llm.tools.unknown_action", action=action)}


# ── Journal daté / memory_query (Enduragent parity review, 2026-09-20) ──────────

_MEMORY_QUERY_MAX_RANGE_DAYS = 400
_MEMORY_QUERY_LIMIT = 30
_MEMORY_QUERY_MAX_RESULT_CHARS = 3000


async def memory_query(args: dict, user: User, session: AsyncSession) -> dict:
    """Recherche dans le journal daté (app/db/models/coach_journal.py) — l'équivalent
    banister du memory_query d'Enduragent (packages/engine/src/sport/memory-tools.ts,
    vérifié sur le vrai source). Plafonds validés par l'owner : 400 jours de plage max,
    30 entrées / 3000 caractères de résultat avec `truncated: true` au-delà.

    Fencing DIFFÉRÉ par décision owner (2026-09-20) : seul sanitize_untrusted_text() est
    appliqué ici (niveau standard minimal, cohérent avec les autres outils du projet qui
    ne wrappent pas leurs résultats). TODO : réévaluer si un fencing complet
    (wrap_untrusted_block, comme build_system_prompt() l'applique à coach_memory) est
    nécessaire pour ce canal — une partie des entrées est écrite par le LLM lui-même
    (voie add_note) donc potentiellement adversariale, mais le canal `role="tool"` de la
    boucle agentique est structurellement distinct du system prompt.
    """
    from app.db.repositories import journal_repo
    from app.llm.prompt_fence import sanitize_untrusted_text

    try:
        start = date.fromisoformat(args.get("from_date", ""))
        end = date.fromisoformat(args.get("to_date", ""))
    except (TypeError, ValueError):
        return {"error": t("llm.tools.date_range_format_error")}

    if start > end:
        return {"error": t("llm.tools.date_range_reversed", start=start, end=end)}

    range_days = (end - start).days + 1
    if range_days > _MEMORY_QUERY_MAX_RANGE_DAYS:
        return {
            "error": t(
                "llm.tools.date_range_too_wide",
                days=range_days,
                max_days=_MEMORY_QUERY_MAX_RANGE_DAYS,
            ),
        }

    keyword = (args.get("keyword") or "").strip() or None
    category = args.get("category") or None

    rows = await journal_repo.query(
        session,
        user.id,
        start,
        end,
        keyword=keyword,
        category=category,
        limit=_MEMORY_QUERY_LIMIT,
    )

    truncated = len(rows) >= _MEMORY_QUERY_LIMIT
    entries: list[dict] = []
    total_chars = 0
    for row in rows:
        text = sanitize_untrusted_text(row.text)
        if total_chars + len(text) > _MEMORY_QUERY_MAX_RESULT_CHARS:
            truncated = True
            break
        total_chars += len(text)
        entries.append({"date": str(row.entry_date), "category": row.category, "text": text})

    return {
        "range": {"from": str(start), "to": str(end)},
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }
