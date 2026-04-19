"""
Import de l'historique Strava pour l'onboarding.

Flux :
  1. GET /athlete          → ftp, weight, sex, id, firstname
  2. GET /athletes/{id}/stats → ytd_ride_totals + all_ride_totals (carrière)
  3. GET /athlete/activities → activités des 49 derniers jours (paginé)
  → calcul TSS par activité → bulk_insert → analyse
"""

import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import httpx

from app.db.repositories import activity_repo
from app.engine.tss import calc_tss
from app.strava.client import get_athlete, get_athlete_stats

logger = logging.getLogger(__name__)

_STRAVA_API_BASE = "https://www.strava.com/api/v3"
_CYCLING_TYPES = {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "EBikeRide"}

# Coefficients W/kg pour estimation FTP selon niveau
_FTP_W_PER_KG = {
    "beginner": 2.5,
    "intermediate": 3.0,
    "advanced": 3.5,
    "expert": 4.0,
}


async def fetch_history(access_token: str, days: int = 49) -> list[dict]:
    """Récupère les activités cyclisme des N derniers jours (paginé)."""
    after_epoch = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    activities = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{_STRAVA_API_BASE}/athlete/activities",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"after": after_epoch, "per_page": 100, "page": page},
                timeout=15.0,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break

            for act in batch:
                if act.get("sport_type") in _CYCLING_TYPES:
                    activities.append(act)

            if len(batch) < 100:
                break
            page += 1

    return activities


async def import_history(session, user_id, access_token: str) -> dict:
    """
    Importe l'historique Strava et calcule les métriques d'onboarding.

    Retourne un dict d'analyse utilisé pour auto-remplir l'onboarding.
    """
    # 1. Profil athlète
    athlete = await get_athlete(access_token)
    athlete_id = athlete.get("id")
    athlete_ftp = athlete.get("ftp")          # None si non renseigné (non-premium)
    athlete_weight = athlete.get("weight")    # kg, peut être None
    athlete_sex = athlete.get("sex")          # "M" | "F" | None
    athlete_firstname = athlete.get("firstname", "")

    # 2. Stats carrière + YTD
    all_ride_totals = {}
    ytd_moving_time_s = 0
    if athlete_id:
        try:
            stats = await get_athlete_stats(access_token, athlete_id)
            ytd_moving_time_s = stats.get("ytd_ride_totals", {}).get("moving_time", 0)
            all_ride_totals = stats.get("all_ride_totals", {})
        except Exception:
            logger.warning("Impossible de récupérer les stats Strava")

    # 3. Activités des 49 derniers jours
    fetch_days = 120
    raw_activities = await fetch_history(access_token, days=fetch_days)

    # 4. Analyse + calcul TSS
    analysis = _analyze(
        raw_activities,
        ytd_moving_time_s=ytd_moving_time_s,
        ftp=athlete_ftp,
        weight_kg=athlete_weight,
        all_ride_totals=all_ride_totals,
        fetch_days=fetch_days,
    )

    # FTP : Strava en priorité, sinon estimation depuis poids/niveau
    ftp_detected = athlete_ftp
    if not ftp_detected and athlete_weight:
        coeff = _FTP_W_PER_KG.get(analysis["level_detected"], 3.0)
        ftp_detected = int(athlete_weight * coeff)

    # Threshold HR ≈ 92% de la FC max observée
    hr_max = analysis.get("hr_max_detected")
    threshold_hr = int(hr_max * 0.92) if hr_max else None

    # 5. Construire les rows à insérer
    rows = []
    for act in raw_activities:
        duration_s = act.get("moving_time") or act.get("elapsed_time") or 0
        np_watts = act.get("weighted_average_watts")
        avg_watts = act.get("average_watts")
        avg_hr = act.get("average_heartrate")
        max_hr = act.get("max_heartrate")
        suffer = act.get("suffer_score")
        device_w = act.get("device_watts", False)

        tss, tss_method = calc_tss(
            duration_s=duration_s,
            normalized_w=np_watts,
            avg_hr=avg_hr,
            ftp=ftp_detected,
            threshold_hr=threshold_hr,
            suffer_score=suffer,
        )

        sport_type = act.get("sport_type", "Ride")
        env = "indoor" if sport_type == "VirtualRide" else "outdoor"

        act_date_str = act.get("start_date_local", act.get("start_date", ""))
        try:
            act_date = date.fromisoformat(act_date_str[:10])
        except (ValueError, TypeError):
            continue

        rows.append({
            "source": "strava",
            "source_activity_id": act.get("id"),
            "activity_date": act_date,
            "duration_seconds": duration_s or None,
            "sport_type": sport_type,
            "environment": env,
            "distance_meters": act.get("distance"),
            "elevation_gain_meters": act.get("total_elevation_gain"),
            "avg_watts": avg_watts,
            "normalized_watts": np_watts,
            "device_watts": device_w,
            "avg_heartrate": avg_hr,
            "max_heartrate": max_hr,
            "suffer_score": suffer,
            "kilojoules": act.get("kilojoules"),
            "tss": round(tss, 1) if tss else None,
            "tss_method": tss_method,
            "ftp_used": ftp_detected,
        })

    inserted = await activity_repo.bulk_insert(session, user_id, rows)
    logger.info(f"Import Strava : {inserted}/{len(rows)} activités insérées pour user {user_id}")

    total_km = round(all_ride_totals.get("distance", 0) / 1000)
    total_rides = all_ride_totals.get("count", 0)

    return {
        "activity_count": len(raw_activities),
        "volume_suggested": analysis["avg_weekly_hours"],   # suggestion, pas auto-rempli
        "level_detected": analysis["level_detected"],
        "ftp_detected": ftp_detected,
        "hr_max_detected": hr_max,
        "has_power_meter": analysis["has_power_meter"],
        "preferred_days": analysis["preferred_days"],
        "athlete_weight_kg": athlete_weight,
        "athlete_sex": athlete_sex,
        "athlete_firstname": athlete_firstname,
        "total_rides": total_rides,
        "total_km": total_km,
        "fetch_weeks": round(fetch_days / 7),
    }


async def generate_strava_intro(analysis: dict) -> str:
    """Génère via LLM un message d'accueil personnalisé basé sur les stats Strava."""
    from app.llm.factory import get_provider

    level_labels = {
        "beginner": "débutant", "intermediate": "intermédiaire",
        "advanced": "avancé", "expert": "expert",
    }

    # Résumé des dernières activités pour le contexte
    recent_summary = ""
    strava_analysis = analysis  # analysis IS the dict we built
    # (no raw_activities here — we summarize from what we have)

    firstname = analysis.get("athlete_firstname", "")
    total_rides = analysis.get("total_rides", 0)
    total_km = analysis.get("total_km", 0)
    ftp = analysis.get("ftp_detected")
    level = level_labels.get(analysis.get("level_detected", ""), "")
    volume = analysis.get("volume_suggested", 0)
    has_pm = analysis.get("has_power_meter", False)
    hr_max = analysis.get("hr_max_detected")
    activity_count = analysis.get("activity_count", 0)
    fetch_weeks = analysis.get("fetch_weeks", 17)

    stats_block = f"""Prénom : {firstname}
Sorties totales carrière : {total_rides}
Kilomètres totaux carrière : {total_km} km
FTP : {ftp}W{"" if ftp else " (non renseigné)"}
Capteur puissance : {"Oui" if has_pm else "Non"}
Volume récent ({fetch_weeks} dernières semaines) : {volume:.1f}h/semaine ({activity_count} sorties)
FC Max observée : {hr_max if hr_max else "non disponible"}
Niveau estimé : {level}"""

    system_prompt = (
        "Tu es Banister, un coach cyclisme IA. "
        "En 2 à 4 phrases maximum, présente à l'athlète un résumé chaleureux et élogieux de son profil Strava. "
        "Cite ses vraies statistiques (km totaux, FTP, niveau). Sois direct et personnel. "
        "Ne pose aucune question. Termine sur une note positive et motivante. "
        "Texte brut uniquement, sans balises HTML ni markdown (pas de **, *, <b>). Quelques emojis discrets bienvenus."
    )

    user_message = f"Voici les stats Strava de l'athlète :\n{stats_block}\n\nGénère le message de bienvenue."

    try:
        provider = get_provider()
        return await provider.generate(system_prompt, user_message, max_tokens=250)
    except Exception:
        logger.exception("Erreur génération intro Strava LLM")
        # Fallback déterministe
        name_part = f"{firstname}, t" if firstname else "T"
        ftp_part = f" — FTP {ftp}W" if ftp else ""
        return (
            f"{name_part}u as {total_rides} sorties et {total_km} km au compteur sur Strava{ftp_part}. "
            f"Niveau estimé : {level}. "
            "Quel est ton objectif pour cette saison ?"
        )


def _analyze(
    activities: list[dict],
    ytd_moving_time_s: int = 0,
    ftp: int | None = None,
    weight_kg: float | None = None,
    all_ride_totals: dict | None = None,
    fetch_days: int = 49,
) -> dict:
    """
    Analyse l'historique pour détecter niveau, volume, jours préférés, etc.

    Priorité niveau : FTP/kg > carrière (all_ride_totals) > volume 49j
    """
    all_ride_totals = all_ride_totals or {}
    levels_order = ["beginner", "intermediate", "advanced", "expert"]

    if not activities:
        avg_weekly_hours = 0.0
    else:
        total_seconds = sum(a.get("moving_time") or a.get("elapsed_time") or 0 for a in activities)
        avg_weekly_hours = round(total_seconds / 3600 / (fetch_days / 7), 1)

    # ── Détection du niveau ───────────────────────────────────────────────────

    # Priorité 1 : FTP/kg
    if ftp and weight_kg and weight_kg > 0:
        level_primary = _level_from_ftp_per_kg(ftp, weight_kg)
    # Priorité 2 : stats carrière
    elif all_ride_totals.get("count", 0) > 0 or all_ride_totals.get("distance", 0) > 0:
        level_primary = _level_from_career(all_ride_totals)
    # Priorité 3 : volume 49j
    else:
        level_primary = _level_from_weekly_hours(avg_weekly_hours)

    # Cross-validation avec YTD (garder le plus conservateur)
    level_volume = _level_from_weekly_hours(avg_weekly_hours) if avg_weekly_hours > 0 else level_primary
    ytd_weekly_hours = 0.0
    if ytd_moving_time_s:
        today = date.today()
        weeks_ytd = max(1, today.timetuple().tm_yday / 7)
        ytd_weekly_hours = (ytd_moving_time_s / 3600) / weeks_ytd
    level_ytd = _level_from_weekly_hours(ytd_weekly_hours) if ytd_weekly_hours > 0 else level_primary

    idx_primary = levels_order.index(level_primary)
    idx_volume = levels_order.index(level_volume)
    idx_ytd = levels_order.index(level_ytd)
    # Si FTP/kg disponible, il domine sans cross-validation (signal le plus fiable)
    if ftp and weight_kg:
        level_detected = level_primary
    else:
        level_detected = levels_order[min(idx_primary, idx_volume, idx_ytd)]

    if not activities:
        return {
            "avg_weekly_hours": avg_weekly_hours,
            "level_detected": level_detected,
            "has_power_meter": False,
            "hr_max_detected": None,
            "preferred_days": ["tuesday", "thursday", "saturday"],
        }

    # ── Capteur puissance ─────────────────────────────────────────────────────
    has_power_meter = any(
        a.get("device_watts") and a.get("sport_type") != "VirtualRide"
        for a in activities
    )

    # ── FC max ────────────────────────────────────────────────────────────────
    hr_values = [a["max_heartrate"] for a in activities if a.get("max_heartrate")]
    hr_max_detected = int(max(hr_values)) if hr_values else None

    # ── Jours préférés ────────────────────────────────────────────────────────
    _DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day_counts: Counter = Counter()
    for act in activities:
        date_str = act.get("start_date_local", act.get("start_date", ""))
        try:
            act_date = date.fromisoformat(date_str[:10])
            day_counts[act_date.weekday()] += 1
        except (ValueError, TypeError):
            continue

    top_days = [_DAY_NAMES[d] for d, _ in day_counts.most_common(3)]
    if not top_days:
        top_days = ["tuesday", "thursday", "saturday"]

    return {
        "avg_weekly_hours": avg_weekly_hours,
        "level_detected": level_detected,
        "has_power_meter": has_power_meter,
        "hr_max_detected": hr_max_detected,
        "preferred_days": top_days,
    }


def _level_from_ftp_per_kg(ftp: int, weight_kg: float) -> str:
    """Détecte le niveau depuis le ratio FTP/kg (métrique standard cyclisme)."""
    ratio = ftp / weight_kg
    if ratio < 2.0:
        return "beginner"
    if ratio < 3.0:
        return "intermediate"
    if ratio < 4.0:
        return "advanced"
    return "expert"


def _level_from_career(all_ride_totals: dict) -> str:
    """Détecte le niveau depuis les stats de carrière (fallback sans FTP)."""
    total_km = all_ride_totals.get("distance", 0) / 1000
    total_rides = all_ride_totals.get("count", 0)

    # Prendre le niveau le plus conservateur entre km et nb sorties
    if total_km < 2000 or total_rides < 50:
        level_km = "beginner"
    elif total_km < 15000 or total_rides < 300:
        level_km = "intermediate"
    elif total_km < 40000 or total_rides < 1000:
        level_km = "advanced"
    else:
        level_km = "expert"

    return level_km


def _level_from_weekly_hours(avg_hours: float) -> str:
    if avg_hours < 3:
        return "beginner"
    if avg_hours < 7:
        return "intermediate"
    if avg_hours < 12:
        return "advanced"
    return "expert"
