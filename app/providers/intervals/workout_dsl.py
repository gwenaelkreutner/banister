"""Render a structured session (spec 004 Step/RepeatGroup) as the intervals.icu
workout DSL text (spec 005 research R3, contracts/calendar-publication.md §1).

This is a pure, synchronous text transformation with no I/O — deliberately split from
calendar.py (all I/O and ordering) so the format, the part most likely to need
adjustment as real sessions hit real devices, is testable without touching the network
and R3's probe output can be used directly as a fixture.

intensity is always `%ftp`: Steps store only zone codes (spec 004 — nothing absolute is
ever persisted), and the percentage bounds come from the plan's own `zones`, so a
threshold change retargets published sessions the same way it retargets displayed ones.
"""
from __future__ import annotations

import hashlib
from datetime import date

from app.engine.schemas import RepeatGroup, Step, Zone

_WARMUP = "Warmup"
_MAIN = "Main set"
_COOLDOWN = "Cooldown"


def hash_session_content(session_date: date, name: str, rendered_dsl: str) -> str:
    """SHA-256( session_date | name | rendered_DSL_text ) — data-model.md §Content hashing.

    The canonical content fingerprint, shared by the approval binding (FR-004) and the
    republish diff (FR-017, FR-020). Lives here, in the pure format module, so provider
    I/O (calendar.py) and orchestration (services/publication.py) hash identically.
    Deliberately excludes the server-assigned event id and the load/duration intervals.icu
    derives from the DSL itself (R4)."""
    payload = f"{session_date.isoformat()}|{name}|{rendered_dsl}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EmptySessionError(ValueError):
    """A session with no steps cannot be rendered — it is refused and reported (FR-009),
    never emitted as an empty or invented workout."""


def _intensity(zone_code: str, zones: dict[str, Zone]) -> str:
    """`{lower}-{upper}%` from Zone.lower_pct/upper_pct (fractions, ×100), or a single
    `{value}%` when the bounds coincide — matching R3's verified server parse.

    Z1's lower bound is 0.00 (app/engine/zones.py). Emitting `- 15m 0-55%` for a
    recovery step is technically accepted by intervals.icu but reads as a nonsensical
    "0% FTP" target on the athlete's device (found in the T054 live run). When the lower
    bound rounds to 0, render the ceiling alone (`- 15m 55%`) — an honest "ride at or
    below this", with no invented floor.
    """
    try:
        zone = zones[zone_code]
    except KeyError as exc:
        raise ValueError(
            f"Step references zone {zone_code!r} absent from the plan's zones "
            f"({sorted(zones)}) — cannot resolve its %ftp bounds"
        ) from exc
    lower = round(zone.lower_pct * 100)
    upper = round(zone.upper_pct * 100)
    if lower <= 0 or lower == upper:
        return f"{upper}%"
    return f"{lower}-{upper}%"


def _step_line(step: Step, zones: dict[str, Zone]) -> str:
    return f"- {step.duration_minutes}m {_intensity(step.zone_code, zones)}"


def render_dsl(steps: list[Step | RepeatGroup], zones: dict[str, Zone]) -> str:
    """Step/RepeatGroup -> workout DSL text (contracts §1).

    - warmup steps go under a `Warmup` header, cooldown under `Cooldown`,
      work/recovery under `Main set`; a `steady` step is a single headerless line
      (an unstructured ride).
    - a `RepeatGroup(repeat=N)` renders `Nx` on its own line then its full inner unit,
      *including* the recovery after the final repetition — intervals.icu computes the
      same total duration for this shape as spec 004's derive_duration_minutes (R4).
    - raises EmptySessionError for a session with no steps (FR-009).
    """
    if not steps:
        raise EmptySessionError(
            "render_dsl called on a session with no steps — refuse and report (FR-009), "
            "never publish an empty or fabricated workout"
        )

    blocks: list[list[str]] = []
    current: list[str] | None = None
    current_header: str | None = None

    def section(header: str) -> None:
        nonlocal current, current_header
        if current_header != header:
            current = [header]
            current_header = header
            blocks.append(current)

    for item in steps:
        if isinstance(item, RepeatGroup):
            section(_MAIN)
            assert current is not None
            current.append(f"{item.repeat}x")
            for inner in item.steps:
                current.append(_step_line(inner, zones))
            continue

        if item.kind == "warmup":
            section(_WARMUP)
        elif item.kind == "cooldown":
            section(_COOLDOWN)
        elif item.kind == "steady":
            # No header — an unstructured ride is a bare line.
            current = None
            current_header = None
            blocks.append([_step_line(item, zones)])
            continue
        else:  # work | recovery
            section(_MAIN)
        assert current is not None
        current.append(_step_line(item, zones))

    return "\n\n".join("\n".join(block) for block in blocks)
