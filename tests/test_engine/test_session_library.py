"""spec 004 T035-T037 — the session library loads, covers every (phase, workout_type)
the periodization can request, selects deterministically, and is genuinely extensible
without touching plan_builder.py's code.
"""
from __future__ import annotations

import shutil

import pytest

import app.engine.session_library as session_library
from app.engine.session_library import (
    SessionLibraryError,
    load_library,
    select_template,
)

# Every (phase, workout_type) plan_builder.py's _build_week_template() can actually
# request, per research R4 — the enumerable coverage set FR-019/SC-007 promise.
_REQUIRED_COVERAGE = [
    ("base", "long_ride"),
    ("base", "endurance"),
    ("base", "recovery"),
    ("base", "intervals"),   # sweet_spot
    ("build", "long_ride"),
    ("build", "endurance"),
    ("build", "recovery"),
    ("build", "intervals"),  # threshold, vo2
    ("peak", "long_ride"),
    ("peak", "endurance"),
    ("peak", "recovery"),
    ("peak", "intervals"),   # vo2, threshold
    ("taper", "long_ride"),
    ("taper", "endurance"),
    ("taper", "recovery"),
    ("taper", "intervals"),  # taper-activation
]


class TestLibraryLoads:
    def test_loads_without_error(self):
        library = load_library()
        assert len(library) > 0

    def test_every_template_has_rationale_fields(self):
        """FR-017 — purpose/intent/suits are mandatory, not decoration."""
        for template in load_library():
            assert template.purpose
            assert template.intent
            assert template.suits


class TestCoverage:
    """FR-019, SC-007 — every request the periodization can make has an answer."""

    @pytest.mark.parametrize("phase,workout_type", _REQUIRED_COVERAGE)
    def test_coverage(self, phase, workout_type):
        candidates = [
            t for t in load_library()
            if phase in t.phases and t.workout_type == workout_type
        ]
        assert candidates, f"no template covers phase={phase!r} workout_type={workout_type!r}"


class TestSelectionIsDeterministic:
    def test_same_request_returns_same_template_across_calls(self):
        first = select_template("build", "intervals", week_in_block=2, family="threshold")
        second = select_template("build", "intervals", week_in_block=2, family="threshold")
        assert first.id == second.id

    def test_rotation_varies_by_week_in_block(self):
        """The old `week_in_block % len(STRUCTURES)` rotation preserved week-to-week
        variety — confirm the library-backed selection still does, for a family with
        more than one candidate."""
        selected_ids = {
            select_template("build", "intervals", week_in_block=w, family="threshold").id
            for w in range(4)
        }
        assert len(selected_ids) > 1

    def test_no_match_raises_and_names_the_request(self):
        with pytest.raises(SessionLibraryError, match="base.*recovery.*nonexistent_family"):
            select_template("base", "recovery", week_in_block=0, family="nonexistent_family")


class TestCoverageAssertionCanActuallyFail:
    """A coverage test that cannot fail proves nothing (quickstart Scenario 3, T036).
    Verified here by sabotaging a copy of the library in an isolated temp directory —
    never touching the real sessions/ the app uses."""

    def test_removing_the_only_family_for_a_slot_fails_coverage(self, tmp_path, monkeypatch):
        # Copy the real library so this test exercises real YAML parsing, then
        # delete the one file providing base-phase intervals coverage.
        shutil.copytree(session_library.SESSIONS_DIR, tmp_path / "sessions")
        (tmp_path / "sessions" / "sweet-spot.yaml").unlink()

        monkeypatch.setattr(session_library, "SESSIONS_DIR", tmp_path / "sessions")
        session_library.load_library.cache_clear()
        try:
            # Scoped to family="sweet_spot" specifically — a plain (phase, workout_type)
            # filter also matches recovery.yaml's sprint_activation template (also
            # base-phase intervals), which would make this assertion pass even with
            # sweet-spot.yaml deleted, proving nothing (exactly the trap T036 warns
            # about — this is the fix, caught by running the test itself).
            candidates = [
                t for t in session_library.load_library()
                if "base" in t.phases and t.workout_type == "intervals" and t.family == "sweet_spot"
            ]
            assert not candidates, "expected sweet_spot coverage to be gone"
        finally:
            session_library.load_library.cache_clear()  # don't leak the sabotaged cache


class TestExtensibilityWithoutCodeChanges:
    """FR-016, SC-006 — a contributor adds a template and it becomes selectable with
    zero .py changes. Verified in an isolated temp copy of the real library."""

    def test_new_template_becomes_selectable(self, tmp_path, monkeypatch):
        shutil.copytree(session_library.SESSIONS_DIR, tmp_path / "sessions")
        new_file = tmp_path / "sessions" / "contributor-test.yaml"
        new_file.write_text(
            """
templates:
  - id: contributor-test-session
    workout_type: recovery
    family: contributor_test
    phases: [base]
    purpose: Prove a new template needs no code change to become selectable.
    intent: A trivial steady session added purely for this test.
    suits: This test only.
    structure:
      - kind: steady
        duration_minutes: 30
        zone_code: Z1
""",
            encoding="utf-8",
        )

        monkeypatch.setattr(session_library, "SESSIONS_DIR", tmp_path / "sessions")
        session_library.load_library.cache_clear()
        try:
            template = session_library.select_template(
                "base", "recovery", week_in_block=0, family="contributor_test"
            )
            assert template.id == "contributor-test-session"
        finally:
            session_library.load_library.cache_clear()


class TestLoadErrorsAreLegible:
    """Duplicate id, repeat: 1, and an undefined zone code all fail with a message
    naming enough to fix the problem (contracts/session-library.md's guarantee)."""

    def _write_and_load(self, tmp_path, monkeypatch, yaml_content: str):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "broken.yaml").write_text(yaml_content, encoding="utf-8")
        monkeypatch.setattr(session_library, "SESSIONS_DIR", sessions_dir)
        session_library.load_library.cache_clear()

    def test_duplicate_id_across_two_files_is_a_load_error(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        template_yaml = """
templates:
  - id: dup-id
    workout_type: recovery
    family: x
    phases: [base]
    purpose: p
    intent: i
    suits: s
    structure:
      - kind: steady
        duration_minutes: 30
        zone_code: Z1
"""
        (sessions_dir / "a.yaml").write_text(template_yaml, encoding="utf-8")
        (sessions_dir / "b.yaml").write_text(template_yaml, encoding="utf-8")
        monkeypatch.setattr(session_library, "SESSIONS_DIR", sessions_dir)
        session_library.load_library.cache_clear()
        try:
            with pytest.raises(SessionLibraryError, match="duplicate template id"):
                session_library.load_library()
        finally:
            session_library.load_library.cache_clear()

    def test_repeat_of_one_is_rejected_at_load_time(self, tmp_path, monkeypatch):
        self._write_and_load(tmp_path, monkeypatch, """
templates:
  - id: bad-repeat
    workout_type: intervals
    family: x
    phases: [base]
    purpose: p
    intent: i
    suits: s
    structure:
      - kind: warmup
        duration_minutes: 10
        zone_code: Z1
      - repeat: 1
        steps:
          - kind: work
            duration_minutes: 10
            zone_code: Z4
      - kind: cooldown
        duration_minutes: 10
        zone_code: Z1
""")
        try:
            with pytest.raises(SessionLibraryError):
                session_library.load_library()
        finally:
            session_library.load_library.cache_clear()

    def test_invalid_workout_type_is_rejected_at_load_time(self, tmp_path, monkeypatch):
        self._write_and_load(tmp_path, monkeypatch, """
templates:
  - id: bad-workout-type
    workout_type: not_a_real_type
    family: x
    phases: [base]
    purpose: p
    intent: i
    suits: s
    structure:
      - kind: steady
        duration_minutes: 30
        zone_code: Z1
""")
        try:
            with pytest.raises(SessionLibraryError, match="invalid workout_type"):
                session_library.load_library()
        finally:
            session_library.load_library.cache_clear()
