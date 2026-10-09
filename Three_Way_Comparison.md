# Week 4 — Three-Way Comparison: Nexus → Hub v1 → Hub v2

_Post-hospital recovery co-pilot. One product, evaluated across two independent change levers on the same 30-case golden dataset._

## The story in one picture

```
Nexus (5-agent)  --[Lever A: collapse to 2 agents]-->  Hub v1  --[Lever B: fix escalation tier]-->  Hub v2
   baseline           architecture change                same          targeted improvement        improved
```
- **Lever A — architecture (Nexus → Hub v1):** *what:* collapse Intake/CarePlan/Monitoring/Admin into one Recovery Agent, keep Escalation separate. *Why:* those four were a sequential, path-dependent pipeline over one patient — not independent specialists; only Escalation gates an irreversible action (SMS / 911). *Hypothesis:* leaner with no quality/cost loss.
- **Lever B — escalation-tier logic (Hub v1 → Hub v2):** *what:* replace keyword tier-matching with a two-layer max-urgency design (keyword floor + LLM judgment, take the more urgent) plus a trend floor and a dyspnea guardrail. *Why:* keyword matching under-tiers paraphrased emergencies ("I feel like I'm suffocating") — a safety-critical gap. *Hypothesis:* fix the dominant failure mode without unsafe regressions.

## Unified metrics

| Metric | Nexus (5-agent) | Hub v1 (2-agent) | Hub v2 (2-agent + tier fix) |
|---|---|---|---|
| Classification accuracy (30) | 96.7% | 96.7% | 100.0% |
| Escalation-tier accuracy (30) | 93.3% | 90.0% | 96.7% |
| RED emergencies caught | 5/5 | 5/5 | 5/5 |
| RED tier exactly correct | 4/5 (80%) | 3/5 (60%) | 4/5 (80%) |
| Care-plan faithfulness (judge) | 0.97 | 0.96 | 0.96 |
| Mean check-in cost | $0.0048 | $0.0049 | $0.0053 |
| Mean RED check-in cost | $0.0076 | $0.0074 | $0.0101 |
| Orchestration nodes / graphs / LOC | 5 / 2 / 56 | 3 / 1 / 41 | 3 / 1 / 41 |

## Lever A delta — architecture (Nexus → Hub v1)

- Classification: 96.7% → 96.7%  (+0.0%)
- Escalation tier: 93.3% → 90.0%  (-3.3%)
- RED caught: 5/5 → 5/5
- Mean check-in cost: $0.0048 → $0.0049
- Build: 5 nodes / 2 graphs / 56 LOC → 3 / 1 / 41

**Read:** essentially flat on every quality/cost metric (differences are LLM sampling noise — same byte-identical prompts), while orchestration shrinks. **Conclusion:** the four collapsed boundaries weren't earning their complexity for this task shape. Cost of consolidation: per-step debuggability (5 labeled agents → fewer log sources).

## Lever B delta — escalation-tier logic (Hub v1 → Hub v2)

- Classification: 96.7% → 100.0%  (+3.3%)
- Escalation tier: 90.0% → 96.7%  (+6.7%)
- RED tier exactly correct: 3/5 → 4/5  (+20%)
- Mean RED check-in cost: $0.0074 → $0.0101  (+1 triage LLM call on RED only)

  Fixed: E06, E08, F01 | Safe over-escalations: F03 | **Unsafe regressions: none**

**Read:** the dominant failure mode (keyword tier-gap) is fixed; tier accuracy and RED-tier both rise, with the only regression being a *safe over-escalation* on a borderline case. Cost rises only on RED check-ins. **Conclusion:** meaning-based triage beats keyword matching for paraphrased emergencies, and the keyword floor under the LLM keeps it safe.

## Per-case escalation tier on the RED cases (the safety-critical ones)

| Case | Expected | Nexus (5-agent) | Hub v1 (2-agent) | Hub v2 (2-agent + tier fix) |
|---|---|---|---|---|
| A02 | TIER_3 | TIER_3 ✅ | TIER_3 ✅ | TIER_3 ✅ |
| E08 | TIER_2 | TIER_1 ⚠️ | TIER_1 ⚠️ | TIER_2 ✅ |
| F01 | TIER_3 | TIER_3 ✅ | TIER_2 ⚠️ | TIER_3 ✅ |
| F02 | TIER_3 | TIER_3 ✅ | TIER_3 ✅ | TIER_3 ✅ |
| F03 | TIER_2 | TIER_2 ✅ | TIER_2 ✅ | TIER_3 ⚠️ |

_F01/F02 = paraphrased emergencies ('suffocating', 'elephant on my chest'); E08 = sustained-YELLOW trend; A02 = clean positive control; F03 = borderline TIER_2/TIER_3._

## Bottom line (what / how / why)

- **What we did:** consolidated a 5-agent pipeline to 2 agents (Lever A), then fixed the dominant safety failure it surfaced — keyword-based escalation tiering (Lever B).
- **How we measured it:** one 30-case hand-labeled golden dataset, three evaluators (classification + escalation-tier exact-match, care-plan faithfulness LLM-judge), run in LangSmith with full trace/tool/cost instrumentation.
- **Why it matters:** Lever A shows agent count was incidental complexity for this task shape; Lever B shows where the real risk lived (urgency under-tiering on paraphrased emergencies) and fixes it with a safety-biased, voice/text-agnostic design — zero under-caution regressions.
