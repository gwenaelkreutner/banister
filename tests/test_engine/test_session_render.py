"""spec 004 T047-T050 — descriptions are derived from a session's actual steps
(FR-028), follow a language parameter (FR-029, to the extent this feature owns
it), and no hardcoded French sentence construction remains in plan_builder.py
(FR-030, SC-010).
"""
from __future__ import annotations

import inspect

from app.engine.plan_builder import generate_plan
from app.engine.schemas import RepeatGroup, SessionSpec, Step
from app.engine.session_render import render_description, render_session_description


def _threshold_steps():
    return [
        Step(kind="warmup", duration_minutes=15, zone_code="Z1"),
        RepeatGroup(repeat=3, steps=[
            Step(kind="work", duration_minutes=12, zone_code="Z4"),
            Step(kind="recovery", duration_minutes=4, zone_code="Z1"),
        ]),
        Step(kind="cooldown", duration_minutes=15, zone_code="Z1"),
    ]


class TestDescriptionDerivedFromStructure:
    def test_interval_shape_comes_from_the_real_repeat_group_not_a_label(self):
        """The old code trusted a hand-typed `detail` string ('3x12min') to match
        the actual steps — never checked. This asserts the description reflects
        the steps directly: change the steps, the description changes with them,
        with no separate label to keep in sync."""
        desc = render_description("intervals", _threshold_steps(), coaching_mode="power")
        assert "3×12min" in desc
        assert "Z4" in desc

    def test_steady_session_shows_its_zone(self):
        steps = [Step(kind="steady", duration_minutes=90, zone_code="Z2")]
        desc = render_description("endurance", steps, coaching_mode="power")
        assert "Z2" in desc

    def test_recovery_never_shows_detail_matching_old_behaviour(self):
        steps = [Step(kind="steady", duration_minutes=45, zone_code="Z1")]
        desc = render_description("recovery", steps, detail="should not appear")
        assert "should not appear" not in desc

    def test_non_recovery_detail_is_appended(self):
        steps = [Step(kind="steady", duration_minutes=60, zone_code="Z2")]
        desc = render_description("endurance", steps, detail="cadence haute (95-100 rpm)")
        assert "cadence haute" in desc


class TestHrRpeCaveatPreserved:
    """T049 — real coaching content (HR lags true effort on short intervals),
    not boilerplate to lose while refactoring."""

    def test_z5_hr_mode_gets_the_caveat(self):
        steps = [
            Step(kind="warmup", duration_minutes=20, zone_code="Z1"),
            RepeatGroup(repeat=5, steps=[
                Step(kind="work", duration_minutes=5, zone_code="Z5"),
                Step(kind="recovery", duration_minutes=3, zone_code="Z1"),
            ]),
            Step(kind="cooldown", duration_minutes=15, zone_code="Z1"),
        ]
        desc = render_description("intervals", steps, coaching_mode="hr")
        assert "RPE" in desc

    def test_z5_power_mode_does_not_get_the_caveat(self):
        """The caveat is specifically about HR lag — irrelevant in power mode."""
        steps = [
            Step(kind="warmup", duration_minutes=20, zone_code="Z1"),
            RepeatGroup(repeat=5, steps=[
                Step(kind="work", duration_minutes=5, zone_code="Z5"),
                Step(kind="recovery", duration_minutes=3, zone_code="Z1"),
            ]),
            Step(kind="cooldown", duration_minutes=15, zone_code="Z1"),
        ]
        desc = render_description("intervals", steps, coaching_mode="power")
        assert "RPE" not in desc

    def test_z4_hr_mode_does_not_get_the_caveat(self):
        """Only Z5/Z6 — Z4 threshold work doesn't have the same HR-lag problem."""
        desc = render_description("intervals", _threshold_steps(), coaching_mode="hr")
        assert "RPE" not in desc


class TestLanguageParameter:
    def test_french_is_the_default(self):
        steps = [Step(kind="steady", duration_minutes=60, zone_code="Z2")]
        desc = render_description("endurance", steps)
        assert "Endurance" in desc  # same word both languages; check zone name instead
        assert "Récupération" not in desc  # sanity: not accidentally the Z1 label

    def test_english_language_changes_the_workout_type_label(self):
        steps = [Step(kind="steady", duration_minutes=60, zone_code="Z1")]
        desc_fr = render_description("recovery", steps, language="fr")
        desc_en = render_description("recovery", steps, language="en")
        assert desc_fr != desc_en
        assert "Active recovery" in desc_en
        assert "Récupération" in desc_fr

    def test_english_zone_names_differ_from_french(self):
        steps = [Step(kind="steady", duration_minutes=60, zone_code="Z4")]
        desc_fr = render_description("endurance", steps, language="fr")
        desc_en = render_description("endurance", steps, language="en")
        assert "Seuil lactique" in desc_fr
        assert "Lactate threshold" in desc_en

    def test_caveat_text_also_follows_language(self):
        steps = [
            Step(kind="warmup", duration_minutes=20, zone_code="Z1"),
            RepeatGroup(repeat=5, steps=[
                Step(kind="work", duration_minutes=5, zone_code="Z5"),
                Step(kind="recovery", duration_minutes=3, zone_code="Z1"),
            ]),
            Step(kind="cooldown", duration_minutes=15, zone_code="Z1"),
        ]
        desc_fr = render_description("intervals", steps, coaching_mode="hr", language="fr")
        desc_en = render_description("intervals", steps, coaching_mode="hr", language="en")
        assert "cardio" in desc_fr
        assert "heart rate" in desc_en


class TestStoredSessionDisplay:
    def test_structured_session_renders_in_both_languages_without_changing_steps(self):
        steps = _threshold_steps()
        session = SessionSpec(
            day_of_week=1,
            workout_type="intervals",
            zone_code="Z4",
            duration_minutes=78,
            target_time_in_zone_minutes=36,
            tss_target=80.0,
            description_fr="Ancienne description française",
            steps=steps,
        )
        original = session.model_dump()

        fr = render_session_description(session, coaching_mode="power", language="fr")
        en = render_session_description(session, coaching_mode="power", language="en")

        assert fr == "Ancienne description française"
        assert "3×12min Z4 (Lactate threshold)" in en
        assert "Ancienne description" not in en
        assert session.model_dump() == original

    def test_legacy_session_keeps_stored_text_verbatim_in_both_languages(self):
        session = SessionSpec(
            day_of_week=1,
            workout_type="intervals",
            zone_code="Z4",
            duration_minutes=78,
            target_time_in_zone_minutes=36,
            tss_target=80.0,
            description_fr="3×12min au seuil — texte historique",
        )

        assert render_session_description(session, language="fr") == session.description_fr
        assert render_session_description(session, language="en") == session.description_fr

    def test_race_day_marker_keeps_its_event_instructions(self):
        steps = [Step(kind="steady", duration_minutes=20, zone_code="Z2")]
        session = SessionSpec(
            day_of_week=6,
            workout_type="long_ride",
            zone_code="Z2",
            duration_minutes=20,
            target_time_in_zone_minutes=0,
            tss_target=1.0,
            description_fr=(
                "JOUR DE COURSE — Bonne chance ! Échauffement 20min Z1-Z2 avant le départ. "
                "Ta sortie sera automatiquement importée depuis intervals.icu."
            ),
            steps=steps,
        )

        assert render_session_description(session, language="fr") == session.description_fr
        en = render_session_description(session, language="en")
        assert "RACE DAY" in en
        assert "20 min" in en
        assert "automatically" in en
        assert "Long ride" not in en

    def test_pre_race_activation_retains_purpose_and_interval_shape(self):
        steps = [
            Step(kind="warmup", duration_minutes=20, zone_code="Z2"),
            RepeatGroup(repeat=5, steps=[
                Step(kind="work", duration_minutes=5, zone_code="Z5"),
                Step(kind="recovery", duration_minutes=3, zone_code="Z1"),
            ]),
            Step(kind="cooldown", duration_minutes=10, zone_code="Z1"),
        ]
        session = SessionSpec(
            day_of_week=3,
            workout_type="intervals",
            zone_code="Z5",
            duration_minutes=70,
            target_time_in_zone_minutes=25,
            tss_target=64.0,
            description_fr=(
                "Activation pre-course — 20min Z2 échauffement, puis 5×5min Z5 / "
                "3min Z1 récup, 10min Z1 retour au calme. "
                "Rappels neuromusculaires pour ouvrir les jambes sans fatigue."
            ),
            steps=steps,
        )

        assert render_session_description(session, language="fr") == session.description_fr
        en = render_session_description(session, language="en")
        assert "Pre-race activation" in en
        assert "5×5min Z5" in en
        assert "without adding fatigue" in en


class TestNoHardcodedFrenchRemainsInPlanBuilder:
    """SC-010 — no French sentence construction left in generation logic."""

    def test_plan_builder_session_description_delegates_to_the_renderer(self):
        import app.engine.plan_builder as plan_builder_module

        source = inspect.getsource(plan_builder_module._session_description)
        assert "render_description" in source
        # No hardcoded French zone-name dict or sentence fragments remain in the
        # function itself (they moved to session_render.py).
        assert "Récupération active" not in source
        assert "Sortie longue" not in source

    def test_real_generated_plan_still_has_populated_description_fr(self):
        """FR-008/FR-030 — description_fr stays populated for the ~20 existing
        readers, even though it's no longer the only description and no longer
        built from inline French sentences."""
        from app.engine.schemas import (
            AthleteProfileSchema,
            AvailabilityProfile,
            EquipmentProfile,
            ObjectiveProfile,
            PhysioProfile,
        )

        profile = AthleteProfileSchema(
            objective=ObjectiveProfile(type="fitness", target_date=None),
            availability=AvailabilityProfile(
                hours_per_week=6, preferred_days=["tuesday", "thursday", "saturday"]
            ),
            level="intermediate",
            structured_plan_history=True,
            equipment=EquipmentProfile(power_meter=True, ftp=220, ftp_source="declared"),
            physio=PhysioProfile(
                age=34, hr_max=186, hr_max_source="declared",
                hr_rest=58, hr_rest_source="declared",
            ),
            coaching_mode="power",
            health_constraints=False,
        )
        plan = generate_plan(profile)
        for week in plan.weeks:
            for session in week.sessions:
                assert session.description_fr
