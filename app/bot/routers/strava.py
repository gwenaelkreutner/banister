from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.user import User

router = Router()


@router.message(Command("connect_strava"))
async def cmd_connect_strava(message: Message, user: User):
    """Envoie le lien OAuth Strava à l'utilisateur."""
    from app.strava.oauth import build_auth_url

    auth_url = build_auth_url(user.telegram_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Connecter Strava", url=auth_url)
    ]])
    await message.answer(
        "🚴 <b>Connecte ton compte Strava</b>\n\n"
        "Clique sur le bouton ci-dessous pour autoriser Banister "
        "à accéder à tes activités.\n\n"
        "Tu pourras déconnecter à tout moment avec /disconnect_strava",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(Command("disconnect_strava"))
async def cmd_disconnect_strava(message: Message, session: AsyncSession, user: User):
    """Supprime la connexion Strava de l'utilisateur."""
    conn = await repo.oauth_repo.get_connection(session, user.id, "strava")
    if conn is None:
        await message.answer("ℹ️ Aucun compte Strava connecté.")
        return

    await repo.oauth_repo.delete_connection(session, user.id, "strava")
    await session.commit()
    await message.answer("✅ Compte Strava déconnecté.")


@router.message(Command("strava_status"))
async def cmd_strava_status(message: Message, session: AsyncSession, user: User):
    """Affiche le statut de la connexion Strava."""
    conn = await repo.oauth_repo.get_connection(session, user.id, "strava")
    if conn is None:
        await message.answer(
            "❌ Strava non connecté.\n\nUtilise /connect_strava pour lier ton compte.",
        )
        return

    await message.answer(
        f"✅ <b>Strava connecté</b>\n"
        f"ID athlète : <code>{conn.provider_user_id}</code>\n"
        f"Token valide jusqu'au : {conn.token_expires_at.strftime('%d/%m/%Y %H:%M')} UTC",
        parse_mode="HTML",
    )
