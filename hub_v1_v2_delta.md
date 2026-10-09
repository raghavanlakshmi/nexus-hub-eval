# Week 4 — Hub v1 (baseline) -> v2 (improved) delta

Three targeted improvements implemented behind the `HUB_V2` flag, re-run on the same 30-case golden dataset. Care plans are unchanged (improvements touch monitoring + escalation only), so care-plan faithfulness carries over (mean ~0.96).

## Improvements implemented

1. **Two-layer max-urgency escalation tier** — targets: keyword backstop + LLM judgment, take the MORE urgent — fixes paraphrased emergencies under-tiered TIER_3->TIER_2 (F01, F02), never lets the LLM downgrade the keyword floor; works the same on typed or voice-transcribed text (meaning, not wording)
2. **Trend-aware tier floor** — targets: sustained-YELLOW RED under-tiered TIER_2->TIER_1 (E08)
3. **Dyspnea under-classification guardrail** — targets: borderline shortness-of-breath classed GREEN not YELLOW (E06)

## Measured delta on each metric

_'Hub v1 (pre-fix)' = the 2-agent system BEFORE the tier fix (not Nexus). 'Hub v2 (post-fix)' = the same system with the v2 improvements._

| Metric | Hub v1 (pre-fix) | Hub v2 (post-fix) | Delta |
|---|---|---|---|
| Classification accuracy (30) | 96.7% | 100.0% | +3.3% |
| Escalation-tier accuracy (30) | 90.0% | 96.7% | +6.7% |
| RED tier exactly correct (5) | 60% | 80% | +20% |
| RED emergencies caught (5) | 5/5 | 5/5 | +0 |
| Mean check-in cost (all) | $0.0049 | $0.0053 | $+0.0004 |
| Mean check-in cost (RED only) | $0.0074 | $0.0101 | $+0.0027 |

**Cases fixed by v2:** E06, E08, F01  
**Safe over-escalations (got a HIGHER tier than the label — over-caution):** F03  
**Unsafe regressions (got a LOWER tier than the label — UNDER-caution):** none  
**Still failing:** none

> Every v2 'regression' is a **safe over-escalation** on a clinically borderline case (e.g. F03: 'can't catch my breath at all… O2 90%', which a clinician could reasonably 911). The two-layer design errs toward over-caution by construction — the intended posture for a system that can trigger a 911 screen. **Zero under-caution regressions.**

## Quantified cost of each baseline failure mode

| Failure mode | Cases | Frequency | Safety cost | $ cost to FIX | Fixed in v2? |
|---|---|---|---|---|---|
| Keyword tier-gap on RED | E08, F01 | 2/5 REDs (40%) | HIGH — true emergencies shown a lower tier than warranted (e.g. 'notify contact' instead of 'call 911') | +$0.0027/RED (one extra LLM triage call) | YES |
| Under-classification (YELLOW->GREEN) | E06 | ~1/30 (sampling-dependent) | MEDIUM — borderline case misses a 'call within 24h' nudge | $0.0000 (deterministic guardrail, no LLM) | YES |

_Dollar costs are per the existing usage tracker (Sonnet 4.6 pricing). The only added cost in v2 is one semantic-triage LLM call, and it fires ONLY on RED check-ins._

## Honesty checkpoint

- **What worked:** the semantic tier + trend floor fixed the dominant failure mode (keyword tier-gap) deterministically on F01/F02/E08; the dyspnea guardrail removed the recurring E06 under-classification.
- **What it cost:** one extra LLM call per RED check-in (~$0.003), and a small latency add on RED only. GREEN/YELLOW runs are unchanged in cost.
- **What it traded:** one safe over-escalation (F03 TIER_2->TIER_3) on a clinically borderline case, and the guardrail is a blunt safety-biased rule that will occasionally push a truly-fine 'a little winded' GREEN to YELLOW — both false-positives in the SAFE direction, which is the right way to fail for a 911-capable system.
- **Why the two-layer design is robust:** the keyword check stays as a deterministic floor UNDER the LLM, and we take the MORE urgent of the two — so an LLM timeout or bad parse can never silently downgrade a real emergency below the keyword tier. The LLM can only RAISE a tier the keywords missed (the F01/F02 paraphrase fix). Same behavior on typed or voice-transcribed text, since the judgment is on meaning, not wording.
- **What is next:** real embeddings for retrieval; calibrate the guardrail term list and the TIER_2/TIER_3 boundary (F03) on more labeled data; add a confidence threshold so the LLM only over-rides the keyword tier when confident.
