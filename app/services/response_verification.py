"""Verify that what a coaching response says about the athlete's numbers is true
(spec 006 US3, FR-017–FR-021).

A `MetricRegistry` records exactly which metric values were put in front of the model
while the context was built — the definition of "retrieved". After generation, the
response is scanned for `(metric term, number)` pairs and each is checked against the
registry: a mismatch or a value stated for an unretrieved metric is a failure, the
claim's sentence is withheld (not the whole response — spec 006 research R6), and the
failure is recorded so its frequency is measurable (FR-021).

Deterministic, no LLM call (FR-022, SC-010). Verification is **keyword-anchored** rather
than "extract every number" — a real coaching reply carries mostly durations and zone
codes, and an unanchored checker would raise a dozen false alarms to catch two real
claims (spec 006 research R5).

Known limit (recorded, not hidden): anchoring trades recall for precision. A fabricated
number phrased with no metric word nearby passes. That is the deliberate trade — a
verifier the athlete trusts beats one that catches every case and is ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.localization import Language, t
from app.engine.guardrail_thresholds import VERIFY_TOLERANCE_PCT

# ── What "retrieved" means ───────────────────────────────────────────────────


@dataclass
class MetricRegistry:
    """`{canonical_name: value}` — every metric value the context assembly put in front
    of the model. The single source of truth for FR-018 / FR-020: a metric absent here
    is one the response may not state a value for."""

    _values: dict[str, float] = field(default_factory=dict)
    _history: dict[str, dict[str, set[float]]] = field(default_factory=dict)

    def register(self, name: str, value: float | int | None) -> None:
        if value is None:
            return
        self._values[name.lower()] = float(value)

    def get(self, name: str) -> float | None:
        return self._values.get(name.lower())

    def register_history(self, name: str, values_by_date: dict[str, float | int | None]) -> None:
        """Register dated tool results without weakening verification of current values."""
        canonical = name.lower()
        history = self._history.setdefault(canonical, {})
        for as_of, value in values_by_date.items():
            if value is not None:
                history.setdefault(as_of, set()).add(float(value))

    def values_for(self, name: str, *, sentence: str = "") -> set[float]:
        """Current value always applies. Historical values require their date in prose."""
        canonical = name.lower()
        values: set[float] = set()
        if canonical in self._values:
            values.add(self._values[canonical])
        for as_of, dated_values in self._history.get(canonical, {}).items():
            short_date = f"{as_of[8:10]}/{as_of[5:7]}"
            if as_of in sentence or short_date in sentence:
                values.update(dated_values)
        return values

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._values

    def names(self) -> set[str]:
        return set(self._values)


# ── Anchoring: metric term -> canonical registry name ────────────────────────
#
# A number is a claim to be checked ONLY when it sits next to one of these terms
# (contracts/guardrails.md §3). Order matters: longer / more specific terms first so
# "fc de repos" is matched before a bare "fc".

_ANCHORS: list[tuple[str, str]] = [
    (r"fc\s+de\s+repos", "rhr"),
    (r"fr[ée]quence\s+cardiaque\s+de\s+repos", "rhr"),
    (r"fc\s+repos", "rhr"),
    (r"resting\s+heart\s+rate", "rhr"),
    (r"resting\s+hr", "rhr"),
    (r"variabilit[ée]\s+cardiaque", "hrv"),
    (r"heart\s+rate\s+variability", "hrv"),
    (r"\bvfc\b", "hrv"),
    (r"\bhrv\b", "hrv"),
    (r"\bctl\b", "ctl"),
    (r"\batl\b", "atl"),
    (r"\btsb\b", "tsb"),
    (r"\btss\b", "tss"),
    (r"\bftp\b", "ftp"),
    (r"rapport\s+aigu[\s/-]*chronique", "acwr"),
    (r"ratio\s+aigu[\s/-]*chronique", "acwr"),
    (r"acute[\s/-]*chronic\s+(?:workload\s+)?ratio", "acwr"),
    (r"\bacwr\b", "acwr"),
    (r"monotonie", "monotony"),
    (r"(?:training\s+)?monotony", "monotony"),
    (r"rampe?\s+de\s+charge", "ramp_rate"),
    (r"load\s+ramp", "ramp_rate"),
    (r"ramp\s*rate", "ramp_rate"),
    (r"indice de r[ée]cup[ée]ration", "recovery_index"),
    (r"recovery\s+index", "recovery_index"),
    (r"indice de polarisation", "polarization_index"),
    (r"polarization\s+index", "polarization_index"),
]

# A number token: optional sign, digits, optional decimal (French comma or dot).
_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"
_DATE_TOKEN = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b")

# Numbers we never treat as metric claims even inside an anchored clause: durations,
# zones, cadence, percentages (a "-20%" is a relative statement, not an absolute value
# the registry could hold).
_NON_CLAIM_AFTER = re.compile(
    rf"(?:{_NUMBER})\s*(?:%|h\d*|h\b|mins?\b|minutes?\b|hours?\b|rpm\b|s\b|sec\b|seconds?\b|semaines?\b|weeks?\b|jours?\b|days?\b)",
    re.IGNORECASE,
)
_ZONE = re.compile(r"\bZ[1-7]\b", re.IGNORECASE)

# Clause boundaries — a claim is a term and a number in the *same* clause. A period or
# comma *between digits* is a decimal separator, not a boundary.
_CLAUSE_SPLIT = re.compile(r"(?<!\d)\.(?!\d)|[\n;()—]|(?<!\d),(?!\d)| - ")
# Sentence boundaries, for withholding (a whole sentence, not a fragment). The trailing
# \s+ already means "45.9" never splits — there is no space inside a decimal.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Claim:
    metric: str          # canonical registry name
    stated_text: str      # the number exactly as written
    stated_value: float
    sentence: str          # the sentence it appears in (for withholding + the record)


@dataclass
class VerificationResult:
    passed: list[Claim] = field(default_factory=list)
    mismatches: list[tuple[Claim, float]] = field(default_factory=list)  # (claim, expected)
    unretrieved: list[Claim] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.unretrieved

    @property
    def failures(self) -> list[Claim]:
        return [c for c, _ in self.mismatches] + self.unretrieved


def _to_float(token: str) -> float:
    return float(token.replace(",", ".").replace("+", ""))


def _extract_claims(text: str) -> list[Claim]:
    claims: list[Claim] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        for clause in _CLAUSE_SPLIT.split(sentence):
            low = clause.lower()
            anchors_here = [
                (canon, m.end())
                for pat, canon in _ANCHORS
                for m in re.finditer(pat, low)
            ]
            if not anchors_here:
                continue
            date_spans = [match.span() for match in _DATE_TOKEN.finditer(clause)]
            seen: set[str] = set()
            for num_m in re.finditer(_NUMBER, clause):
                start, end = num_m.span()
                if any(start >= d_start and end <= d_end for d_start, d_end in date_spans):
                    continue
                # Skip durations / zones / percentages / cadence.
                if _NON_CLAIM_AFTER.match(clause[start:]):
                    continue
                if _ZONE.search(clause[max(0, start - 2):end + 2]):
                    continue
                token = num_m.group()
                if token in seen:
                    continue
                seen.add(token)
                # Anchor to the nearest preceding term, else the nearest term.
                canon = min(
                    anchors_here,
                    key=lambda a: (abs(a[1] - start), 0 if a[1] <= start else 1),
                )[0]
                claims.append(
                    Claim(
                        metric=canon,
                        stated_text=token,
                        stated_value=_to_float(token),
                        sentence=sentence,
                    )
                )
    return claims


def verify_response(text: str, registry: MetricRegistry) -> VerificationResult:
    """Scan `text` for anchored metric claims and check each against `registry`
    (contracts §3). Deterministic — no clock, no randomness, no LLM."""
    result = VerificationResult()
    for claim in _extract_claims(text):
        expected_values = registry.values_for(claim.metric, sentence=claim.sentence)
        if not expected_values:
            result.unretrieved.append(claim)
            continue
        expected = min(expected_values, key=lambda value: abs(claim.stated_value - value))
        tol = max(abs(expected) * VERIFY_TOLERANCE_PCT / 100.0, 0.5)
        if abs(claim.stated_value - expected) <= tol:
            result.passed.append(claim)
        else:
            result.mismatches.append((claim, expected))
    return result


def apply_result(
    text: str,
    result: VerificationResult,
    *,
    language: Language | None = None,
) -> str:
    """Replace every sentence carrying a failed claim with an honest omission (FR-019,
    research R6). Never rewrites around a corrected number, never drops the whole
    response. A passing response is returned unchanged."""
    if result.ok:
        return text
    bad_sentences = {c.sentence for c in result.failures}
    metric_by_sentence: dict[str, str] = {}
    for c in result.failures:
        metric_by_sentence.setdefault(
            c.sentence,
            t(f"metric.{c.metric}", language=language),
        )

    out: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        stripped = sentence.strip()
        match = next((b for b in bad_sentences if b and b in stripped), None)
        if match is not None:
            out.append(
                t("verification.uncertain_metric", language=language,
                  metric=metric_by_sentence[match])
            )
        elif stripped:
            out.append(stripped)
    return " ".join(out)
