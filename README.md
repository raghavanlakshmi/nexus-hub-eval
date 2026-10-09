# Nexus vs Hub — Evaluating a Multi-Agent Recovery Co-Pilot

A golden-dataset evaluation of one product built two ways: **[Nexus](https://github.com/raghavanlakshmi/nexus)**
(5 agents) and **[Hub](https://github.com/raghavanlakshmi/hub)** (2 agents). The goal was to measure two
changes separately:

```
Nexus (5-agent)  ──[A: collapse to 2 agents]──►  Hub v1  ──[B: fix escalation tier]──►  Hub v2
```

- **Lever A — architecture.** Does collapsing four sequential agents into one cost anything in quality or money?
- **Lever B — escalation logic.** Can meaning-based triage fix paraphrased emergencies that keyword matching misses, without making anything less safe?

## Results (30 hand-labelled cases)

| Metric | Nexus | Hub v1 | Hub v2 |
|---|---|---|---|
| Classification accuracy (GREEN/YELLOW/RED) | 96.7% | 96.7% | **100%** |
| Escalation-tier accuracy | 93.3% | 90.0% | **96.7%** |
| RED emergencies caught | 5/5 | 5/5 | 5/5 |
| RED tier exactly correct | 4/5 | 3/5 | 4/5 |
| Care-plan faithfulness (LLM judge) | 0.97 | 0.96 | 0.96 |
| Mean check-in cost | $0.0048 | $0.0049 | $0.0053 |
| Orchestration nodes / graphs / LOC | 5 / 2 / 56 | 3 / 1 / 41 | 3 / 1 / 41 |

**Lever A:** quality and cost are flat (the gaps are LLM sampling noise; the prompts are byte-identical),
while the orchestration shrinks. The four collapsed agent boundaries weren't paying for their complexity.

**Lever B:** the dominant failure was keyword tier-matching under-tiering paraphrased emergencies
("I feel like I'm suffocating"). A two-layer design — keyword floor plus LLM judgment, take the more
urgent — fixed it. The only remaining miss is a *safe* over-escalation on a borderline case;
**zero unsafe regressions**. Extra cost is one LLM call, only on RED check-ins.

Full write-up: [Week4_Evaluation_Report.md](Week4_Evaluation_Report.md) ·
[Three_Way_Comparison.md](Three_Way_Comparison.md) · [hub_v1_v2_delta.md](hub_v1_v2_delta.md)

## Method

- **Golden dataset** — `golden_dataset_cases.json` / `golden_dataset_labels.csv`: 30 synthetic discharge +
  check-in scenarios (15 happy path, 9 edge, 4 known-failure, 2 adversarial) across CHF, COPD, post-op
  hip, type-2 diabetes and pneumonia. Ground truth hand-written. Discharge PDFs are in `_pdfs/`.
- **Evaluators** — exact match for classification and escalation tier; an LLM-as-judge
  (`faithfulness_judge.py`) for care-plan faithfulness (no invented meds, no dose changes).
- **Instrumentation** — per-call tokens, cost and latency from each system's own usage tracker, with
  LangSmith tracing layered on top.
- **Isolation** — each system runs in its own process and venv (both expose `agents.orchestrator`).
  Pinecone writes and SMS/email are stubbed; OpenFDA and Claude calls are real.

## Reproducing

Clone this repo next to `nexus` and `hub` (or point `NEXUS_DIR` / `HUB_DIR` at them), with each
system's `.env` filled in:

```bash
# run with each system's own interpreter
../nexus/venv/bin/python eval_harness.py --system nexus
../hub/venv/bin/python   eval_harness.py --system hub
../hub/venv/bin/python   eval_harness.py --system hub --variant v2   # Hub v2
# add --langsmith to trace a run, --limit 3 for a quick smoke test

python faithfulness_judge.py --system nexus      # LLM-judge care plans
python three_way_compare.py                      # Nexus vs Hub v1 vs Hub v2 tables
```

`run_eval_langsmith.py` and `upload_dataset_langsmith.py` run the same evaluation as a LangSmith
experiment (needs `LANGCHAIN_API_KEY`). Raw outputs are committed as `results_*.json` and
`faithfulness_*.json`.

All data is synthetic. Not medical advice.

## License

[MIT](LICENSE)
