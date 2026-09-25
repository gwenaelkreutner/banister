"""Response verification (spec 006 US3, FR-017–FR-021).

Deterministic, no DB, no LLM. The keyword-anchoring precision is what these tests
protect — research R5 measured a real reply with fourteen numerals of which twelve were
durations and zone codes, and a checker that flags those gets switched off.
"""
from __future__ import annotations

from app.services.response_verification import (
    MetricRegistry,
    apply_result,
    verify_response,
)


def _registry(**kw) -> MetricRegistry:
    r = MetricRegistry()
    for k, v in kw.items():
        r.register(k, v)
    return r


# ── the registry ────────────────────────────────────────────────────────────


def test_registry_ignores_none_and_is_case_insensitive():
    r = MetricRegistry()
    r.register("CTL", 45.9)
    r.register("atl", None)
    assert r.get("ctl") == 45.9
    assert "ctl" in r
    assert r.get("atl") is None and "atl" not in r


# ── a matching claim passes, display rounding included ───────────────────────


def test_exact_match_passes():
    r = _registry(ctl=45.9, tsb=-18.8)
    res = verify_response("Ton CTL est à 45.9 et ton TSB à -18.8.", r)
    assert res.ok and len(res.passed) == 2


def test_display_rounding_is_not_a_lie():
    r = _registry(ctl=45.9)
    res = verify_response("Ta forme de fond : CTL autour de 46.", r)
    assert res.ok


def test_recovery_index_anchor_matches():
    """2026-09-21 — recovery_index was addable to the registry with nothing anchoring it
    in _ANCHORS; this pins the anchor exists and resolves to the right metric name."""
    r = _registry(recovery_index=0.85)
    res = verify_response("Ton indice de récupération est à 0.85 aujourd'hui.", r)
    assert res.ok and len(res.passed) == 1
    assert res.passed[0].metric == "recovery_index"


# ── a mismatch is flagged ───────────────────────────────────────────────────


def test_value_disagreeing_with_the_registry_is_a_mismatch():
    r = _registry(ctl=45.9)
    res = verify_response("Ton CTL est à 62.", r)
    assert not res.ok
    assert len(res.mismatches) == 1
    claim, expected = res.mismatches[0]
    assert claim.metric == "ctl" and claim.stated_text == "62" and expected == 45.9


# ── a value for an unretrieved metric is flagged (FR-018) ────────────────────


def test_value_for_a_metric_never_retrieved_is_unretrieved():
    r = _registry(ctl=45.9)  # no FTP registered
    res = verify_response("Ta FTP est de 250 W.", r)
    assert not res.ok
    assert len(res.unretrieved) == 1 and res.unretrieved[0].metric == "ftp"


def test_historical_value_requires_its_date_and_keeps_current_value_authoritative():
    r = _registry(ctl=54)
    r.register_history("ctl", {"2026-09-01": 48})

    assert verify_response("Ton CTL était à 48 le 01/09.", r).ok
    assert not verify_response("Ton CTL actuel est à 48.", r).ok


# ── the precision guarantee: prose is not flagged (research R5) ──────────────


def test_durations_and_zones_are_not_claims():
    r = _registry(ctl=45.9)
    text = (
        "Samedi : la séance endurance passe de 3h15 en Z3 à 2h15 en Z2. "
        "Dimanche : la sortie longue passe de 3h30 en Z2 à 2h30 en Z1. "
        "Tu gardes 2 séances de qualité cette semaine."
    )
    res = verify_response(text, r)
    assert res.ok  # zero claims, zero false alarms


def test_percentage_relative_statements_are_not_claims():
    r = _registry(hrv=45.0)
    res = verify_response("Ta VFC est 20% sous ta normale — journée facile.", r)
    assert res.ok  # "-20%" is relative, not an absolute value the registry holds


def test_a_bare_number_with_no_metric_term_is_not_a_claim():
    r = _registry(ctl=45.9)
    res = verify_response("Tu as fait 3 sorties cette semaine, bravo.", r)
    assert res.ok


# ── withholding: the claim's sentence, not the whole response (research R6) ───


def test_a_failed_claim_withholds_its_sentence_and_keeps_the_rest():
    r = _registry(ctl=45.9)
    text = "Ton CTL est à 62. Continue comme ça, tu progresses bien."
    res = verify_response(text, r)
    out = apply_result(text, res, language="fr")
    assert "62" not in out
    assert "progresses bien" in out
    assert (
        "(je préfère ne pas avancer de chiffre sur ta forme de fond ici "
        "— je n'en suis pas certain)"
    ) in out


def test_english_mismatch_withholds_only_the_false_metric_sentence():
    r = _registry(rhr=52)
    text = "Your resting heart rate is 68. Keep the next ride easy."
    res = verify_response(text, r)

    assert len(res.mismatches) == 1
    assert res.mismatches[0][0].metric == "rhr"
    out = apply_result(text, res, language="en")
    assert "68" not in out
    assert "Keep the next ride easy." in out
    assert (
        "(I prefer not to give a number for your resting heart rate here "
        "— I'm not certain)"
    ) in out


def test_english_unretrieved_metric_uses_english_fallback():
    r = _registry(ctl=45.9)
    text = "Your recovery index is 0.85 today. Ride if you feel ready."
    res = verify_response(text, r)

    assert len(res.unretrieved) == 1
    assert res.unretrieved[0].metric == "recovery_index"
    out = apply_result(text, res, language="en")
    assert "0.85" not in out
    assert "Ride if you feel ready." in out
    assert "your recovery index" in out


def test_english_metric_words_anchor_claims_and_ignore_duration():
    r = _registry(acwr=1.2, monotony=1.5, hrv=48)
    text = (
        "Your acute/chronic workload ratio is 1.2, training monotony is 1.5, "
        "and heart rate variability is 48. Ride for 2 hours."
    )
    res = verify_response(text, r)
    assert res.ok
    assert {claim.metric for claim in res.passed} == {"acwr", "monotony", "hrv"}


def test_a_clean_response_is_returned_unchanged():
    r = _registry(ctl=45.9)
    text = "Ton CTL est à 45.9, belle forme."
    res = verify_response(text, r)
    assert apply_result(text, res) == text
