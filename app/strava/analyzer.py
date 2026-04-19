from __future__ import annotations

from statistics import mean
import math

import dataclasses

from app.engine.tss import HRSSResult, calc_hrss, calc_tss
from app.engine.zones import compute_hr_zones, compute_power_zones
from app.strava.analysis_models import AnalyzedSession, RawActivity


class SessionAnalyzer:
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
        time_in_zones_s = self._compute_time_in_zones(raw, ftp=ftp, hr_max=hr_max, hr_rest=hr_rest)
        dominant_zone = max(time_in_zones_s, key=time_in_zones_s.get) if time_in_zones_s else None
        respect_zones_score = self._compute_respect_zones_score(time_in_zones_s, planned_zone, planned_target_time_in_zone_s)
        normalized_power, np_source = self._compute_normalized_power(raw)

        # ── Calcul TSS : power > HRSS (streams HR) > fallback ───────────
        fatigue_anomaly: dict | None = None

        if normalized_power and ftp and ftp > 0:
            # Mode power : TSS depuis NP/FTP (HRSS non applicable)
            tss, _ = calc_tss(
                duration_s=raw.moving_time_s or raw.duration_s,
                normalized_w=normalized_power,
                avg_hr=None,
                ftp=ftp,
                threshold_hr=None,
                suffer_score=None,
            )
        elif (
            raw.streams
            and raw.streams.heartrate
            and raw.streams.time
            and hr_rest is not None
            and hr_max is not None
            and threshold_hr is not None
        ):
            # Mode HR haute fidélité : TRIMP de Banister sur la série temporelle
            hrss_result: HRSSResult = calc_hrss(
                hr_series=raw.streams.heartrate,
                time_series=raw.streams.time,
                hr_rest=hr_rest,
                hr_max=hr_max,
                threshold_hr=threshold_hr,
                sex=sex or "M",
                user_rpe=user_rpe,
            )
            tss = hrss_result.hrss
            if hrss_result.fatigue_anomaly is not None:
                fatigue_anomaly = dataclasses.asdict(hrss_result.fatigue_anomaly)
        else:
            # Fallback : avg_hr scalaire ou suffer_score (import historique)
            tss, _ = calc_tss(
                duration_s=raw.moving_time_s or raw.duration_s,
                normalized_w=normalized_power,
                avg_hr=raw.avg_hr,
                ftp=ftp,
                threshold_hr=threshold_hr,
                suffer_score=raw.suffer_score,
            )

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
            tss=round(tss, 1),
            intensity_factor=round(intensity_factor, 3) if intensity_factor is not None else None,
            variability_index=variability_index,
            session_type_real=session_type_real,
            respect_zones_score=respect_zones_score,
            cardiac_drift_index=self._cardiac_drift_index(raw),
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
        Normalized Power (NP): priorité au NP Strava, puis calculé depuis les watts streams.
        """
        if raw.weighted_avg_power and raw.weighted_avg_power > 0:
            return float(raw.weighted_avg_power), "strava"

        watts = raw.streams.watts if raw.streams and raw.streams.watts else []
        
        # On garde les zéros, on remplace juste les None par 0
        clean_watts = [w if w is not None else 0 for w in watts]
        
        if len(clean_watts) < 30: # Trop court pour un NP cohérent
            return None, None

        # 2. Moyenne mobile de 30 secondes (Lissage physiologique)
        rolling_30s = [
            sum(clean_watts[i-30:i]) / 30 
            for i in range(30, len(clean_watts))
        ]

        # 3. Calcul NP : Moyenne des puissances 4ème, puis racine 4ème
        if rolling_30s:
            sum_p4 = sum(pow(p, 4) for p in rolling_30s)
            mean_p4 = sum_p4 / len(rolling_30s)
            return float(math.pow(mean_p4, 0.25)), "computed"
        
        return None, None

    def _compute_respect_zones_score(
        self,
        time_in_zones_s: dict[str, int],
        planned_zone: str | None,
        target_time_in_zone_s: int | None,
    ) -> float | None:
        """
        Score : part du temps passé dans la zone par rapport au TEMPS CIBLE prévu.
        """
        if not planned_zone or not target_time_in_zone_s or target_time_in_zone_s <= 0:
            return None
            
        in_planned = time_in_zones_s.get(planned_zone, 0)
        
        # On applique une petite tolérance de 5% pour l'inertie physiologique (montée en FC/Watts)
        score = (in_planned / target_time_in_zone_s) * 105
        return min(round(score, 2), 100.0)
    
    def _compute_time_in_zones(
        self,
        raw: RawActivity,
        *,
        ftp: int | None,
        hr_max: int | None,
        hr_rest: int | None,
    ) -> dict[str, int]:
        if not raw.streams or not raw.streams.time:
            return {}

        if raw.streams.watts and ftp:
            zones = compute_power_zones(ftp)
            values = raw.streams.watts
            return self._accumulate_zones(raw.streams.time, values, zones, mode="power")

        if raw.streams.heartrate and hr_max and hr_rest is not None:
            zones = compute_hr_zones(hr_max, hr_rest)
            values = raw.streams.heartrate
            return self._accumulate_zones(raw.streams.time, values, zones, mode="hr")

        return {}

    def _accumulate_zones(self, times: list[int], values: list[float], zones: dict, *, mode: str) -> dict[str, int]:
        if len(times) < 2:
            return {}

        zone_times: dict[str, int] = {code: 0 for code in zones.keys()}
        samples = min(len(times), len(values))

        for idx in range(1, samples):
            delta = max(0, times[idx] - times[idx - 1])
            val = values[idx]
            for code, zone in zones.items():
                lo = zone.lower_watts if mode == "power" else zone.lower_bpm
                hi = zone.upper_watts if mode == "power" else zone.upper_bpm
                if lo is None or hi is None:
                    continue
                if lo <= val <= hi:
                    zone_times[code] += delta
                    break

        return {k: v for k, v in zone_times.items() if v > 0}

    def _detect_session_type(self, raw: RawActivity, time_in_zones_s: dict[str, int]) -> str:
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
    
    def _cardiac_drift_index(self, raw: RawActivity) -> float | None:
        if not raw.streams or not raw.streams.watts or not raw.streams.heartrate:
            return None
        samples = min(len(raw.streams.watts), len(raw.streams.heartrate))
        if samples < 20:
            return None

        half = samples // 2
        pw_first = mean(raw.streams.watts[:half]) or 0
        pw_second = mean(raw.streams.watts[half:]) or 0
        hr_first = mean(raw.streams.heartrate[:half]) or 0
        hr_second = mean(raw.streams.heartrate[half:]) or 0
        if pw_first <= 0 or pw_second <= 0 or hr_first <= 0:
            return None

        ef_first = pw_first / hr_first
        ef_second = pw_second / hr_second if hr_second > 0 else 0
        if ef_first <= 0:
            return None
        return round((ef_first - ef_second) / ef_first, 4)

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
