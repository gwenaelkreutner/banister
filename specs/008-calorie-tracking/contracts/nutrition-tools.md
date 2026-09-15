# Contract: Nutrition LLM Tools

**Feature**: 008-calorie-tracking | **Date**: 2026-09-15

Three interfaces cross a boundary in this feature, the same three kinds spec 006's contract names for
guardrails: what the **model is told it may do** (the tool schema — a contract with the LLM), what the
**tool hands back** (a contract with the coach's next sentence — SC-003's "always an estimate" lives here),
and what's **deliberately out of reach** (no calorie figure this feature produces is checked by
`response_verification.py` — research R3).

---

## 1. `log_meal`

```json
{
  "type": "function",
  "function": {
    "name": "log_meal",
    "description": "Enregistre ce que l'athlète a mangé — un repas isolé ou un récap de toute la journée — avec ton estimation du nombre de calories. Utilise cet outil dès que l'athlète décrit un aliment ou un repas qu'il a réellement consommé (pas une question hypothétique, pas une demande de conseil nutritionnel). N'invente jamais une estimation pour un texte qui ne décrit pas de la nourriture. Précise toujours dans ta réponse qu'il s'agit d'une ESTIMATION, jamais d'une mesure précise.",
    "parameters": {
      "type": "object",
      "properties": {
        "entry_type": {
          "type": "string",
          "enum": ["meal", "day_recap"],
          "description": "meal = un repas ou une collation isolée. day_recap = un résumé de tout ce qui a été mangé dans la journée en un seul message."
        },
        "meal_slot": {
          "type": "string",
          "enum": ["breakfast", "lunch", "dinner", "snack", "other"],
          "description": "Uniquement si entry_type=meal et que le moment du repas est clair. Ne pas fournir si incertain ou si entry_type=day_recap."
        },
        "estimated_calories": {
          "type": "integer",
          "description": "Ton estimation du nombre de calories pour CETTE entrée (ce repas, ou le total de la journée si day_recap).",
          "minimum": 1,
          "maximum": 8000
        },
        "days_ago": {
          "type": "integer",
          "description": "0 = aujourd'hui (défaut), 1 = hier, 2 = avant-hier. Utilise si l'athlète parle d'un repas passé.",
          "minimum": 0,
          "maximum": 2
        }
      },
      "required": ["entry_type", "estimated_calories"]
    }
  }
}
```

**Tool result** (returned to the model, not shown raw to the athlete):

```json
{
  "ok": true,
  "estimated_calories": 650,
  "entry_date": "2026-09-15",
  "day_total_estimated_calories": 1450,
  "replaced_existing_entries": false
}
```

or, when the executor rejects the call (implausible or missing estimate):

```json
{"ok": false, "error": "estimation calorique manquante ou hors limites plausibles — rien n'a été enregistré"}
```

### Rules

- **`day_total_estimated_calories` is always the deterministic sum from the repository after this insert**,
  never the model re-adding numbers itself (research R2) — the coach's next sentence should quote this
  figure, not compute one.
- **`entry_type="day_recap"` replaces that day's existing entries** (FR-009) before inserting — the result's
  `replaced_existing_entries: true` is the signal the coach must surface ("j'ai remplacé ce que tu avais
  loggé plus tôt aujourd'hui par ce récap"), never applied silently.
- **Every figure reported back to the athlete must be phrased as an estimate** (FR-005, SC-003) — enforced
  by the tool description reaching the model, not by any downstream check (research R3: this is out of
  `response_verification.py`'s scope by design).
- **A rejected call must be reported as "not recorded," never as a silent success or a fabricated number**
  (FR-011).

---

## 2. `undo_last_meal_entry`

```json
{
  "type": "function",
  "function": {
    "name": "undo_last_meal_entry",
    "description": "Supprime la toute dernière entrée calorique enregistrée AUJOURD'HUI. Utilise cet outil uniquement quand l'athlète signale explicitement une erreur de saisie récente (mauvais aliment, mauvaise quantité, entrée en double). Ne s'applique jamais à un jour autre qu'aujourd'hui.",
    "parameters": {
      "type": "object",
      "properties": {},
      "required": []
    }
  }
}
```

**Tool result**:

```json
{"ok": true, "removed_estimated_calories": 650, "entry_date": "2026-09-15", "day_total_estimated_calories": 800}
```

or, if nothing was logged today:

```json
{"ok": false, "error": "aucune entrée aujourd'hui à annuler"}
```

### Rules

- Removes exactly one row: the most recently created entry for today (`created_at` descending), regardless
  of `entry_type` — including a `day_recap` row, which does **not** restore the individual entries it had
  replaced (data-model.md §"What this feature does not model" — accepted, stated risk).
- Scoped to today only, by design — a same-session correction, not a general edit history. Correcting a
  past day is out of scope for this version (spec doesn't ask for it).

---

## 3. `get_calorie_history`

```json
{
  "type": "function",
  "function": {
    "name": "get_calorie_history",
    "description": "Récupère le total calorique estimé jour par jour sur une période récente. Utilise cet outil quand l'athlète demande son historique, sa consommation d'un jour précis, ou une tendance récente.",
    "parameters": {
      "type": "object",
      "properties": {
        "days": {
          "type": "integer",
          "description": "Nombre de jours à couvrir, en remontant depuis aujourd'hui (1 à 30).",
          "minimum": 1,
          "maximum": 30
        }
      },
      "required": ["days"]
    }
  }
}
```

**Tool result** — every day in the requested range is present, logged or not (FR-008: "nothing logged" must
be representable and distinct from a logged zero):

```json
{
  "range_start": "2026-09-09",
  "range_end": "2026-09-15",
  "days": [
    {"date": "2026-09-09", "logged": true, "total_calories": 2100, "entry_count": 3},
    {"date": "2026-09-10", "logged": false},
    {"date": "2026-09-11", "logged": true, "total_calories": 1950, "entry_count": 2}
  ]
}
```

### Rules

- A day absent from logging is returned with `"logged": false` and **no** `total_calories` key — the coach
  must say "rien loggé le 10" rather than "0 kcal le 10" (FR-008, SC-... none directly, but this is the
  mechanism SC-004's "nothing logged ≠ zero" distinction runs on).

---

## Out of scope for this contract

- No tool edits a specific past entry by id — only `undo_last_meal_entry` (today, latest) and the
  replace-on-`day_recap` path (FR-009) can remove data.
- No tool reads or writes macro breakdown, calorie targets, or anything training-related (spec Scope).
- No figure this feature produces is added to `MetricRegistry` / checked by `verify_response()` (research
  R3) — this is a deliberate absence, not an oversight, and should stay that way unless FR-013's "standalone
  for now" is revisited.
