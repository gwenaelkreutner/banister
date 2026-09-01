"""Router : /reset — spec 007.

Recommencer de zéro — action distincte de /goal : liste ce qui sera supprimé,
confirmation tapée, suppressions locales uniquement, ne touche jamais
intervals.icu (US4).

Enregistré avant chat_router dans app/bot/setup.py.
"""
from __future__ import annotations

from aiogram import Router

router = Router(name="reset")
