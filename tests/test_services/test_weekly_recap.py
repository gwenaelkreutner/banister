"""Weekly recap verification with source-computed loads (spec 002 T052, FR-034a).

FR-034a is explicit that this must be *verified*, not assumed unaffected, now that TSS
values come from intervals.icu rather than local computation — including the case where
the source has no computed load for an activity (tss_actual/tss stays None, FR-020).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.db.models.user import User
from app.db.repositories import plan_repo, session_log_repo, wellness_repo
from app.services.weekly_recap import compute_weekly_recap


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


def _plan_technical() -> dict:
    return {
        "weeks": [
            {
                "week_number": 1,
                "phase": "base",
                "is_recovery_week": False,
                "total_tss_target": 300,
                "sessions": [
                    {
                        "day_of_week": d,
                        "workout_type": "endurance",
                        "zone_code": "Z2",
                        "duration_minutes": 60,
                        "target_time_in_zone_minutes": 40,
                        "tss_target": 60,
                        "description_fr": "Sortie",
                    }
                    for d in range(5)
                ],
            }
        ],
        "zones": {},
        "initial_weekly_tss": 200,
        "peak_weekly_tss": 350,
        "weeks_count": 1,
        "coaching_mode": "power",
    }


async def _make_plan(session, user_id, *, start: date):
    return await plan_repo.create(
        session, user_id, plan_technical=_plan_technical(), start_date=start, end_date=start
    )


class TestWeeklyRecapWithSourceComputedLoads:
    async def test_stats_section_is_correct_with_intervals_icu_logs(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "app.llm.factory.get_provider", lambda: (_ for _ in ()).throw(RuntimeError())
        )
        monday = date.today() - timedelta(days=date.today().weekday())
        user = await _make_user(db_session, 1000)
        await _make_plan(db_session, user.id, start=monday)

        # Two intervals.icu-sourced logs with real TSS values
        await session_log_repo.create(
            db_session, user.id, plan_id=(await plan_repo.get_active_plan(db_session, user.id)).id,
            week_number=1, day_of_week=0, logged_date=monday, status="done",
            tss_actual=80.0, source="intervals_icu", source_activity_id="i-1",
        )
        await session_log_repo.create(
            db_session, user.id, plan_id=(await plan_repo.get_active_plan(db_session, user.id)).id,
            week_number=1, day_of_week=2, logged_date=monday + timedelta(days=2), status="done",
            tss_actual=60.0, source="intervals_icu", source_activity_id="i-2",
        )
        await db_session.commit()

        result = await compute_weekly_recap(db_session, user, today=monday + timedelta(days=3))

        assert result.has_data is True
        assert "140" in result.stats_section  # 80 + 60 TSS
        assert "2" in result.stats_section  # 2 sessions done

    async def test_null_tss_log_does_not_crash_the_recap(self, db_session, monkeypatch):
        """FR-020: an intervals.icu activity with no computed load (tss_actual=None) must
        not crash the weekly recap — it should simply not contribute a TSS number,
        exactly like a log with no TSS already had to be handled before this feature."""
        monkeypatch.setattr(
            "app.llm.factory.get_provider", lambda: (_ for _ in ()).throw(RuntimeError())
        )
        monday = date.today() - timedelta(days=date.today().weekday())
        user = await _make_user(db_session, 1001)
        plan = await _make_plan(db_session, user.id, start=monday)

        await session_log_repo.create(
            db_session, user.id, plan_id=plan.id,
            week_number=1, day_of_week=0, logged_date=monday, status="done",
            tss_actual=None, source="intervals_icu", source_activity_id="i-null-tss",
        )
        await db_session.commit()

        result = await compute_weekly_recap(db_session, user, today=monday + timedelta(days=1))

        assert result.has_data is True
        assert "0" in result.stats_section  # TSS 0 — the null-tss log contributed nothing

    async def test_tsb_comes_from_wellness_not_recomputed_locally(self, db_session, monkeypatch):
        """spec 002 FR-016: once a wellness row exists, the recap's TSB must be the
        source's own CTL/ATL, not app/engine/atl_ctl.py's local recomputation from logs.
        A single 60-TSS endurance log alone would land near TSB≈0 locally ("Bonne forme
        pour aborder..."); the wellness row here is deliberately extreme (TSB=-60) so the
        two paths produce different, distinguishable fallback text — proving which one
        actually won."""
        monkeypatch.setattr(
            "app.llm.factory.get_provider", lambda: (_ for _ in ()).throw(RuntimeError())
        )
        monday = date.today() - timedelta(days=date.today().weekday())
        today = monday + timedelta(days=3)
        user = await _make_user(db_session, 1005)
        plan = await _make_plan(db_session, user.id, start=monday)

        await session_log_repo.create(
            db_session, user.id, plan_id=plan.id,
            week_number=1, day_of_week=0, logged_date=monday, status="done",
            tss_actual=60.0, source="intervals_icu", source_activity_id="i-wellness-check",
        )
        await wellness_repo.upsert(db_session, user.id, today, ctl=30.0, atl=90.0)  # tsb = -60
        await db_session.commit()

        result = await compute_weekly_recap(db_session, user, today=today)

        assert "fatigue accumulée" in result.next_week_section  # tsb < -10 fallback text
        assert "Bonne forme" not in result.next_week_section

    async def test_weekly_adherence_is_persisted(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "app.llm.factory.get_provider", lambda: (_ for _ in ()).throw(RuntimeError())
        )
        monday = date.today() - timedelta(days=date.today().weekday())
        user = await _make_user(db_session, 1002)
        plan = await _make_plan(db_session, user.id, start=monday)

        await session_log_repo.create(
            db_session, user.id, plan_id=plan.id,
            week_number=1, day_of_week=0, logged_date=monday, status="done",
            tss_actual=80.0, source="intervals_icu", source_activity_id="i-3",
        )
        await db_session.commit()

        await compute_weekly_recap(db_session, user, today=monday + timedelta(days=1))
        await db_session.commit()

        from app.db.repositories import weekly_adherence_repo

        rows = await weekly_adherence_repo.get_recent(db_session, user.id, limit=1)
        assert len(rows) == 1
        assert rows[0].tss_7d == 80.0
