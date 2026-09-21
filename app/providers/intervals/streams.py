"""Normalise la réponse brute `client.get_activity_streams()` — une liste de
`{"type": ..., "data": [...]}` (forme vérifiée contre l'API réelle intervals.icu,
2026-09-21) — vers le dict `{type: data}` que ses consommateurs veulent réellement
(`app/engine/dfa.py`)."""
from __future__ import annotations


def streams_to_dict(raw: list[dict]) -> dict[str, list]:
    return {s["type"]: s["data"] for s in raw if "type" in s and "data" in s}
