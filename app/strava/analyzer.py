from __future__ import annotations

from app.strava.analysis_models import AnalyzedSession, RawActivity


class SessionAnalyzer:
    """Analyzes Strava-sourced activities (spec 002: reduced — no local TSS or zone
    derivation; those are now intervals.icu's job, done in app/providers/intervals/mapper.py.
    This class stays only for the transition window before Strava is removed (spec 002
    Phase F) and for the quality metrics the source cannot know (our own plan, our own
    classification): respect_zones_score, session_type_real, intervals_consistency_index.

    tss, time_in_zones_s, dominant_zone and cardiac_drift_index are always empty/None for
    activities analyzed here — a Strava activity has no source-computed equivalent of
    intervals.icu's icu_training_load/icu_zone_times/decoupling, and recomputing them
    locally is exactly the redundant calculation this feature removes (FR-017). This is an
    accepted, temporary reduction in what the Strava path can report during the migration
    window, not a bug.
    """

    def analyze(
        self,
        raw: RawActivity,
        *,
        ftp: int | None = None,
        hr_max: int | None = None,
        hr_rest: int | None = None,
        threshold_hr: int | None = None,
        sex: str | None = None,
        user_rpe: int | None = None,
        planned_session_id: str | None = None,
        planned_workout_type: str | None = None,
        planned_zone: str | None = None,
        planned_tss: float | None = None,
        planned_target_time_in_zone_s: float | None = None,
    ) -> AnalyzedSession:
        time_in_zones_s: dict[str, int] = {}
        dominant_zone = None
        respect_zones_score = self._compute_respect_zones_score(time_in_zones_s, planned_zone, planned_target_time_in_zone_s)
        normalized_power, np_source = self._compute_normalized_power(raw)

        tss: float | None = None
        fatigue_anomaly: dict | None = None

        intensity_factor = (normalized_power / ftp) if normalized_power and ftp else None
        variability_index = (
            round(normalized_power / raw.avg_power, 3)
            if normalized_power and raw.avg_power and raw.avg_power > 0
            else None
        )
        session_type_real = self._detect_session_type(raw, time_in_zones_s)

        environment = "indoor" if raw.strava_sport_type == "VirtualRide" else "outdoor"

        return AnalyzedSession(
            session_id=raw.activity_id,
            source=raw.source,
            sport_type=raw.sport_type,
            start_datetime=raw.start_datetime,
            duration_s=raw.duration_s,
            moving_time_s=raw.moving_time_s,
            distance_m=raw.distance_m,
            has_power=raw.has_power,
            has_heartrate=raw.has_heartrate,
            has_gps=raw.has_gps,
            avg_power=raw.avg_power,
            normalized_power=round(normalized_power, 1) if normalized_power is not None else None,
            normalized_power_source=np_source,
            max_power=raw.max_power,
            avg_hr=raw.avg_hr,
            max_hr=raw.max_hr,
            avg_speed_m_s=raw.avg_speed_m_s,
            time_in_zones_s=time_in_zones_s,
            dominant_zone=dominant_zone,
            tss=tss,
            intensity_factor=round(intensity_factor, 3) if intensity_factor is not None else None,
            variability_index=variability_index,
            session_type_real=session_type_real,
            respect_zones_score=respect_zones_score,
            cardiac_drift_index=None,
            intervals_consistency_index=self._intervals_consistency_index(raw, ftp=ftp),
            planned_session_id=planned_session_id,
            planned_workout_type=planned_workout_type,
            planned_zone=planned_zone,
            planned_target_time_in_zone_s=planned_target_time_in_zone_s,
            planned_tss=planned_tss,
            plan_match_score=None,
            fatigue_anomaly=fatigue_anomaly,
            environment=environment,
        )

    def _compute_normalized_power(self, raw: RawActivity) -> tuple[float | None, str | None]:
        """
        Normalized Power (NP): Strava's own weighted_avg_power, passed through as-is.

        The stream-based rolling-30s / 4th-power local computation this used to fall back
        to is removed (spec 002 FR-017) — intervals.icu's icu_weighted_avg_watts made it
        redundant, and the formula was never applied here anyway once a real source value
        exists.
        """
        if raw.weighted_avg_power and raw.weighted_avg_power > 0:
            return float(raw.weighted_avg_power), "strava"
        return None, None

    def _compute_respect_zones_score(
        self,
        time_in_zones_s: dict[str, int],
        planned_zone: str | None,
        target_time_in_zone_s: int | None,
    ) -> float | None:
        """
        Score : part du temps passé dans la zone par rapport au TEMPS CIBLE prévu.

        Returns None (not 0.0) when there is no zone data at all — "we don't know" is not
        "you spent none of your time there" (FR-020). Only meaningful once time_in_zones_s
        is populated (currently always empty for Strava activities; intervals.icu's mapper
        populates it from icu_zone_times).
        """
        if not planned_zone or not target_time_in_zone_s or target_time_in_zone_s <= 0:
            return None
        if not time_in_zones_s:
            return None

        in_planned = time_in_zones_s.get(planned_zone, 0)
        
        # On applique une petite tolérance de 5% pour l'inertie physiologique (montée en FC/Watts)
        score = (in_planned / target_time_in_zone_s) * 105
        return min(round(score, 2), 100.0)
    
    def _detect_session_type(self, raw: RawActivity, time_in_zones_s: dict[str, int]) -> str:
        # time_in_zones_s is always {} for Strava activities now (zone derivation removed,
        # FR-017) — this always falls through to the total<=0 branch below for the Strava
        # path. Kept rather than deleted because it is our own classification (T021) and
        # intervals.icu's mapper feeds it real zone data (icu_zone_times), where it works
        # as originally designed.
        total = sum(time_in_zones_s.values())
        if total <= 0:
            # Activité manuelle : pas de streams → impossible de déduire le type d'effort
            if getattr(raw, 'is_manual', False):
                return "unknown"
            return "race" if getattr(raw, 'race_or_test', False) else "unknown"

        duration_h = raw.duration_s / 3600
        z1_z2 = (time_in_zones_s.get("Z1", 0) + time_in_zones_s.get("Z2", 0)) / total
        z3 = time_in_zones_s.get("Z3", 0) / total
        z3_z4 = (time_in_zones_s.get("Z3", 0) + time_in_zones_s.get("Z4", 0)) / total
        z4_plus = (time_in_zones_s.get("Z4", 0) + time_in_zones_s.get("Z5", 0) + time_in_zones_s.get("Z6", 0)) / total

        # 1. Priorité Intervalles (Haute intensité + variabilité)
        if z4_plus >= 0.10 and self._high_stream_variation(raw):
            return "intervals"

        # 2. Tempo — seuil plus exigeant pour les longues sorties (>3h avec 40% z3/z4 = endurance tonique)
        tempo_threshold = 0.6 if duration_h > 3 else 0.4
        if z3_z4 >= tempo_threshold:
            return "tempo"

        # 3. Récupération / Endurance / Long ride par distribution de zones
        if z1_z2 >= 0.6:
            if raw.duration_s < 3600:
                return "recovery"
            elif duration_h >= 2.5:  # 2h30+ en Z1/Z2 = long ride
                return "long_ride"
            else:
                return "endurance"

        # 4. Long ride par durée (sans dominante de zone claire)
        if duration_h >= 2.5:
            return "long_ride"

        return "unknown"
    
    def _high_stream_variation(self, raw: RawActivity) -> bool:
        """
        Détecte la variabilité via le ratio P95/P05 pour ignorer les spikes de puissance.
        """
        if not raw.streams or not raw.streams.watts:
            return False
            
        active_watts = [w for w in raw.streams.watts if w and w > 0]
        if len(active_watts) < 60: # Minimum 1 minute de données actives
            return False
            
        avg = sum(active_watts) / len(active_watts)
        sorted_watts = sorted(active_watts)
        
        # Calcul des percentiles pour l'effort "stable" haut et bas
        p95 = sorted_watts[int(len(sorted_watts) * 0.95)]
        p05 = sorted_watts[int(len(sorted_watts) * 0.05)]
        
        variance_ratio = (p95 - p05) / avg
        return variance_ratio > 0.6
    
    def _intervals_consistency_index(self, raw: RawActivity, ftp: int | None = None) -> float | None:
        """
        Détecte les blocs d'effort (> seuil pendant ≥ 20s) et mesure la consistance
        via le coefficient de variation de leur puissance moyenne.
        Retourne None si < 2 blocs détectés (pas une séance d'intervalles).
        """
        if not raw.streams or not raw.streams.watts or not raw.streams.time:
            return None

        watts = raw.streams.watts
        times = raw.streams.time
        if len(watts) < 60:
            return None

        active = [w for w in watts if w and w > 0]
        if not active:
            return None
        avg = sum(active) / len(active)
        if avg <= 0:
            return None

        # Seuil : 120% FTP si disponible, sinon 130% de la moyenne active
        threshold = ftp * 1.2 if ftp else avg * 1.3

        # Détecter les blocs d'effort consécutifs au-dessus du seuil
        blocks: list[float] = []
        in_block = False
        block_start_idx = 0
        block_watts: list[float] = []

        for i, w in enumerate(watts):
            above = bool(w and w >= threshold)
            if above:
                if not in_block:
                    in_block = True
                    block_start_idx = i
                    block_watts = [w]
                else:
                    block_watts.append(w)
            else:
                if in_block:
                    end_idx = i - 1
                    t_start = times[block_start_idx] if block_start_idx < len(times) else 0
                    t_end = times[end_idx] if end_idx < len(times) else t_start
                    if (t_end - t_start) >= 20 and block_watts:
                        blocks.append(sum(block_watts) / len(block_watts))
                    in_block = False
                    block_watts = []

        # Traiter le dernier bloc encore ouvert
        if in_block and block_watts:
            end_idx = len(watts) - 1
            t_start = times[block_start_idx] if block_start_idx < len(times) else 0
            t_end = times[end_idx] if end_idx < len(times) else t_start
            if (t_end - t_start) >= 20:
                blocks.append(sum(block_watts) / len(block_watts))

        if len(blocks) < 2:
            return None  # pas assez d'intervalles détectés

        block_mean = sum(blocks) / len(blocks)
        if block_mean <= 0:
            return None

        variance = sum((b - block_mean) ** 2 for b in blocks) / len(blocks)
        cv = (variance ** 0.5) / block_mean
        return round(max(0.0, min(1.0, 1.0 - cv)), 4)
