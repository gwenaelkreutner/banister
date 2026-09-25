"""Installation-wide gettext catalogs for athlete-facing text."""

import gettext
from functools import lru_cache
from pathlib import Path

from app.config import settings

Language = str
LOCALES_DIR = Path(__file__).resolve().parent.parent.parent / "locales"


@lru_cache(maxsize=None)
def _catalog(language: str) -> gettext.GNUTranslations:
    return gettext.translation(
        "banister", localedir=LOCALES_DIR, languages=[language], fallback=False
    )


def t(key: str, *, language: Language | None = None, **values: object) -> str:
    """Read a required catalog key, then interpolate data after translation."""
    selected = language or settings.app_language
    translated = _catalog(selected).gettext(key)
    if translated == key:
        raise KeyError(f"Missing translation {key!r} for language {selected!r}")
    return translated.format(**values)


def tp(key: str, count: int, *, language: Language | None = None, **values: object) -> str:
    """Read a pluralized catalog entry using the selected language's plural rules."""
    selected = language or settings.app_language
    translated = _catalog(selected).ngettext(key, f"{key}.plural", count)
    if translated in {key, f"{key}.plural"}:
        raise KeyError(f"Missing plural translation {key!r} for language {selected!r}")
    return translated.format(count=count, **values)


def with_language_rule(system_prompt: str, *, language: Language | None = None) -> str:
    """Require the selected output language for every LLM generation path."""
    rule = t("llm.output_language_rule", language=language)
    return f"{system_prompt}\n\nOUTPUT LANGUAGE: {rule}"
