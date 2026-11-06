"""Messages for the external agent to deliver (`COACH_INTERFACE=agent`).

`OutboxMessenger` has the part of aiogram's `Bot` that the notifier and the schedulers use, so
they write one message whichever interface is active: wording and buttons stay in Banister, and
the agent only delivers them. Written in its own transaction: callers commit their work first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.db.models.publication import OutboxMessage

# Past this, a message no longer means what it did: a morning reminder, a prompt for a feeling.
MAX_AGE = timedelta(days=3)


def _buttons(reply_markup) -> list | None:
    rows = getattr(reply_markup, "inline_keyboard", None)
    if not rows:
        return None
    return [[{"label": b.text, "action": b.callback_data} for b in row] for row in rows]


class OutboxMessenger:
    async def send_chat_action(self, *_args, **_kwargs) -> None:
        """No typing indicator through the outbox."""

    async def send_message(
        self,
        _chat_id,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup=None,
        disable_notification: bool = False,
        agent_prompt: str | None = None,
    ) -> None:
        from app.db.client import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            await enqueue(
                session,
                text=text,
                parse_mode=parse_mode,
                buttons=_buttons(reply_markup),
                silent=disable_notification,
                agent_prompt=agent_prompt,
            )
            await session.commit()


async def enqueue(
    session: AsyncSession,
    *,
    text: str,
    parse_mode: str | None = None,
    buttons: list | None = None,
    silent: bool = False,
    agent_prompt: str | None = None,
) -> OutboxMessage:
    message = OutboxMessage(
        text=text,
        parse_mode=parse_mode,
        buttons=buttons,
        silent=silent,
        agent_prompt=agent_prompt,
        status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(message)
    await session.flush()
    return message


async def pending_messages(session: AsyncSession, *, limit: int = 20) -> list[dict]:
    """Oldest first, so a staged notification arrives in order."""
    await session.execute(
        update(OutboxMessage)
        .where(
            OutboxMessage.status == "pending",
            OutboxMessage.created_at < datetime.now(UTC) - MAX_AGE,
        )
        .values(status="expired")
    )
    await session.commit()
    rows = await session.execute(
        select(OutboxMessage)
        .where(OutboxMessage.status == "pending")
        .order_by(OutboxMessage.id)
        .limit(limit)
    )
    return [
        {
            "id": m.id,
            "text": m.text,
            "parse_mode": m.parse_mode,
            "silent": m.silent,
            "buttons": m.buttons,
            "agent_prompt": m.agent_prompt,
            "handover_hint": t("agent.handover_hint") if m.agent_prompt else None,
        }
        for m in rows.scalars().all()
    ]


async def mark_delivered(session: AsyncSession, message_id: int) -> bool:
    result = await session.execute(
        update(OutboxMessage)
        .where(OutboxMessage.id == message_id, OutboxMessage.status == "pending")
        .values(status="delivered", delivered_at=datetime.now(UTC))
    )
    await session.commit()
    return result.rowcount == 1
