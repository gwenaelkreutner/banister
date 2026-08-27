from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.exceptions import PersonaNotFoundError

PERSONAS_DIR = Path(__file__).resolve().parent.parent.parent / "personas"


@dataclass(frozen=True)
class Persona:
    """A coach's identity, language, and conversational style, loaded from personas/*.yaml."""

    id: str
    name: str
    language: str
    voice: str
    system_prompt: str
    ux_prompt: str

    def format_system_prompt(self, first_name: str, tsb_label: str = "", event_name: str = "") -> str:
        return self.system_prompt.format(
            first_name=first_name, tsb_label=tsb_label, event_name=event_name
        )

    def format_ux_prompt(self, first_name: str, tsb_label: str = "", event_name: str = "") -> str:
        return self.ux_prompt.format(
            first_name=first_name, tsb_label=tsb_label, event_name=event_name
        )


@lru_cache(maxsize=None)
def load_persona(persona_id: str) -> Persona:
    """Load a persona YAML file from personas/. Cached — restart the app to pick up edits."""
    path = PERSONAS_DIR / f"{persona_id}.yaml"
    if not path.is_file():
        raise PersonaNotFoundError(f"Persona '{persona_id}' not found at {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    for field in ("system_prompt", "ux_prompt"):
        if not data.get(field):
            raise PersonaNotFoundError(f"Persona '{persona_id}' is missing required field '{field}'")

    return Persona(
        id=persona_id,
        name=data.get("name", persona_id),
        language=data.get("language", "en"),
        voice=data.get("voice", ""),
        system_prompt=data["system_prompt"],
        ux_prompt=data["ux_prompt"],
    )
