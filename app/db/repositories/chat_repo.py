import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_message import ChatMessage


async def create_message(
    session: AsyncSession,
    user_id: uuid.UUID,
    role: str,
    content: str,
    intent: str | None = None,
    tool_used: str | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        id=uuid.uuid4(),
        user_id=user_id,
        role=role,
        content=content,
        intent=intent,
        tool_used=tool_used,
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
