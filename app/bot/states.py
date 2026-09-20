from aiogram.fsm.state import State, StatesGroup


class SetupStates(StatesGroup):
    # spec 007 : setup lit d'abord la source et la fait confirmer, puis ne demande
    # que ce qui ne peut pas être lu.
    CONFIRM_PROFILE = State()  # écran de confirmation des valeurs lues
    CORRECT_VALUE = State()    # l'athlète corrige une valeur lue
    GOAL = State()             # Objectif (event / fitness / performance / other)
    DATE = State()             # Date cible
    VOLUME = State()           # Heures/semaine voulues
    AVAILABLE_DAYS = State()  # Jours disponibles — aucune source ne le sait (found 2026-09-18 :
                               # c'était hardcodé mar/jeu/sam/dim pour tout le monde)
    CONSTRAINTS = State()      # Contraintes santé (rien qu'aucune source ne connaît)


class GoalStates(StatesGroup):
    """spec 007 /goal — changer d'objectif sans repasser tout le setup."""
    GOAL = State()
    DATE = State()
    CONFIRM_REGEN = State()  # plan actif existant : confirmation avant d'écraser (revu)


class ResetStates(StatesGroup):
    """spec 007 /reset — confirmation tapée avant toute suppression."""
    CONFIRM = State()


class PlanStates(StatesGroup):
    ACTIVE = State()               # Utilisation normale post-setup
    SUSPENDED = State()            # Suspendu (contraintes santé détectées)
    PENDING_MODIFICATION = State() # En attente de confirmation d'une modification de plan
