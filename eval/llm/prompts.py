"""Prompts structurés pour l'évaluation LLM en 2 passes (critic + verifier).

Principes de conception :
- Sortie JSON strict uniquement — aucun texte libre hors champs définis
- Métriques précises fournies au LLM pour éviter les jugements vagues
- Définitions explicites de chaque critère avec exemples concrets
- Le verifier reçoit le plan + la critique pour validation/correction indépendante
"""

from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema

_DAY_FR = {0: "Lun", 1: "Mar", 2: "Mer", 3: "Jeu", 4: "Ven", 5: "Sam", 6: "Dim"}

# ─────────────────────────────────────────────────────────────────────────────
# Prompts système
# ─────────────────────────────────────────────────────────────────────────────

CRITIC_SYSTEM = """\
Tu es un entraîneur cycliste expert certifié (niveau coach UCI) avec 15 ans d'expérience.
Tu évalues des plans d'entraînement générés automatiquement par un algorithme déterministe.

MISSION : Analyser le plan ci-dessous et produire une évaluation structurée en JSON.

RÈGLES ABSOLUES :
1. Retourne UNIQUEMENT du JSON valide — pas de texte avant ni après.
2. Ne commente que ce qui est présent dans les données — aucune supposition.
3. Chaque score est un entier de 0 à 10 :
   0–3 = dangereux ou fondamentalement incorrect
   4–5 = insuffisant, risques identifiés
   6–7 = acceptable avec réserves
   8–9 = bon, quelques ajustements possibles
   10  = excellent, aucune amélioration nécessaire
4. Les "points_critiques" doivent référencer précisément la semaine et la séance concernées.
5. Les "points_forts" doivent être factuels et vérifiables dans les données.

DÉFINITION DES 5 CRITÈRES D'ÉVALUATION :

1. coherence_physiologique
   Vérifie que la charge est adaptée aux paramètres physiologiques.
   ✓ Points positifs : FTP cohérent avec le niveau déclaré, zones calculées correctement,
     TSS/heure réaliste pour le niveau, FC cible dans des plages sûres selon l'âge.
   ✗ Points négatifs : TSS/h > 120 pour un débutant, FTP trop bas pour expert,
     zones qui ne correspondent pas au niveau déclaré, volume irréaliste pour l'âge.

2. progressivite_charge
   Vérifie que la montée en charge est sûre et cohérente.
   ✓ Points positifs : augmentation ≤ 10% TSS/semaine, récupération bien dosée (≤65% semaine précédente),
     progression linéaire et régulière dans chaque bloc, pic TSS atteint 2-3 semaines avant la fin.
   ✗ Points négatifs : spike TSS > 10% entre 2 semaines, récupération insuffisante,
     progression non linéaire, plan qui stagne sur plusieurs semaines.

3. respect_profil_utilisateur
   Vérifie l'adéquation avec les contraintes déclarées.
   ✓ Points positifs : séances sur les jours disponibles uniquement, volume ≤ heures/sem disponibles,
     nombre de séances cohérent avec le volume, niveau de difficulté adapté au niveau déclaré.
   ✗ Points négatifs : séances sur jours non disponibles, volume total dépassant la disponibilité,
     séances trop nombreuses ou pas assez pour le volume déclaré.

4. securite_recuperation
   Vérifie que la récupération est suffisante pour éviter le surentraînement.
   ✓ Points positifs : gap ≥ 48h entre séances Z4+, semaine de récup chaque 3-4 semaines,
     récupération = 55-65% du TSS précédent, pas de Z5 avant une sortie longue.
   ✗ Points négatifs : séances intenses 2 jours consécutifs, pas de semaine récup sur 5+ semaines,
     semaine récup trop chargée (>70% semaine normale), Z5 la veille d'une longue.

5. adequation_objectif
   Vérifie que le type de travail est adapté à l'objectif déclaré.
   ✓ Cyclosportive : base Z2 solide, threshold en build, activation pre-course en peak.
   ✓ Étape du Tour : montée en charge progressive sur 16-24 sem, gros volume Z2, VO2 en peak.
   ✓ Santé : prédominance Z1-Z2, pas de Z5+ pour débutant/intermédiaire, régularité.
   ✗ Points négatifs : trop de VO2 pour objectif santé, pas de phase threshold pour cyclosportive,
     taper absent ou trop court avant l'objectif.

FORMAT JSON DE SORTIE (respecter exactement cette structure) :
{
  "scores": {
    "coherence_physiologique": <entier 0-10>,
    "progressivite_charge": <entier 0-10>,
    "respect_profil_utilisateur": <entier 0-10>,
    "securite_recuperation": <entier 0-10>,
    "adequation_objectif": <entier 0-10>
  },
  "points_forts": [
    "<constat factuel positif précis>",
    "<constat factuel positif précis>"
  ],
  "points_critiques": [
    "<S{N} : description précise du problème>",
    "<S{N} : description précise du problème>"
  ],
  "verdict_global": "<1-2 phrases de synthèse sur la qualité globale du plan>",
  "verifier_adjustments": []
}
"""

VERIFIER_SYSTEM = """\
Tu es un entraîneur senior et formateur de coachs. Tu révises l'évaluation d'un collègue
sur un plan d'entraînement cycliste généré automatiquement.

MISSION : Valider ou corriger l'évaluation du critique en te basant uniquement sur les données.

RÈGLES ABSOLUES :
1. Retourne UNIQUEMENT du JSON valide — pas de texte avant ni après.
2. Si un score du critique est correct, conserve-le tel quel.
3. Si un score est incorrect (erreur factuelle, sévérité disproportionnée), corrige-le
   et documente la raison dans "verifier_adjustments".
4. Ajoute dans "verifier_adjustments" UNIQUEMENT les corrections effectuées, pas les confirmations.
5. Si aucune correction n'est nécessaire, "verifier_adjustments" doit être une liste vide [].
6. Les critères sont les mêmes que ceux du critique (voir définitions ci-dessous).

AXES DE CORRECTION PRIORITAIRES :
- Le critique est-il trop sévère sur les contraintes physiologiques normales ?
  (ex : pénaliser un débutant pour FTP faible alors que c'est cohérent avec son niveau)
- Le critique ignore-t-il des problèmes visibles dans les données ?
  (ex : ne pas pénaliser une séance Z5 en semaine 1 pour un débutant)
- Les "points_critiques" sont-ils vérifiables dans les données fournies ?
- La note "securite_recuperation" reflète-t-elle réellement les gaps entre séances intenses ?

DÉFINITION DES CRITÈRES (identique au critique) :
1. coherence_physiologique : charge adaptée aux paramètres physiologiques (FTP, âge, FC, niveau)
2. progressivite_charge : montée en charge ≤ 10%/sem, récupération bien dosée
3. respect_profil_utilisateur : jours disponibles, volume, nombre de séances
4. securite_recuperation : gap ≥ 48h entre Z4+, semaine récup chaque 3-4 sem, récup dosée
5. adequation_objectif : type de travail cohérent avec l'objectif (cyclosportive/santé/tour)

FORMAT JSON DE SORTIE (identique au critique, avec verifier_adjustments rempli si corrections) :
{
  "scores": {
    "coherence_physiologique": <entier 0-10>,
    "progressivite_charge": <entier 0-10>,
    "respect_profil_utilisateur": <entier 0-10>,
    "securite_recuperation": <entier 0-10>,
    "adequation_objectif": <entier 0-10>
  },
  "points_forts": ["<constat factuel positif>"],
  "points_critiques": ["<S{N} : problème précis>"],
  "verdict_global": "<synthèse finale 1-2 phrases>",
  "verifier_adjustments": [
    "<Score X modifié de Y à Z : raison factuelle>"
  ]
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# Constructeurs de messages utilisateur
# ─────────────────────────────────────────────────────────────────────────────

def build_critic_messages(
    profile: AthleteProfileSchema, plan: TrainingPlanSchema
) -> tuple[str, str]:
    """Retourne (system_prompt, user_message) pour la passe Critic."""
    user_message = f"PROFIL ATHLÈTE :\n{_summarize_profile(profile)}\n\n{_summarize_plan(profile, plan)}"
    return CRITIC_SYSTEM, user_message


def build_verifier_messages(
    profile: AthleteProfileSchema,
    plan: TrainingPlanSchema,
    critic_output: dict,
) -> tuple[str, str]:
    """Retourne (system_prompt, user_message) pour la passe Verifier."""
    import json
    user_message = (
        f"PROFIL ATHLÈTE :\n{_summarize_profile(profile)}\n\n"
        f"{_summarize_plan(profile, plan)}\n\n"
        f"ÉVALUATION DU CRITIQUE À RÉVISER :\n"
        f"```json\n{json.dumps(critic_output, ensure_ascii=False, indent=2)}\n```"
    )
    return VERIFIER_SYSTEM, user_message


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires de résumé
# ─────────────────────────────────────────────────────────────────────────────

def _summarize_profile(profile: AthleteProfileSchema) -> str:
    """Résumé concis du profil pour le LLM."""
    if profile.equipment.power_meter and profile.equipment.ftp:
        equip = (
            f"Capteur puissance : oui | FTP={profile.equipment.ftp}W "
            f"({profile.equipment.ftp_source})"
        )
    else:
        equip = "Capteur puissance : non — mode FC uniquement"

    if profile.objective.target_date:
        obj = f"{profile.objective.type} (date cible : {profile.objective.target_date})"
    else:
        obj = f"{profile.objective.type} (pas de date cible)"

    lines = [
        f"Niveau : {profile.level} | Mode coaching : {profile.coaching_mode}",
        f"Âge : {profile.physio.age} ans | Sexe : {profile.sex or 'non renseigné'}"
        + (f" | Poids : {profile.weight_kg}kg" if profile.weight_kg else ""),
        f"FCmax : {profile.physio.hr_max}bpm ({profile.physio.hr_max_source})"
        f" | FCrepos : {profile.physio.hr_rest}bpm",
        equip,
        f"Disponibilité : {profile.availability.hours_per_week}h/sem"
        f" | Jours : {', '.join(profile.availability.preferred_days)}",
        f"Objectif : {obj}",
        f"Historique plan structuré : {'oui' if profile.structured_plan_history else 'non'}",
        f"Contraintes santé : {'oui' if profile.health_constraints else 'non'}"
        + (f" | Blessure : {profile.injury_status}" if profile.injury_status else ""),
    ]
    return "\n".join(lines)


def _summarize_plan(profile: AthleteProfileSchema, plan: TrainingPlanSchema) -> str:
    """Résumé structuré du plan : zones + semaines ligne par ligne."""
    lines = ["PLAN GÉNÉRÉ :"]

    mode_label = (
        f"Puissance (FTP={profile.equipment.ftp}W)"
        if plan.coaching_mode == "power"
        else "Fréquence cardiaque"
    )
    lines.append(
        f"Durée : {plan.weeks_count} semaines | Mode : {mode_label} | "
        f"TSS initial : {plan.initial_weekly_tss:.0f}/sem → TSS pic : {plan.peak_weekly_tss:.0f}/sem"
    )

    # Zones
    lines.append("\nZONES :")
    for code in sorted(plan.zones.keys()):
        zone = plan.zones[code]
        if plan.coaching_mode == "power" and zone.lower_watts is not None:
            bounds = f"{zone.lower_watts}–{zone.upper_watts or '∞'}W"
        elif zone.lower_bpm is not None:
            bounds = f"{zone.lower_bpm}–{zone.upper_bpm or '∞'}bpm"
        else:
            bounds = "—"
        lines.append(f"  {code} ({bounds}) : {zone.description_fr}")

    # Semaines
    lines.append("\nDÉTAIL SEMAINE PAR SEMAINE :")
    for week in plan.weeks:
        tags = []
        if week.is_recovery_week:
            tags.append("RÉCUP")
        tag_str = f" [{'/'.join(tags)}]" if tags else ""
        lines.append(
            f"S{week.week_number} ({week.phase.upper()}{tag_str})"
            f" | TSS cible : {week.total_tss_target:.0f}"
        )
        for s in sorted(week.sessions, key=lambda x: x.day_of_week):
            zone_info = f" (TIZ={s.target_time_in_zone_minutes}min)" if s.workout_type == "intervals" else ""
            lines.append(
                f"  {_DAY_FR.get(s.day_of_week, '?')} : "
                f"{s.workout_type} {s.zone_code} {s.duration_minutes}min"
                f"{zone_info} | TSS={s.tss_target:.0f}"
            )

    return "\n".join(lines)
