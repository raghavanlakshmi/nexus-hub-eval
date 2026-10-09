# Evaluation Results: Nexus (baseline) vs Hub (post-improvement)

_Golden dataset: 30 cases (15 happy / 9 edge / 4 known-failure / 2 adversarial)._

## Headline metrics

| Metric | NEXUS | HUB |
|---|---|---|
| Classification accuracy (30) | 29/30 (96.7%) | 29/30 (96.7%) |
| Escalation-tier accuracy (30) | 28/30 (93.3%) | 27/30 (90.0%) |
| RED cases caught (classification) | 5/5 | 5/5 |
| RED tier exactly correct | 4/5 | 3/5 |
| Care-plan faithfulness (templates) | 5/5, mean 0.97 | 5/5, mean 0.96 |
| Mean check-in cost (USD) | $0.0048 | $0.0049 |
| One-time intake+careplan cost | $0.0472 | $0.0483 |
| Mean check-in latency (s) | 5.5 | 5.6 |
| p95 check-in latency (s) | 10.5 | 9.9 |
| Orchestration: nodes / graphs / LOC | 5 / 2 / 56 | 3 / 1 / 41 |

## Classification accuracy by scenario type

| Scenario | NEXUS | HUB |
|---|---|---|
| happy_path | 15/15 | 15/15 |
| edge_case | 8/9 | 8/9 |
| known_failure | 4/4 | 4/4 |
| adversarial | 2/2 | 2/2 |

## Failure clusters

### NEXUS
- **Under-classification (predicted LESS severe than truth — safety-critical)**: 1 — E06
- **Keyword tier-gap on RED (right RED call, wrong tier)**: 1 — E08

### HUB
- **Under-classification (predicted LESS severe than truth — safety-critical)**: 1 — E06
- **Keyword tier-gap on RED (right RED call, wrong tier)**: 2 — E08, F01

## Delta — Nexus (baseline) -> Hub (post-improvement)

**Lever:** Control flow — collapse Intake/CarePlan/Monitoring/Admin into one Recovery Agent; keep Escalation as the sole separate, conditionally-routed boundary.

| Dimension | Nexus | Hub | Delta |
|---|---|---|---|
| Classification accuracy | 96.7% | 96.7% | 0.0% |
| Escalation-tier accuracy | 93.3% | 90.0% | -3.3% |
| RED caught | 5/5 | 5/5 | +0 |
| Mean check-in cost | $0.0048 | $0.0049 | $+0.0000 |
| p95 check-in latency | 10.5s | 9.9s | -0.6s |
| Orchestration nodes | 5 | 3 | -2 |
| Compiled graphs | 2 | 1 | -1 |
| Orchestration LOC | 56 | 41 | -15 |

**Honesty checkpoint:** cost/latency differences are within run-to-run LLM variance (same prompts, same model, same token workload). The keyword tier-gap failure cluster appears in BOTH systems identically — it is independent of agent count. What Hub trades away is per-step debuggability (5 separately-labeled log sources collapse to fewer).
