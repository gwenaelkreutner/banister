**Rôle :** Tu es un Architecte Logiciel Expert en Python et en conception de systèmes scalables (Design Patterns, SOLID). 

**Contexte :** Je développe un générateur de plans d'entraînement sportifs. Actuellement, mon code souffre d'une logique trop impérative et couplée. Je souhaite refactoriser l'intégralité du projet pour adopter une architecture "State-of-the-Art" orientée données (Data-Driven). Le moteur de calcul doit devenir totalement agnostique du sport traité. Il ne doit manipuler que des "contraintes", des "budgets TSS" et des "scores de fatigue".

**Les 3 Piliers de la Nouvelle Architecture :**

1. **Séparation Stricte (Données vs Moteur) :**
   - Zéro règle métier en dur dans le moteur.
   - Utilisation de "Protocoles de Sport" (fichiers de config ou classes) contenant : les ratios de volume par discipline, les limiteurs physiques (ex: plafond de durée pour la course), et la bibliothèque de séances.

2. **Pipeline de Calcul Agnostique (en 3 étapes) :**
   - *Étape 1 - TSS Budgeting :* Calcul du budget TSS global et répartition par sport selon la phase d'entraînement.
   - *Étape 2 - Pattern Matching :* Sélection des séances dans la bibliothèque du sport concerné pour combler le budget (ex: piocher un 6x1000m pour 60 TSS en course).
   - *Étape 3 - Day Slotting (Placement Spatial) :* Remplacement de l'assignation basique par un algorithme basé sur un "score de charge résiduelle" (fatigue). Le moteur doit placer les séances très traumatisantes là où le score de fatigue accumulée est le plus bas.

3. **Abstraction et Polymorphisme :**
   - Implémentation d'une classe de base abstraite `SportLogic` (méthodes attendues : `get_weekly_template`, `get_intensity_constraints`, etc.).
   - Implémentation d'une classe concrète pour l'existant, par exemple `TriathlonLogic`.
   - Le moteur principal (`generate_plan`) doit uniquement instancier la bonne logique via une factory (`get_logic_for_sport`) et appeler des méthodes génériques.

**Mission :**
Voici mon code actuel ci-dessous. 
1. Analyse-le et identifie les zones de couplage fort et de logique impérative qui violent cette nouvelle architecture.
2. Propose un plan de refactoring étape par étape.
3. Génère le code refactorisé (architecture des dossiers, classes abstraites, classes concrètes de configuration, et le nouveau moteur agnostique). 

**Code source actuel :**
[INSERER TON CODE OU TES FICHIERS ICI]