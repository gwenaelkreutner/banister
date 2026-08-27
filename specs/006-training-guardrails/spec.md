# Feature Specification: Training Guardrails

**Feature Branch**: `006-training-guardrails`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: Give the coach the ability to notice when the athlete is heading somewhere bad —
ramping load too fast, training while unrecovered, or grinding a monotonous week — and to say so before it
becomes an injury or an illness. Additionally, verify that what the coach says about the athlete's numbers
is actually true.

## Scope

**In scope**: computing workload-progression and recovery signals from data already captured, evaluating
them against explicit published thresholds, surfacing what they mean, verifying coaching responses against
the values they claim to cite, and stating plainly what this product is not.

**Out of scope**: diagnosing anything. This feature raises signals and offers training adjustments; it does
not identify illness, injury, or medical conditions, and must not present itself as capable of doing so.
Also excluded: acquiring new data — the signals here are computed from what is already captured.

**Depends on**: the wellness capture specified for the training data source. Recovery signals cannot be
evaluated before heart rate variability, resting heart rate and sleep are being retained.

**Inherited and not re-argued here**: spec 001 required workload-ratio evaluation, explicit readiness
thresholds, a validation checklist before coaching responses, and a disclaimer. This specification says how
each behaves.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The athlete is warned before digging the hole (Priority: P1)

The athlete has been building for three weeks and feels good, so they keep pushing. Before the session that
would tip them over, the coach tells them their load is climbing faster than their body is absorbing it,
says by how much, and offers a way to keep the benefit without the risk. The athlete decides knowing what
they are deciding.

**Why this priority**: Preventing the athlete from wrecking their own season is the highest-value thing a
coach does, and it is the thing an enthusiastic athlete cannot do for themselves. This is the entire reason
the feature exists.

**Independent Test**: Feed a load history that ramps beyond the safe range and confirm the coach raises it
before prescribing more load, with the figure stated.

**Acceptance Scenarios**:

1. **Given** the athlete's recent load has risen sharply relative to what they have absorbed over time,
   **When** the coach next discusses training, **Then** it states the ratio, says what range is considered
   safe, and explains what the athlete's value means.
2. **Given** the ratio is within the safe range, **When** the coach discusses training, **Then** it does not
   manufacture a warning.
3. **Given** the ratio is far outside the safe range, **When** the coach recommends anything, **Then** its
   recommendation reduces rather than increases load.
4. **Given** a week whose training is unusually uniform in load, **When** the coach reviews it, **Then** it
   raises the lack of variation as a risk in its own right.
5. **Given** any signal is raised, **When** it is presented, **Then** the athlete is told what to do about
   it, not merely that a number is bad.

---

### User Story 2 - The coach notices the athlete is not recovered (Priority: P1)

The athlete slept badly, their heart rate variability is well below normal and their resting heart rate is
up. The plan says intervals. The coach opens by saying their recovery signals are down, says which ones and
by how much, and suggests an easy day instead.

**Why this priority**: This is the capability the migration to the new data source unlocked and that the
previous source could never support. Training hard while unrecovered is how athletes get injured and ill,
and the athlete usually cannot feel it until afterwards.

**Independent Test**: Supply wellness data below the athlete's established baseline and confirm the coach
raises it, naming the signal and the deviation.

**Acceptance Scenarios**:

1. **Given** heart rate variability has fallen more than twenty percent below the athlete's baseline,
   **When** the coach discusses today's training, **Then** it directs an easy day and says why.
2. **Given** resting heart rate is five beats per minute or more above baseline, **When** the coach
   discusses today's training, **Then** it raises a fatigue signal and says why.
3. **Given** several recovery signals are simultaneously poor, **When** the coach responds, **Then** it
   treats the combination as more significant than any one alone.
4. **Given** recovery signals are normal, **When** the coach responds, **Then** it does not raise a warning.
5. **Given** a signal is raised, **When** it is presented, **Then** the athlete is told the observed value,
   their baseline, and the threshold that was crossed.
6. **Given** recovery signals are poor and the plan prescribes a hard session, **When** the athlete asks
   what to do, **Then** the conflict is stated openly rather than resolved silently.

---

### User Story 3 - The coach's numbers are true (Priority: P1)

Whatever the coach states about the athlete's training — a load value, a fitness figure, a ratio, a
percentage — is what the data actually says. It never rounds to something more persuasive, never fills a
gap with a plausible figure, and never cites a metric it was not given.

**Why this priority**: A coach that occasionally invents a number is worse than one that says less, because
the athlete cannot tell which numbers to trust and must therefore distrust all of them. Every guardrail in
this specification is worthless if its figures might be fabricated.

**Independent Test**: Generate coaching responses across many situations and check every figure they state
against the retrieved data.

**Acceptance Scenarios**:

1. **Given** a coaching response citing a training metric, **When** the stated value is compared with the
   retrieved value, **Then** they agree.
2. **Given** a metric that was not retrieved, **When** the coach responds, **Then** it does not state a
   value for it.
3. **Given** a response that would state a value disagreeing with the data, **When** it is checked before
   delivery, **Then** it is corrected or withheld rather than sent.
4. **Given** required data could not be retrieved, **When** the coach responds, **Then** it says so rather
   than answering as though it had the data.
5. **Given** a response is checked, **When** the check fails, **Then** the failure is recorded so the
   frequency of such failures is measurable.

---

### User Story 4 - Guardrails advise; they do not seize control (Priority: P2)

A guardrail fires. The coach explains it and recommends a change. The athlete's plan is not silently
rewritten, their calendar is not silently altered, and their session is not cancelled behind their back.
The athlete remains the one who decides.

**Why this priority**: A guardrail that acts unilaterally is indistinguishable from a malfunction when it is
wrong, and it will sometimes be wrong. It is P2 because it constrains behaviour introduced by the stories
above rather than adding capability.

**Independent Test**: Trigger every guardrail and confirm none mutates the plan or the calendar without the
athlete agreeing.

**Acceptance Scenarios**:

1. **Given** any guardrail fires, **When** it fires, **Then** no plan is modified and no calendar entry is
   changed without the athlete's agreement.
2. **Given** a guardrail recommends a change, **When** the athlete accepts, **Then** the change is applied
   through the same approval path as any other change.
3. **Given** a guardrail recommends a change, **When** the athlete declines, **Then** the plan stands and
   they are not nagged repeatedly for the same occurrence.
4. **Given** the athlete repeatedly overrides the same guardrail, **When** they do, **Then** the coach
   continues to state the signal without escalating into obstruction.

---

### User Story 5 - Guardrails stay quiet when they cannot know (Priority: P2)

A new athlete has two weeks of history. The coach does not tell them their heart rate variability is down
twenty percent from a baseline computed on four days of data. It says it does not yet know their normal, and
waits until it does.

**Why this priority**: A guardrail that fires on noise trains the athlete to ignore it, which destroys the
one that matters. False alarms are more damaging here than silence, because silence is honest.

**Independent Test**: Supply insufficient history and confirm no signal fires, with the insufficiency
stated.

**Acceptance Scenarios**:

1. **Given** insufficient history to establish a baseline, **When** a signal would otherwise be evaluated,
   **Then** it is not raised and the insufficiency is stated if relevant.
2. **Given** a signal requires data that is missing for the period, **When** it is evaluated, **Then** the
   absence is treated as unknown rather than as a normal value.
3. **Given** a baseline exists, **When** it is used, **Then** it reflects the athlete's own history rather
   than a population assumption.
4. **Given** history becomes sufficient, **When** the threshold is next evaluated, **Then** the signal
   becomes active without the athlete having to do anything.

---

### User Story 6 - The athlete knows what this is not (Priority: P3)

The athlete understands that the coach is software, that a proposed session is a suggestion, and that
recovery signals are not a medical opinion. If something suggests they should see a doctor, they are told to
see a doctor rather than being coached through it.

**Why this priority**: Necessary and non-negotiable, but it protects against misuse rather than delivering
capability. It is P3 in build order, not in importance.

**Independent Test**: Confirm the disclaimer is presented at first use and in documentation, and that
health-suggestive situations produce a referral rather than advice.

**Acceptance Scenarios**:

1. **Given** the athlete's first use, **When** they begin, **Then** they are told this is neither a
   physician nor a certified coach and that sessions are suggestions.
2. **Given** signals consistent with illness rather than training fatigue, **When** the coach responds,
   **Then** it suggests they seek qualified advice rather than prescribing training through it.
3. **Given** the athlete describes pain or injury, **When** the coach responds, **Then** it does not offer a
   diagnosis.

### Edge Cases

- The athlete's baseline shifts genuinely, through fitness gains, altitude, illness recovery, or seasonal
  change, and a fixed baseline would misread the new normal as a deviation.
- A single wildly wrong wellness reading from a device error would trip a threshold on its own.
- The athlete trains deliberately hard in a planned overload block, where a high ratio is intended rather
  than accidental.
- A planned taper produces a falling ratio that must not be read as detraining.
- Recovery signals are poor for a reason unrelated to training, such as a newborn, travel, or a hangover.
- Wellness data exists for some days and not others within the same evaluation window.
- The athlete has no power meter, so load history is derived from a different measure throughout.
- A long layoff makes the chronic baseline very low, so a normal first session produces an alarming ratio.
- Two guardrails fire simultaneously with contradictory implications.
- The athlete asks the coach to disable a guardrail entirely.
- Wellness data arrives late, after the day it describes has already been coached.

## Requirements *(mandatory)*

### Functional Requirements

#### Workload progression

- **FR-001**: The system MUST compute the ratio of the athlete's recent training load to the load they have
  absorbed over a longer preceding period.
- **FR-002**: The system MUST evaluate that ratio against a documented range and MUST state both the value
  and the range when raising it.
- **FR-003**: When the ratio is above the documented range, recommendations MUST reduce rather than increase
  load.
- **FR-004**: The system MUST evaluate how uniform the athlete's training load has been and MUST raise
  insufficient variation as a risk in its own right.
- **FR-005**: The system MUST NOT raise a workload signal when values are within the documented range.

#### Recovery readiness

- **FR-006**: The system MUST establish baselines for recovery signals from the athlete's own history rather
  than from population assumptions.
- **FR-007**: The system MUST direct an easy day when heart rate variability falls more than twenty percent
  below baseline.
- **FR-008**: The system MUST raise a fatigue signal when resting heart rate is five beats per minute or
  more above baseline.
- **FR-009**: The system MUST treat multiple simultaneously poor recovery signals as more significant than
  any one alone.
- **FR-010**: When raising a recovery signal, the system MUST state the observed value, the baseline, and
  the threshold crossed.
- **FR-011**: The system MUST NOT allow a single anomalous reading to trigger a signal on its own.
- **FR-012**: When recovery signals conflict with what the plan prescribes, the system MUST state the
  conflict openly rather than resolving it silently.

#### Sufficiency and honesty

- **FR-013**: The system MUST NOT evaluate a threshold before enough history exists to establish a
  meaningful baseline, and MUST state the insufficiency where relevant.
- **FR-014**: Missing data MUST be treated as unknown and MUST NOT be substituted with a normal or default
  value.
- **FR-015**: Baselines MUST adapt as the athlete's own history evolves.
- **FR-016**: All thresholds and ranges MUST be documented and inspectable rather than embedded as
  unexplained values.

#### Response verification

- **FR-017**: Every value a coaching response states about the athlete's training MUST agree with the
  retrieved value.
- **FR-018**: A coaching response MUST NOT state a value for a metric that was not retrieved.
- **FR-019**: Responses MUST be checked before delivery, and a response failing the check MUST be corrected
  or withheld rather than sent.
- **FR-020**: When required data could not be retrieved, the response MUST say so rather than answering as
  though it were available.
- **FR-021**: Check failures MUST be recorded so their frequency is measurable.
- **FR-022**: All guardrail evaluation MUST be performed deterministically, never by the language model,
  which may only narrate results it was given.
- **FR-022a**: Guardrail evaluation MUST ship with tests covering each threshold, including the cases where
  a threshold must NOT fire — insufficient history, a single anomalous reading, and normal values — per the
  project's constitutional requirement that engine changes be test-covered.

#### Authority and restraint

- **FR-023**: A guardrail MUST NOT modify a plan or a calendar entry without the athlete's agreement.
- **FR-024**: An accepted guardrail recommendation MUST be applied through the same approval path as any
  other change.
- **FR-025**: A declined recommendation MUST leave the plan unchanged and MUST NOT be raised repeatedly for
  the same occurrence.
- **FR-026**: Repeated athlete override MUST NOT escalate into obstruction; the signal continues to be
  stated.
- **FR-027**: Every raised signal MUST be accompanied by what the athlete can do about it.

#### Scope of advice

- **FR-028**: The system MUST state at first use, and in documentation, that it is neither a physician nor a
  certified coach and that proposed sessions are suggestions.
- **FR-029**: When signals are more consistent with illness than with training fatigue, the system MUST
  suggest qualified advice rather than prescribing training through them.
- **FR-030**: The system MUST NOT offer a diagnosis in response to reported pain or injury.

### Key Entities

- **Workload ratio**: The relationship between recently accumulated load and load absorbed over a longer
  preceding period, evaluated against a documented safe range.
- **Load uniformity**: How evenly training load is distributed across a period, where excessive evenness is
  itself a risk.
- **Recovery baseline**: The athlete's own established normal for a recovery signal, derived from their
  history and adapting as it evolves.
- **Recovery signal**: A dated observation of heart rate variability, resting heart rate, or sleep,
  evaluated against its baseline.
- **Guardrail finding**: A raised signal, carrying the observed value, the baseline or range, the threshold
  crossed, and what the athlete can do about it.
- **Response check**: The verification applied to a coaching response before delivery, confirming that every
  value it states matches what was retrieved.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of coaching responses that state a training metric state a value matching the retrieved
  value, verified across a representative corpus of responses.
- **SC-002**: Zero coaching responses state a value for a metric that was not retrieved.
- **SC-003**: Every guardrail finding presented to the athlete includes the observed value, the reference
  point, and a recommended action.
- **SC-004**: Zero guardrail findings fire when the athlete lacks sufficient history to establish a
  baseline.
- **SC-005**: A single anomalous reading never triggers a finding on its own, verified by injecting outliers
  into otherwise normal data.
- **SC-006**: Zero plan or calendar changes occur as a direct result of a guardrail firing without the
  athlete agreeing.
- **SC-007**: Every threshold and range in use is documented and can be located by a reader without reading
  code.
- **SC-008**: When load ratio exceeds the documented range, 100% of subsequent load recommendations reduce
  rather than increase load.
- **SC-009**: The disclaimer is presented before the athlete's first coaching interaction and is present in
  user-facing documentation.
- **SC-010**: Guardrail evaluation results are reproducible: the same inputs produce the same findings every
  time.

## Assumptions

- The recovery signals evaluated here — heart rate variability, resting heart rate, sleep — come from the
  training data source and are captured but not interpreted by the preceding specification. This feature
  interprets them and acquires nothing new.
- Load uniformity is already computed by the existing engine using an established formula. This feature
  surfaces and acts on it rather than introducing it.
- The workload ratio and the readiness thresholds are drawn from published, citable training science, as is
  the existing uniformity measure. Specific published values are used as documented defaults, which is why
  FR-016 requires them to be inspectable rather than buried.
- Thresholds are stated as absolutes in this specification because the athlete needs a fixed reference to
  interpret a signal. They remain configurable defaults rather than immutable constants, but changing them
  is a deliberate act, not an incidental one.
- Baselines are personal and rolling. A fixed baseline would misread genuine fitness change as deviation,
  which is why FR-015 requires adaptation.
- Guardrails are advisory by design. Making them authoritative would require them to be right, and they will
  sometimes be wrong — a poor night's sleep has causes this system cannot see. The athlete keeps the
  decision, and the coach keeps the obligation to say what it sees.
- Response verification is possible because every metric the coach may cite is retrieved or computed before
  the response is generated. Verification compares the response against that known set; it does not
  re-derive the athlete's data.
- Determinism is a constitutional constraint, not a preference. The language model narrates findings; it
  never produces them. This makes findings testable and reproducible, which SC-010 requires.
- This feature raises signals and adjusts training. It does not diagnose, and the boundary matters legally
  as well as ethically, which is why FR-028 through FR-030 are requirements rather than documentation notes.
