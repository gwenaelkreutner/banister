from aiogram.fsm.state import State, StatesGroup


class SetupStates(StatesGroup):
    SPORT = State()     # Quel sport ?
    GOAL = State()      # Objectif (event / fitness / performance / other)
    DATE = State()      # Date cible
    VOLUME = State()    # Heures/semaine
    POWER = State()     # Capteur de puissance → FTP ou FC max
    AGE = State()       # Âge


class PlanStates(StatesGroup):
    ACTIVE = State()               # Utilisation normale post-setup
    SUSPENDED = State()            # Suspendu (contraintes santé détectées)
    PENDING_MODIFICATION = State() # En attente de confirmation d'une modification de plan
