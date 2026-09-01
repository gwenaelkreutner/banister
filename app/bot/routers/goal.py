"""Router : /goal — spec 007.

Changer d'objectif sans effacer l'athlète : relit la source en silence,
demande objectif + date, régénère le plan depuis la forme actuelle, signale un
calendrier périmé (US3).

Enregistré avant chat_router dans app/bot/setup.py.
"""
from __future__ import annotations

from aiogram import Router

router = Router(name="goal")
