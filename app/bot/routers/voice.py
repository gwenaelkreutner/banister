"""Router : /voice — spec 007.

Choisir la voix du coach : liste les personas/*.yaml, écrit users.coach_voice,
effet au message suivant (US5).

Enregistré avant chat_router dans app/bot/setup.py.
"""
from __future__ import annotations

from aiogram import Router

router = Router(name="voice")
