# Contract: First Run, Goals, and Coach Voice

**Feature**: 007-first-run-and-goals | **Date**: 2026-09-01

The interfaces that cross a boundary here are all **with the athlete** (Telegram flows) plus one with the
**source** (reading the athlete profile). No new outbound write path unless R3's endpoint probe passes.

---

## 1. Reading the athlete profile

`app/providers/intervals/athlete_profile.py::read_athlete_profile(client) -> ReadProfile`

Pure mapping from `GET /athlete/{id}` to the structure in [data-model.md](./data-model.md) §Read Profile.

- Threshold figures come from `sportSettings[]`, selecting the entry whose `types` contains a cycling type
  (`"Ride"`, `"VirtualRide"`, `"GravelRide"`, …). No cycling entry with an FTP ⇒ FTP absent, HR mode
  (FR-009).
- `icu_date_of_birth` ⇒ exact age. `icu_resting_hr` is the profile default and is labelled as such — the
  *measured* RHR series (spec 006) is separate and may be stale.
- Every field carries `origin="source"` and, where the source dates it, an `as_of`.
- A field the source does not have is returned **absent**, never defaulted (FR-008).

## 2. The confirmation screen (FR-002, FR-005, SC-003)

```
📋 Voici ce que je sais déjà de toi (lu depuis intervals.icu) :

  FTP              290 W          · Réglages sport
  LTHR             182 bpm        · Réglages sport
  FC max           202 bpm        · Réglages sport
  FC repos         65 bpm         · profil (⚠️ pas ta FC repos mesurée récente)
  Poids            67 kg
  Sexe / âge       H · 28 ans     · d'après ta date de naissance
  Forme actuelle   CTL 41 / ATL 49 / TSB -8   · calculé par intervals.icu, à jour au 01/09
  Volume récent    ~6 h/sem       · d'après tes 6 dernières semaines

  [ ✅ Tout est bon ]   [ ✏️ Corriger une valeur ]
```

### Rules

- **Every value shows where it came from** (SC-003). A value with no origin line is a bug.
- **Nothing is asked that appears here** (SC-002). Age is not asked — `FC max` is read directly.
- **`✏️ Corriger`** lists the editable values; picking one asks for the new value, then → §3.
- After confirmation, the flow asks **only**: goal type, goal date, intended weekly hours, health
  constraints (FR-003).

## 3. Correcting a read value (FR-006, FR-006a, FR-007)

```
Tu veux corriger la FTP : 290 W → 305 W.

Cette valeur vit dans ton compte intervals.icu, pas chez moi. Deux options :

 a) Tu la changes toi-même sur intervals.icu (Réglages → Sport → FTP), je relis ensuite.
 b) [si l'endpoint write-back est vérifié] Je la change pour toi maintenant — tu approuves
    ci-dessous et je note l'approbation avec l'écriture.

  [ 📝 Je le fais moi-même ]   [ ✅ Change-la (290 → 305) ]
```

### Rules

- **The athlete stating a value is not approval to write it** (FR-006a). The write, if offered at all,
  needs its own explicit tap, and the message shows *from what to what*.
- **An approved write-back records the approval** alongside it (FR-006b) — same shape as spec 005's
  `PublicationApproval`.
- **A refused or failed write-back changes nothing at the source and the coach keeps no divergent value**
  (FR-006c, FR-007): setup proceeds on the source's *current* number and states "j'utilise 290 W tant que
  intervals.icu n'est pas à jour".
- **Until the endpoint probe passes (R3), only option (a) exists.**

## 4. Goal change vs start over

### `/goal` (FR-011–FR-016, SC-005)

```
On change d'objectif. Je garde tout le reste — tes séances faites, ton historique, notre conversation.

Nouvel objectif ? [cyclosportive] [gran fondo] [perf] [forme] [autre]
Date ? …

→ (plan régénéré depuis ta forme actuelle : CTL 41)

✅ Nouveau plan — 14 semaines vers gran fondo le 12/04

Ce qui change : périodisation refaite, 3 blocs au lieu de 2.
Ce qui est gardé : 18 séances loggées, ton taux d'adhérence (78 %), tes réglages.
⚠️ 4 séances déjà publiées dans ton calendrier ne correspondent plus au nouveau plan
   — relance /publish pour re-synchroniser.
```

- Re-reads the source silently (no confirmation screen — the athlete just did that, or it hasn't changed).
- Asks goal + date only; volume only if the athlete says they want to change it (FR-013).
- Regenerates from `get_current_fitness()`, never from zero (FR-012).
- Runs spec 005's `check_divergence` and surfaces stale calendar entries (FR-014).
- The "what changed / what carried over" summary is mandatory (FR-016).

### `/reset` (FR-017–FR-020, SC-006, SC-007)

```
⚠️ Recommencer de zéro. Voici ce qui sera SUPPRIMÉ définitivement :

  • 18 séances enregistrées
  • 142 messages de conversation
  • ton historique d'adhérence (11 semaines)
  • ton plan actif et ton profil

Ce qui n'est PAS touché : ton compte intervals.icu, tes activités là-bas, ta voix de coach.

Pour confirmer, écris exactement : SUPPRIMER
```

- **A distinct command** from `/goal` (FR-017). `/setup` still means "reconfigure everything".
- **Lists specifics before anything happens** (FR-018) — real counts, not "your data".
- **Typed confirmation**; anything else discards nothing (FR-019, SC-006).
- **intervals.icu is never touched** (FR-020, SC-007) — `/reset` only ever issues local `DELETE`s.
- `coach_voice` and `disclaimer_acknowledged_at` survive (identity, not training data).

## 5. Coach voice (FR-021–FR-026, SC-008)

### `/voice`

```
🗣️ Voix du coach

  ▸ Pace        pote expert — direct, chaleureux, tutoie      (actuelle)
    Analyste    métriques d'abord, zéro fioriture
    Zen         calme, encourageant, sans pression
    Coach       expert technique, franc, anglais

  Choisis :  [Pace] [Analyste] [Zen] [Coach]
```

- Lists every `personas/*.yaml` with its `name` and `voice` descriptor (FR-022).
- Selecting one writes `users.coach_voice` and confirms; the **next** coach message uses it (FR-023) — no
  restart (the column is read per request).
- Available at any time, not only in setup (FR-021).
- `None` ⇒ `settings.persona` (FR-024). Unresolvable id ⇒ `coach-default` + a one-line "(voix « X »
  introuvable, je reprends la voix par défaut)" (FR-026).
- Changing the voice writes **no** existing record (FR-025) — it is a single `UPDATE users`.

## 6. The disclaimer (FR-027, FR-028)

- Sent once, from `_finalize_setup` / `/goal`, **only when `users.disclaimer_acknowledged_at` is null**;
  then that column is set.
- Text is `prompts.DISCLAIMER_TEXT` (already shipped, spec 006). Not repeated on subsequent interactions
  (FR-028) — it is never sent from the chat path.

## 7. Guarantees

| Guarantee | Requirement |
|---|---|
| Every readable attribute is read, not asked | FR-001, SC-002 |
| Every confirmed value shows origin (+ age where known) | FR-005, SC-003 |
| A correction is written to the source or the athlete is sent there — never a local override | FR-006, FR-007, SC-004 |
| A source write-back needs its own explicit approval, recorded | FR-006a, FR-006b |
| Missing source value ⇒ asked, never defaulted silently | FR-008 |
| No measured threshold ⇒ proceed on what exists, state the basis | FR-009, FR-010 |
| Goal change preserves sessions / adherence / conversation entirely | FR-011, SC-005 |
| Plan after a goal change starts from current fitness | FR-012 |
| Goal change does not re-ask unchanged info | FR-013 |
| Stale published calendar entries after a goal change are surfaced | FR-014 |
| Past / unplannable goal dates are rejected or challenged | FR-015 |
| `/reset` is distinct, itemised, confirmed, complete, and never touches the source | FR-017–FR-020, SC-006, SC-007 |
| Voice is athlete state, persists, takes effect next message, falls back safely | FR-021–FR-026, SC-008 |
| Disclaimer precedes first advice, shown once | FR-027, FR-028, SC-009 |
