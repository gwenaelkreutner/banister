import uuid
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.oauth_connection import OAuthConnection


async def get_connection(
    session: AsyncSession,
    user_id: uuid.UUID,
    provider: str,
) -> OAuthConnection | None:
    result = await session.execute(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def upsert_connection(
    session: AsyncSession,
    user_id: uuid.UUID,
    provider: str,
    tokens: dict,
    provider_user_id: str,
) -> OAuthConnection:
    """
    Crée ou met à jour la connexion OAuth d'un utilisateur pour un provider donné.
    `tokens` doit contenir : access_token, refresh_token, expires_at (Unix timestamp int).
    """
    token_expires_at = datetime.fromtimestamp(tokens["expires_at"], tz=timezone.utc)

    stmt = (
        insert(OAuthConnection)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            provider=provider,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_expires_at=token_expires_at,
            provider_user_id=provider_user_id,
        )
        .on_conflict_do_update(
            constraint="uq_oauth_user_provider",
            set_={
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "token_expires_at": token_expires_at,
                "provider_user_id": provider_user_id,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        .returning(OAuthConnection)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def delete_connection(
    session: AsyncSession,
    user_id: uuid.UUID,
    provider: str,
) -> None:
    await session.execute(
        delete(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == provider,
        )
    )
    await session.flush()


async def update_tokens(
    session: AsyncSession,
    conn: OAuthConnection,
    tokens: dict,
) -> None:
    """Met à jour les tokens après un refresh."""
    conn.access_token = tokens["access_token"]
    conn.refresh_token = tokens["refresh_token"]
    conn.token_expires_at = datetime.fromtimestamp(tokens["expires_at"], tz=timezone.utc)
    conn.updated_at = datetime.now(timezone.utc)
    await session.flush()
