import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_message import ChatMessage


@dataclass(frozen=True)
class DailyTokenUsage:
    day: date
    tokens_input: int
    tokens_output: int
    turns: int  # nombre de réponses de l'assistant ce jour-là, pas de messages (user+assistant)


async def create_message(
    session: AsyncSession,
    user_id: uuid.UUID,
    role: str,
    content: str,
    intent: str | None = None,
    tool_used: str | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        id=uuid.uuid4(),
        user_id=user_id,
        role=role,
        content=content,
        intent=intent,
        tool_used=tool_used,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
    )
    session.add(msg)
    await session.flush()
    return msg


async def get_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 15,
) -> list[ChatMessage]:
    """Retourne les N derniers messages dans l'ordre chronologique."""
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    return list(reversed(messages))  # ordre chronologique


async def clear_conversation(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Efface tout l'historique de conversation d'un utilisateur."""
    await session.execute(
        delete(ChatMessage).where(ChatMessage.user_id == user_id)
    )
    await session.flush()


async def token_usage_by_day(
    session: AsyncSession, user_id: uuid.UUID, start: date, end: date
) -> list[DailyTokenUsage]:
    """Un `DailyTokenUsage` par jour ayant au moins une réponse de l'assistant dans
    [start, end] — un jour sans tour de chat est absent, jamais présent à 0 (même
    convention que `meal_entry_repo.daily_totals`). Ne compte que `role="assistant"` :
    c'est la seule ligne qui porte des tokens (`tokens_input`/`tokens_output` restent
    NULL sur les messages "user" et sur tout ce qui a été créé avant cette colonne,
    donc `func.sum`/`func.count` les ignorent naturellement — pas de filtre explicite
    nécessaire au-delà de `role`)."""
    day_col = func.date(ChatMessage.created_at)
    result = await session.execute(
        select(
            day_col,
            func.sum(ChatMessage.tokens_input),
            func.sum(ChatMessage.tokens_output),
            func.count(ChatMessage.id),
        )
        .where(
            ChatMessage.user_id == user_id,
            ChatMessage.role == "assistant",
            ChatMessage.tokens_input.is_not(None),
            day_col >= start.isoformat(),
            day_col <= end.isoformat(),
        )
        .group_by(day_col)
        .order_by(day_col)
    )
    return [
        DailyTokenUsage(
            day=date.fromisoformat(d) if isinstance(d, str) else d,
            tokens_input=int(t_in or 0),
            tokens_output=int(t_out or 0),
            turns=int(count),
        )
        for d, t_in, t_out, count in result.all()
    ]
