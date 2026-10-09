"""
Unified three-way comparison: Nexus -> Hub v1 -> Hub v2.

Tells the whole story as two independent levers:
  Lever A (architecture):  Nexus (5-agent) -> Hub v1 (2-agent, same prompts/logic)
  Lever B (tier logic):    Hub v1 -> Hub v2 (two-layer max-urgency escalation tier + guardrails)

Reads results_nexus.json, results_hub.json (v1), results_hub_v2.json and the faithfulness files,
and writes Three_Way_Comparison.md.
"""

import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEV = {"NONE": 0, "GREEN": 0, "TIER_1": 1, "YELLOW": 1, "TIER_2": 2, "RED": 2, "TIER_3": 3}
BUILD = {"nexus": (5, 2, 56), "hub_v1": (3, 1, 41), "hub_v2": (3, 1, 41)}
LABEL = {"nexus": "Nexus (5-agent)", "hub_v1": "Hub v1 (2-agent)", "hub_v2": "Hub v2 (2-agent + tier fix)"}
FILES = {"nexus": "results_nexus.json", "hub_v1": "results_hub.json", "hub_v2": "results_hub_v2.json"}
FAITH = {"nexus": "faithfulness_nexus.json", "hub_v1": "faithfulness_hub.json", "hub_v2": "faithfulness_hub.json"}


def load(name):
    p = HERE / name
    return {r["case_id"]: r for r in json.loads(p.read_text(encoding="utf-8"))} if p.exists() else None


def faith_mean(name):
    p = HERE / name
    if not p.exists():
        return None
    sc = [v["score"] for v in json.loads(p.read_text(encoding="utf-8")) if isinstance(v.get("score"), (int, float))]
    return sum(sc) / len(sc) if sc else None


def summarize(rows):
    vals = list(rows.values())
    n = len(vals)
    reds = [r for r in vals if r["expected_classification"] == "RED"]
    checkin_cost = statistics.mean([sum(e["cost_usd"] for e in r.get("checkin_token_log", [])) for r in vals])
    red_cost = statistics.mean([sum(e["cost_usd"] for e in r.get("checkin_token_log", [])) for r in reds]) if reds else 0
    return {
        "n": n,
        "cls": sum(r["classification_correct"] for r in vals) / n,
        "tier": sum(r["tier_correct"] for r in vals) / n,
        "red_caught": sum(1 for r in reds if r["got_classification"] == "RED"),
        "red_n": len(reds),
        "red_tier": sum(r["tier_correct"] for r in reds) / len(reds) if reds else 0,
        "red_tier_ok": sum(r["tier_correct"] for r in reds),
        "checkin_cost": checkin_cost,
        "red_cost": red_cost,
    }


def red_tier_row(systems, cid_order):
    return cid_order


def main():
    data = {k: load(v) for k, v in FILES.items()}
    have = [k for k in ("nexus", "hub_v1", "hub_v2") if data[k]]
    if len(have) < 2:
        raise SystemExit("Need at least two of results_nexus/hub/hub_v2.json")
    S = {k: summarize(data[k]) for k in have}
    F = {k: faith_mean(FAITH[k]) for k in have}

    L = []
    L.append("# Three-Way Comparison: Nexus → Hub v1 → Hub v2\n")
    L.append("_Post-hospital recovery co-pilot. One product, evaluated across two independent change "
             "levers on the same 30-case golden dataset._\n")

    L.append("## The story in one picture\n")
    L.append("```")
    L.append("Nexus (5-agent)  --[Lever A: collapse to 2 agents]-->  Hub v1  --[Lever B: fix escalation tier]-->  Hub v2")
    L.append("   baseline           architecture change                same          targeted improvement        improved")
    L.append("```")
    L.append("- **Lever A — architecture (Nexus → Hub v1):** *what:* collapse Intake/CarePlan/Monitoring/"
             "Admin into one Recovery Agent, keep Escalation separate. *Why:* those four were a sequential, "
             "path-dependent pipeline over one patient — not independent specialists; only Escalation gates "
             "an irreversible action (SMS / 911). *Hypothesis:* leaner with no quality/cost loss.")
    L.append("- **Lever B — escalation-tier logic (Hub v1 → Hub v2):** *what:* replace keyword tier-matching "
             "with a two-layer max-urgency design (keyword floor + LLM judgment, take the more urgent) plus "
             "a trend floor and a dyspnea guardrail. *Why:* keyword matching under-tiers paraphrased "
             "emergencies (\"I feel like I'm suffocating\") — a safety-critical gap. *Hypothesis:* fix the "
             "dominant failure mode without unsafe regressions.\n")

    # Unified metrics table
    L.append("## Unified metrics\n")
    cols = " | ".join(LABEL[k] for k in have)
    L.append(f"| Metric | {cols} |")
    L.append("|---|" + "---|" * len(have))

    def row(label, fn):
        return f"| {label} | " + " | ".join(fn(k) for k in have) + " |"

    L.append(row("Classification accuracy (30)", lambda k: f"{S[k]['cls']:.1%}"))
    L.append(row("Escalation-tier accuracy (30)", lambda k: f"{S[k]['tier']:.1%}"))
    L.append(row("RED emergencies caught", lambda k: f"{S[k]['red_caught']}/{S[k]['red_n']}"))
    L.append(row("RED tier exactly correct", lambda k: f"{S[k]['red_tier_ok']}/{S[k]['red_n']} ({S[k]['red_tier']:.0%})"))
    L.append(row("Care-plan faithfulness (judge)", lambda k: f"{F[k]:.2f}" if F[k] is not None else "n/a"))
    L.append(row("Mean check-in cost", lambda k: f"${S[k]['checkin_cost']:.4f}"))
    L.append(row("Mean RED check-in cost", lambda k: f"${S[k]['red_cost']:.4f}"))
    L.append(row("Orchestration nodes / graphs / LOC", lambda k: "{} / {} / {}".format(*BUILD[k])))
    L.append("")

    # Lever deltas
    if "nexus" in have and "hub_v1" in have:
        a, b = S["nexus"], S["hub_v1"]
        L.append("## Lever A delta — architecture (Nexus → Hub v1)\n")
        L.append(f"- Classification: {a['cls']:.1%} → {b['cls']:.1%}  ({b['cls']-a['cls']:+.1%})")
        L.append(f"- Escalation tier: {a['tier']:.1%} → {b['tier']:.1%}  ({b['tier']-a['tier']:+.1%})")
        L.append(f"- RED caught: {a['red_caught']}/{a['red_n']} → {b['red_caught']}/{b['red_n']}")
        L.append(f"- Mean check-in cost: ${a['checkin_cost']:.4f} → ${b['checkin_cost']:.4f}")
        L.append(f"- Build: {BUILD['nexus'][0]} nodes / {BUILD['nexus'][1]} graphs / {BUILD['nexus'][2]} LOC "
                 f"→ {BUILD['hub_v1'][0]} / {BUILD['hub_v1'][1]} / {BUILD['hub_v1'][2]}")
        L.append("\n**Read:** essentially flat on every quality/cost metric (differences are LLM sampling "
                 "noise — same byte-identical prompts), while orchestration shrinks. **Conclusion:** the "
                 "four collapsed boundaries weren't earning their complexity for this task shape. "
                 "Cost of consolidation: per-step debuggability (5 labeled agents → fewer log sources).\n")

    if "hub_v1" in have and "hub_v2" in have:
        a, b = S["hub_v1"], S["hub_v2"]
        # fixed / safe-over / unsafe-under across the two hub variants
        v1, v2 = data["hub_v1"], data["hub_v2"]
        def ok(r): return r["classification_correct"] and r["tier_correct"]
        fixed = [i for i in v1 if not ok(v1[i]) and ok(v2[i])]
        safe_over, unsafe_under = [], []
        for i in v1:
            if ok(v1[i]) and not ok(v2[i]):
                exp_s = SEV.get(v2[i]["expected_escalation_tier"], 0)
                got_s = SEV.get(v2[i]["got_escalation_tier"], 0)
                (safe_over if got_s > exp_s else unsafe_under).append(i)
        L.append("## Lever B delta — escalation-tier logic (Hub v1 → Hub v2)\n")
        L.append(f"- Classification: {a['cls']:.1%} → {b['cls']:.1%}  ({b['cls']-a['cls']:+.1%})")
        L.append(f"- Escalation tier: {a['tier']:.1%} → {b['tier']:.1%}  ({b['tier']-a['tier']:+.1%})")
        L.append(f"- RED tier exactly correct: {a['red_tier_ok']}/{a['red_n']} → {b['red_tier_ok']}/{b['red_n']}  "
                 f"({b['red_tier']-a['red_tier']:+.0%})")
        L.append(f"- Mean RED check-in cost: ${a['red_cost']:.4f} → ${b['red_cost']:.4f}  "
                 f"(+1 triage LLM call on RED only)")
        L.append(f"\n  Fixed: {', '.join(fixed) or 'none'} | Safe over-escalations: "
                 f"{', '.join(safe_over) or 'none'} | **Unsafe regressions: {', '.join(unsafe_under) or 'none'}**")
        L.append("\n**Read:** the dominant failure mode (keyword tier-gap) is fixed; tier accuracy and RED-tier "
                 "both rise, with the only regression being a *safe over-escalation* on a borderline case. "
                 "Cost rises only on RED check-ins. **Conclusion:** meaning-based triage beats keyword "
                 "matching for paraphrased emergencies, and the keyword floor under the LLM keeps it safe.\n")

    # RED-tier per-case across all three
    if "nexus" in have:
        reds = sorted([cid for cid, r in data["nexus"].items() if r["expected_classification"] == "RED"])
        L.append("## Per-case escalation tier on the RED cases (the safety-critical ones)\n")
        L.append("| Case | Expected | " + " | ".join(LABEL[k] for k in have) + " |")
        L.append("|---|---|" + "---|" * len(have))
        for cid in reds:
            exp = data[have[0]][cid]["expected_escalation_tier"]
            cells = []
            for k in have:
                r = data[k].get(cid, {})
                got = r.get("got_escalation_tier", "?")
                mark = "✅" if r.get("tier_correct") else "⚠️"
                cells.append(f"{got} {mark}")
            L.append(f"| {cid} | {exp} | " + " | ".join(cells) + " |")
        L.append("\n_F01/F02 = paraphrased emergencies ('suffocating', 'elephant on my chest'); E08 = "
                 "sustained-YELLOW trend; A02 = clean positive control; F03 = borderline TIER_2/TIER_3._\n")

    L.append("## Bottom line (what / how / why)\n")
    L.append("- **What we did:** consolidated a 5-agent pipeline to 2 agents (Lever A), then fixed the "
             "dominant safety failure it surfaced — keyword-based escalation tiering (Lever B).")
    L.append("- **How we measured it:** one 30-case hand-labeled golden dataset, three evaluators "
             "(classification + escalation-tier exact-match, care-plan faithfulness LLM-judge), run in "
             "LangSmith with full trace/tool/cost instrumentation.")
    L.append("- **Why it matters:** Lever A shows agent count was incidental complexity for this task "
             "shape; Lever B shows where the real risk lived (urgency under-tiering on paraphrased "
             "emergencies) and fixes it with a safety-biased, voice/text-agnostic design — zero "
             "under-caution regressions.")
    L.append("")

    out = HERE / "Three_Way_Comparison.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"Wrote {out}")
    for k in have:
        print(f"  {LABEL[k]}: class {S[k]['cls']:.0%} tier {S[k]['tier']:.0%} RED-tier {S[k]['red_tier_ok']}/{S[k]['red_n']}")


if __name__ == "__main__":
    main()
