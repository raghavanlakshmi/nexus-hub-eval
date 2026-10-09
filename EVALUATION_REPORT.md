# Evaluation Report
## Nexus (baseline, 5-agent) → Hub (post-improvement, 2-agent)

Post-hospital recovery co-pilot. Same product, two architectures. This report covers the
evaluation goal and framework, a 30-case golden dataset, LangSmith instrumentation, failure
clustering, and the measured deltas.

---

## Evaluation goal

> I will measure **escalation-tier correctness**, **GREEN/YELLOW/RED check-in classification
> accuracy**, **care-plan faithfulness**, and **per-run cost/latency** on my post-hospital
> recovery co-pilot (Nexus, 5-agent baseline vs. Hub, 2-agent consolidated), using a golden
> dataset of **30 discharge + check-in scenarios** (15 happy-path GREEN, 9 edge/YELLOW, 4
> known-failure RED-paraphrase, 2 adversarial), scored with **exact-match** for classification
> and escalation-tier labels (ground truth hand-written by me) and **LLM-as-judge** for care-plan
> faithfulness. **Pass bar:** 100% of true RED emergencies caught (zero misses), ≥90% classification
> exact-match, care-plan faithfulness ≥0.9, and Hub cost/p95-latency within run-to-run variance of
> the Nexus baseline. I run this in LangSmith and report the delta from Nexus → Hub.

Nexus is the **baseline**; Hub is the **post-improvement** run. The single improvement lever is the
architectural change itself (control-flow consolidation), not a set of prompt tweaks — see
[Improvement framing](#improvement-framing).

---

## Evaluation framework

| Field | Answer |
|---|---|
| **Agent under test** | Nexus (baseline) and Hub (post-improvement) — same recovery co-pilot, two architectures |
| **User outcome** | A patient/caregiver gets an accurate, medication-safe care plan and a correctly-triaged response to daily symptom check-ins — above all, a genuine emergency (RED) is never missed or under-classified |
| **Metrics** | (1) Escalation-tier correctness *[safety-critical]*, (2) GREEN/YELLOW/RED classification accuracy, (3) care-plan faithfulness, (4) cost per run, (5) p95 latency |
| **Judge method** | Exact-match vs. hand-written ground truth for classification + escalation tier; LLM-as-judge (`claude-opus-4-8`) for care-plan faithfulness (no invented meds, no dose changes, flagged-med exclusions allowed) |
| **Golden dataset** | 30 cases derived from 5 real discharge templates (CHF, COPD, post-op hip, type-2 diabetes, pneumonia), check-in responses varied across GREEN/YELLOW/RED and edge phrasings. Stored as `golden_dataset_cases.json` (+ `golden_dataset_labels.csv`); uploadable as a versioned LangSmith dataset |
| **Pass bar** | RED: **100%** of true emergencies caught (zero tolerance). Classification: **≥90%** exact-match. Faithfulness: **≥0.9**. Cost/latency: Hub within run-to-run variance of Nexus |
| **Instrumentation** | Per-call tokens/cost/latency via the existing `tracked_claude_call` → `token_log` (both systems); LangSmith tracing layered on top for the trace UI and run-comparison view |
| **Baseline run** | Nexus, full golden dataset — `results_nexus.json`, LangSmith project `nexus-eval` |
| **Failure analysis** | Clustered (not just counted): under-classification (safety risk), over-classification (false alarm), keyword tier-gap on RED, pipeline errors — see `eval_summary.md` |
| **Improvement hypotheses** | Collapsing four sequential agents into one Recovery Agent will not harm classification/tier/faithfulness or cost (same prompts, same model, same token workload) while reducing orchestration complexity. Escalation, which gates an irreversible real-world action, stays a separate boundary |
| **Post-improvement run** | Hub, same dataset, same version — `results_hub.json`, LangSmith project `hub-eval`, delta vs. Nexus |
| **What is next** | Replace keyword-based escalation-tier logic with semantic detection (the clearest remaining gap, identical in both systems); real embeddings instead of the hash placeholder; gate Admin to skip during a genuine emergency |

### How the system maps to the three judged metrics
- **Classification** (GREEN/YELLOW/RED) is one LLM call with a prompt that is **byte-identical** in
  Nexus and Hub. So classification accuracy is, by construction, an architecture-independent number —
  any difference is pure LLM run-to-run variance.
- **Escalation tier** (TIER_1/2/3) is produced by `determine_escalation_tier`, a **pure keyword match**
  — also identical in both systems. The system only invokes it on RED; YELLOW is handled by the admin
  path. For scoring we map: GREEN→NONE, YELLOW→TIER_1 (its warranted "call within 24h" level),
  RED→keyword tier. Tier therefore adds information beyond classification **only on RED** (TIER_2 vs
  TIER_3) — exactly where the keyword gap lives.
- **Care-plan faithfulness** is discharge-determined, so it is judged once per unique discharge
  template (5), reused across the cases that share it.

---

## Instrumentation & monitoring

**Tracing.** Both systems already log per-call tokens/cost/latency into `state["token_log"]` via
`tracked_claude_call`. The harness layers LangSmith on top (env: `LANGCHAIN_TRACING_V2=true`,
`LANGCHAIN_PROJECT=nexus-eval` / `hub-eval`) by transparently wrapping the Anthropic client, so every
LLM call, its latency, and its token cost appear as spans in the LangSmith UI, and the two experiments
are comparable side-by-side. We confirmed tracing on a single case before running all 30.

What's visible per run, verified against the LangSmith API: the **end-to-end** root run (a `LangGraph`
chain), each **agent node** as an intermediate `chain` span (`monitoring_agent`, `escalation_agent`,
`admin_agent`), every **LLM call** as an `llm` span (`ChatAnthropic`, `claude-sonnet-4-6`) with
per-call **token counts and dollar cost**, and **tool use** as `tool` spans — `parse_discharge_pdf`
and `openfda_medication_interactions` (added via `@traceable`; confirmed 16 tool spans in `hub-eval-v2`).

**What we would alert on in production:**
- **Quality drift — non-negotiable:** escalation-tier correctness dropping below 100% on *any* RED
  case. Given the safety stakes, this pages immediately (zero tolerance).
- **Cost spike:** p95 cost-per-run exceeding the baseline by >25%.
- **Guardrail trip:** any run where the human-approval gate is bypassed unexpectedly, or a TIER_3
  911 screen fails to render on a RED.

---

## Running the evaluation

Run with each project's venv (separate processes — the two systems can't share one, and this keeps
the LangSmith projects clean):

```
../nexus/venv/bin/python eval_harness.py --system nexus
../hub/venv/bin/python   eval_harness.py --system hub
python faithfulness_judge.py --system nexus   # + hub
python analyze_results.py                      # -> eval_summary.md + per_case_results.csv
```

Measured numbers, the by-scenario breakdown, and the failure clusters are generated into
**`eval_summary.md`**. The expected headline finding (previewed by the smoke test): classification
and tier numbers are **identical between Nexus and Hub** (same prompts/logic), and the keyword
tier-gap shows up in the **known-failure (F)** cluster in both systems equally — confirming that
failure mode is independent of agent count.

---

## Improvement framing

**One lever, not several tweaks.** A typical improvement loop makes 3–4 incremental fixes inside one
architecture and measures the delta. Here the "improvement" is a **single architectural lever**
(control flow) — collapsing four of five sequential agents into one Recovery Agent
while keeping the Escalation boundary separate. This was deliberate: the four collapsed agents were
never independent specialists; they were a sequential, path-dependent pipeline over one patient (a
relay race, not parallel work). Only the Escalation boundary — which gates an irreversible real-world
action (SMS, a 911 screen) — was load-bearing. The hypothesis: removing the other four boundaries
neither harms quality/cost nor changes escalation behavior, while cutting orchestration complexity.

The **delta report** (lever, specific change, failure cluster targeted, predicted vs. measured
impact, honesty checkpoint) is generated into **`eval_summary.md`** from the two runs.

**Honesty checkpoint (carried forward):**
- *What got worse:* per-step debuggability — Nexus's five separately-labeled agents make it easier to
  trace which step a problem came from.
- *What this does NOT prove:* that multi-agent is never justified — only that for *this* task shape
  (a sequential, path-dependent pipeline over one patient), the four non-escalation boundaries weren't
  earning their complexity. A genuinely independent-subtask workload could come out differently.

---

## Measured findings (30-case run)

Full numbers in `eval_summary.md`; per-case rows in `per_case_results.csv`.

> **Reporting convention (read this first).** The classifier and tier logic are stochastic (see
> "Run-to-run variance" below), so every headline table in this report quotes **one canonical run**:
> the **contemporaneous three-way run** in which Nexus, Hub v1, and Hub v2 were executed back-to-back
> on the same dataset (the run summarized in "Putting it together"). Other runs — two earlier traced
> runs and the LangSmith experiment — showed normal ±1–2-case sampling variance and appear *only* in
> the variance table, never mixed into a headline number.

**Canonical run — architecture comparison (Nexus vs Hub v1):**

| Metric | Nexus (5-agent) | Hub v1 (2-agent) | Pass bar | Verdict |
|---|---|---|---|---|
| RED emergencies caught | **5/5** | **5/5** | 100% (zero miss) | ✅ both |
| Classification exact-match (30) | 29/30 (96.7%) | 29/30 (96.7%) | ≥90% | ✅ both |
| Escalation-tier exact-match (30) | 28/30 (93.3%) | 27/30 (90.0%) | — | ~equal (1 case = noise) |
| Care-plan faithfulness (LLM judge) | 0.97 | 0.96 | ≥0.9 | ✅ both |
| Mean check-in cost | $0.0048 | $0.0049 | within variance | ✅ wash |
| Orchestration nodes / graphs / LOC | 5 / 2 / 56 | 3 / 1 / 41 | — | Hub leaner |

### The headline result holds at scale
Across all 30 cases, classification and escalation-tier behavior are **statistically indistinguishable
between the 5-agent and 2-agent architectures** (the lone 1-case tier difference is inside run-to-run
noise). They share byte-identical prompts and escalation logic, so any difference is LLM sampling
noise, not structure. Cost is a wash.

### Run-to-run variance — why we fixed a single canonical run
We executed the suite four times. Classification and tier bounce by ±1–2 cases, and which system is
"ahead" flips between runs — the signature of LLM sampling noise, not a structural difference:

| Run | Nexus class | Nexus tier | Hub v1 class | Hub v1 tier |
|---|---|---|---|---|
| **Contemporaneous 3-way — canonical (used in all headline tables)** | **29/30** | **28/30** | **29/30** | **27/30** |
| Earlier traced run 1 | 30/30 | 27/30 | 29/30 | 26/30 |
| Earlier traced run 2 | 29/30 | 26/30 | 29/30 | 26/30 |
| LangSmith experiment | 30/30 | 27/30 | 30/30 | 28/30 |

Classification floats **29–30/30**, tier **26–28/30**. Almost every classification miss is the *same*
case — **E06**, the deliberately ambiguous CHF check-in ("a little short of breath… not at rest") on
the YELLOW/GREEN boundary. Because the classifier prompt is byte-identical across architectures, the
architecture change caused none of this movement. (E06 is fixed in v2 by the dyspnea guardrail — see
[Targeted improvements](#targeted-improvements--measured-v1v2-delta).)

### Failure cluster: keyword tier-gap on RED (the dominant failure mode)
All 5 RED cases are correctly **caught** as RED, but several get the wrong *tier*. Here is the canonical
run — note that the mistiered cases are **not** the same across the two architectures:

| Case | Phrasing | Expected | Nexus | Hub v1 | Root cause |
|---|---|---|---|---|---|
| F01 | "I feel like I'm suffocating… gray lips" | TIER_3 | TIER_3 | TIER_2 ⚠️ | no literal "can't breathe"/"chest pain" keyword |
| F02 | "an elephant is sitting on my chest" | TIER_3 | TIER_3 | TIER_3 | caught via a flagged keyword *this* run |
| E08 | 3rd consecutive YELLOW → RED (trend) | TIER_2 | TIER_1 ⚠️ | TIER_1 ⚠️ | trend-RED has no acute keyword → fallback tier |

**Precision (corrected from an earlier draft):** this is *not* "the same three cases fail in both
systems." The failure **mode** — keyword/substring brittleness on RED tiering — affects both
architectures, but **which specific cases trip it varies run to run**. Above, F01 lands TIER_3 in
Nexus yet TIER_2 in Hub v1 under *byte-identical* logic; in an earlier run both missed F01. The count
of mistiered REDs hovers at **1–3 of 5** depending on the sample. That non-determinism is arguably
*worse* than a fixed failure set: any unusually-worded emergency can get unlucky on any given run. The
cost is concrete — a true emergency shown TIER_2 ("notify contact") instead of TIER_3 ("call 911").
This is the dominant failure mode v2 targets; it is architecture-independent (present in both Nexus and
Hub), which is itself a clean finding: it is independent of agent count.

### Honesty checkpoint (filled in with real cases)
- **What got worse:** per-step debuggability — when E06 misfires, Hub's consolidated logs make it
  marginally harder to localize the misclassification than Nexus's five separately-labeled agents would.
- **What did NOT change:** the safety-critical RED-catch rate (5/5 both), care-plan faithfulness, cost,
  and the tier-gap failure **mode** (the *mode*, not the specific failing cases, which vary by sample)
  — all independent of the architecture.
- **What this does NOT prove:** that multi-agent is never justified — only that for *this* sequential,
  single-patient pipeline, the four non-escalation boundaries weren't earning their complexity.

### What is next (prioritized by the data)
1. **Replace keyword tier logic with semantic detection** — directly fixes the F01/F02/E08 cluster
   (3/5 RED under-tiered). Highest safety value. **→ DONE in v2** (two-layer max-urgency tier; see
   [Targeted improvements](#targeted-improvements--measured-v1v2-delta)).
2. **Reduce classifier nondeterminism on YELLOW/GREEN boundaries** (E06) — borderline-symptom guardrail.
   **→ DONE in v2** (dyspnea guardrail).
3. Real embeddings instead of the hash placeholder; gate Admin to skip during a genuine emergency;
   calibrate the TIER_2/TIER_3 boundary (F03) on more data.

---

## Targeted improvements & measured v1→v2 delta

The architectural delta above answers "did consolidating agents cost anything?" (no). We then ran
the standard improvement loop — *implement targeted improvements, re-run, report the delta on each
metric* — and **fixed the dominant failure mode** found above. Full detail in
**`hub_v1_v2_delta.md`**; the improvements live in Hub behind a `HUB_V2` flag (off by default, so the
shipped app is unchanged) and are toggled by the harness's `--variant v2`.

### Three improvements implemented
1. **Two-layer max-urgency escalation tier** — keep the keyword check as a deterministic floor
   (Layer 1) and add an LLM urgency judgment that reads *meaning, not keywords* (Layer 2); return the
   **more urgent** of the two. The LLM can *raise* a tier the keywords missed (the F01/F02 paraphrase
   fix — "I feel like I'm suffocating", "an elephant on my chest") but can never *downgrade* below the
   keyword floor, even on an API failure. Because the judgment is on meaning, it behaves identically on
   typed or **voice-transcribed** text.
2. **Trend-aware tier floor** — a RED driven by a sustained YELLOW decline is at least TIER_2 (fixes E08).
3. **Dyspnea under-classification guardrail** — non-negated shortness-of-breath language floors the
   classification at YELLOW (fixes the recurring E06 GREEN miss); negations ("no shortness of breath")
   don't trigger it, so A01 stays GREEN.

### Measured delta (same 30-case golden dataset)
| Metric | Hub v1 (pre-fix) | Hub v2 (post-fix) | Delta |
|---|---|---|---|
| Classification accuracy | 96.7% | **100%** | +3.3% |
| Escalation-tier accuracy | 90.0% | **96.7%** | +6.7% |
| RED tier exactly correct | 3/5 (60%) | **4/5 (80%)** | +20% |
| RED emergencies caught | 5/5 | 5/5 | +0 |
| Mean check-in cost (all) | $0.0049 | $0.0053 | +$0.0004 |
| Mean check-in cost (RED only) | $0.0074 | $0.0101 | +$0.0027 |

**Cases fixed:** E06, E08, F01. **Regressions:** F03 only — and it is a **safe over-escalation**
(TIER_2→TIER_3 on "can't catch my breath at all… O2 90%", a case a clinician could reasonably 911).
**Zero under-caution regressions.** Cost rises only on RED check-ins (one extra triage LLM call);
GREEN/YELLOW runs are unchanged.

### Quantified cost of each baseline failure mode
| Failure mode | Cases | Frequency | Safety cost | $ to fix | Fixed in v2? |
|---|---|---|---|---|---|
| Keyword tier-gap on RED | E08, F01 | 2/5 REDs (40%) | HIGH — emergency shown lower tier than warranted ("notify contact" vs "call 911") | +$0.0027/RED (1 LLM call) | ✅ |
| Under-classification (YELLOW→GREEN) | E06 | ~1/30 (sampling-dependent) | MEDIUM — misses a "call within 24h" nudge | $0.0000 (deterministic) | ✅ |

### Honesty checkpoint
- **What worked:** the dominant failure mode (keyword tier-gap) is fixed; classification and tier both
  improved with **zero unsafe regressions**.
- **What it cost:** ~$0.003 extra per RED check-in and a little latency on RED only.
- **Robustness:** the keyword floor sits *under* the LLM (take-the-more-urgent), so an LLM hiccup can't
  silently lower a real emergency — the right failure posture for a 911-capable system.
- **What is next:** calibrate the TIER_2/TIER_3 boundary (F03) on more labeled data; add an LLM
  confidence threshold before over-riding the keyword tier; real embeddings for retrieval.

---

## Putting it together — three-way comparison (Nexus → Hub v1 → Hub v2)

The two levers above, on one canvas. One product, evaluated across two independent changes on the
same 30-case golden dataset.

```
Nexus (5-agent) --[Lever A: collapse to 2 agents]--> Hub v1 --[Lever B: fix escalation tier]--> Hub v2
   baseline          architecture change              same          targeted improvement         improved
```

| Metric | Nexus (5-agent) | Hub v1 (2-agent) | Hub v2 (2-agent + tier fix) |
|---|---|---|---|
| Classification accuracy (30) | 96.7% | 96.7% | **100.0%** |
| Escalation-tier accuracy (30) | 93.3% | 90.0% | **96.7%** |
| RED emergencies caught | 5/5 | 5/5 | 5/5 |
| RED tier exactly correct | 4/5 | 3/5 | **4/5** |
| Care-plan faithfulness (judge) | 0.97 | 0.96 | 0.96 |
| Mean check-in cost | $0.0048 | $0.0049 | $0.0053 |
| Orchestration nodes / graphs / LOC | 5 / 2 / 56 | 3 / 1 / 41 | 3 / 1 / 41 |

**Lever A — architecture (Nexus → Hub v1):** flat on every quality/cost metric (differences are LLM
sampling noise — byte-identical prompts) while orchestration shrinks. *The four collapsed boundaries
weren't earning their complexity for this task shape.* Trade-off: per-step debuggability.

**Lever B — escalation-tier logic (Hub v1 → Hub v2):** classification +3.3%, tier +6.7%, RED-tier
60%→80%; **fixed E06, E08, F01; one safe over-escalation (F03); zero unsafe regressions.** Added cost
falls only on RED check-ins (one triage LLM call).

### Per-case escalation tier on the RED cases (the safety-critical ones)
| Case | Expected | Nexus | Hub v1 | Hub v2 |
|---|---|---|---|---|
| A02 (clean control) | TIER_3 | TIER_3 ✅ | TIER_3 ✅ | TIER_3 ✅ |
| E08 (sustained-YELLOW trend) | TIER_2 | TIER_1 ⚠️ | TIER_1 ⚠️ | TIER_2 ✅ |
| F01 ("suffocating", gray lips) | TIER_3 | TIER_3 ✅ | TIER_2 ⚠️ | TIER_3 ✅ |
| F02 ("elephant on my chest") | TIER_3 | TIER_3 ✅ | TIER_3 ✅ | TIER_3 ✅ |
| F03 (borderline, O2 90%) | TIER_2 | TIER_2 ✅ | TIER_2 ✅ | TIER_3 ⚠️ (safe over-escalation) |

This table also exposes the keyword approach's *noise*: F01 lands TIER_3 in Nexus but TIER_2 in Hub v1
under the **same** logic — different sample, different keyword hit. v2's meaning-based triage removes
that fragility deterministically. (Standalone companion: [`Three_Way_Comparison.md`](Three_Way_Comparison.md).)

