"""
Calcul des zones d'entraînement Z1-Z6.
Mode power : basé sur FTP (watts)
Mode HR : basé sur FC Max (réserve cardiaque)
"""

from app.engine.schemas import Zone

ZONE_DEFINITIONS = [
    ("Z1", "Récupération active",    0.00, 0.55),
    ("Z2", "Endurance",              0.55, 0.75),
    ("Z3", "Tempo",                  0.75, 0.87),
    ("Z4", "Seuil lactique",         0.87, 0.95),
    ("Z5", "VO2 Max",                0.95, 1.06),
    ("Z6", "Anaérobie",              1.06, 1.50),
]


def compute_power_zones(ftp: int) -> dict[str, Zone]:
    """Zones en watts depuis le FTP."""
    zones: dict[str, Zone] = {}
    for code, name, lower_pct, upper_pct in ZONE_DEFINITIONS:
        zones[code] = Zone(
            code=code,
            name=name,
            lower_pct=lower_pct,
            upper_pct=upper_pct,
            lower_watts=int(ftp * lower_pct),
            upper_watts=int(ftp * upper_pct),
            description_fr=_zone_description(code),
        )
    return zones


def compute_hr_zones(hr_max: int, hr_rest: int) -> dict[str, Zone]:
    """
    Zones en BPM via la méthode de la réserve cardiaque (Karvonen).
    FC cible = FC repos + %FCR × (FC max - FC repos)
    """
    fcr = hr_max - hr_rest  # Réserve cardiaque
    zones: dict[str, Zone] = {}

    HR_ZONE_DEFS = [
        ("Z1", "Récupération active",  0.00, 0.50),
        ("Z2", "Endurance",            0.50, 0.65),
        ("Z3", "Tempo",                0.65, 0.78),
        ("Z4", "Seuil lactique",       0.78, 0.88),
        ("Z5", "VO2 Max",              0.88, 0.95),
        ("Z6", "Anaérobie",            0.95, 1.00),
    ]

    for code, name, lower_pct, upper_pct in HR_ZONE_DEFS:
        lower_bpm = int(hr_rest + lower_pct * fcr)
        upper_bpm = int(hr_rest + upper_pct * fcr)
        zones[code] = Zone(
            code=code,
            name=name,
            lower_pct=lower_pct,
            upper_pct=upper_pct,
            lower_bpm=lower_bpm,
            upper_bpm=upper_bpm,
            description_fr=_zone_description(code),
        )
    return zones


def _zone_description(code: str) -> str:
    descriptions = {
        "Z1": "Récupération — effort très léger, conversation facile",
        "Z2": "Endurance fondamentale — effort confortable, base aérobie",
        "Z3": "Tempo — effort soutenu, respiration accélérée",
        "Z4": "Seuil — effort intense, difficile à maintenir > 1h",
        "Z5": "VO2 Max — effort très intense, intervalles courts",
        "Z6": "Anaérobie — effort maximal, sprints et accélérations",
    }
    return descriptions.get(code, "")
