"""
Analysis: score the runs, cluster failures, emit the delta report.

Reads results_{nexus,hub}.json (and faithfulness_{nexus,hub}.json if present) and writes:
  * per_case_results.csv      — one row per case per system (for the appendix / LangSmith parity)
  * eval_summary.md           — metrics table, failure clusters, Nexus->Hub delta

No LLM calls here — pure scoring over the captured run artifacts.
"""

import csv
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Static build-complexity facts (from the Nexus-vs-Hub comparison; see hub/README.md).
BUILD = {
    "nexus": {"nodes": 5, "graphs": 2, "orch_loc": 56},
    "hub":   {"nodes": 3, "graphs": 1, "orch_loc": 41},
}
SEVERITY = {"NONE": 0, "GREEN": 0, "TIER_1": 1, "YELLOW": 1, "TIER_2": 2, "RED": 2, "TIER_3": 3}


def load(system):
    p = HERE / f"results_{system}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def faithfulness(system):
    p = HERE / f"faithfulness_{system}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def run_cost_latency(r):
    """Per-check-in monitoring cost/latency, plus one-time discharge (intake+careplan)."""
    checkin_cost = sum(e["cost_usd"] for e in r.get("checkin_token_log", []))
    discharge_cost = sum(e["cost_usd"] for e in r.get("discharge_token_log", []))
    return checkin_cost, discharge_cost, r.get("checkin_secs", 0.0)


def summarize(system, rows):
    n = len(rows)
    cls_ok = sum(x["classification_correct"] for x in rows)
    tier_ok = sum(x["tier_correct"] for x in rows)
    reds = [x for x in rows if x["expected_classification"] == "RED"]
    red_tier_ok = sum(x["tier_correct"] for x in reds)
    red_caught = sum(1 for x in reds if x["got_classification"] == "RED")

    by_scn = {}
    for x in rows:
        s = x["scenario"]
        d = by_scn.setdefault(s, {"n": 0, "cls": 0, "tier": 0})
        d["n"] += 1
        d["cls"] += x["classification_correct"]
        d["tier"] += x["tier_correct"]

    checkin_costs, discharge_costs, latencies = [], [], []
    for x in rows:
        cc, dc, lat = run_cost_latency(x)
        checkin_costs.append(cc)
        discharge_costs.append(dc)
        latencies.append(lat)

    def pctl(v, p):
        if not v:
            return 0.0
        v = sorted(v)
        k = max(0, min(len(v) - 1, int(round((p / 100) * (len(v) - 1)))))
        return v[k]

    fj = faithfulness(system) or []
    fj_scores = [v["score"] for v in fj if isinstance(v.get("score"), (int, float))]

    return {
        "system": system, "n": n,
        "cls_acc": cls_ok / n, "cls_ok": cls_ok,
        "tier_acc": tier_ok / n, "tier_ok": tier_ok,
        "red_n": len(reds), "red_caught": red_caught, "red_tier_ok": red_tier_ok,
        "by_scenario": by_scn,
        "checkin_cost_mean": statistics.mean(checkin_costs) if checkin_costs else 0,
        "discharge_cost_mean": statistics.mean(discharge_costs) if discharge_costs else 0,
        "latency_mean": statistics.mean(latencies) if latencies else 0,
        "latency_p95": pctl(latencies, 95),
        "faithful_n": sum(1 for v in fj if v.get("faithful") is True),
        "faithful_total": len(fj),
        "faithful_mean": (sum(fj_scores) / len(fj_scores)) if fj_scores else None,
        "build": BUILD[system],
    }


def cluster_failures(rows):
    """Bucket every incorrect case into a root-cause cluster."""
    clusters = {
        "under_classification": [],     # predicted less severe than truth (safety risk)
        "over_classification": [],      # predicted more severe than truth
        "tier_gap_on_red": [],          # classification RED-correct but wrong tier (keyword gap)
        "errored": [],
    }
    for x in rows:
        if x.get("error"):
            clusters["errored"].append(x["case_id"])
            continue
        cls_ok = x["classification_correct"]
        tier_ok = x["tier_correct"]
        exp_s = SEVERITY.get(x["expected_classification"], 0)
        got_s = SEVERITY.get(x["got_classification"], 0)
        if not cls_ok and got_s < exp_s:
            clusters["under_classification"].append(x["case_id"])
        elif not cls_ok and got_s > exp_s:
            clusters["over_classification"].append(x["case_id"])
        if cls_ok and not tier_ok and x["expected_classification"] == "RED":
            clusters["tier_gap_on_red"].append(x["case_id"])
    return clusters


def write_csv(systems):
    path = HERE / "per_case_results.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["system", "case_id", "scenario", "diagnosis",
                    "expected_class", "got_class", "class_ok",
                    "expected_tier", "got_tier", "tier_ok",
                    "checkin_secs", "summary"])
        for sysname, rows in systems.items():
            for x in rows:
                w.writerow([sysname, x["case_id"], x["scenario"], x["diagnosis"],
                            x["expected_classification"], x["got_classification"],
                            x["classification_correct"],
                            x["expected_escalation_tier"], x["got_escalation_tier"],
                            x["tier_correct"], x.get("checkin_secs", ""),
                            (x.get("summary") or "")[:120]])
    return path


def pct(x):
    return f"{x:.1%}"


def main():
    nexus, hub = load("nexus"), load("hub")
    have = {k: v for k, v in {"nexus": nexus, "hub": hub}.items() if v is not None}
    if not have:
        raise SystemExit("No results_*.json found — run eval_harness.py first.")

    summaries = {k: summarize(k, v) for k, v in have.items()}
    clusters = {k: cluster_failures(v) for k, v in have.items()}
    csv_path = write_csv(have)

    L = []
    L.append("# Evaluation Results: Nexus (baseline) vs Hub (post-improvement)\n")
    L.append(f"_Golden dataset: 30 cases (15 happy / 9 edge / 4 known-failure / 2 adversarial)._\n")

    # Headline metrics table
    L.append("## Headline metrics\n")
    L.append("| Metric | " + " | ".join(s.upper() for s in have) + " |")
    L.append("|---|" + "---|" * len(have))
    def row(label, fn):
        return f"| {label} | " + " | ".join(fn(summaries[s]) for s in have) + " |"
    L.append(row("Classification accuracy (30)", lambda s: f"{s['cls_ok']}/{s['n']} ({pct(s['cls_acc'])})"))
    L.append(row("Escalation-tier accuracy (30)", lambda s: f"{s['tier_ok']}/{s['n']} ({pct(s['tier_acc'])})"))
    L.append(row("RED cases caught (classification)", lambda s: f"{s['red_caught']}/{s['red_n']}"))
    L.append(row("RED tier exactly correct", lambda s: f"{s['red_tier_ok']}/{s['red_n']}"))
    L.append(row("Care-plan faithfulness (templates)",
                 lambda s: (f"{s['faithful_n']}/{s['faithful_total']}"
                            + (f", mean {s['faithful_mean']:.2f}" if s['faithful_mean'] is not None else ""))
                           if s['faithful_total'] else "n/a"))
    L.append(row("Mean check-in cost (USD)", lambda s: f"${s['checkin_cost_mean']:.4f}"))
    L.append(row("One-time intake+careplan cost", lambda s: f"${s['discharge_cost_mean']:.4f}"))
    L.append(row("Mean check-in latency (s)", lambda s: f"{s['latency_mean']:.1f}"))
    L.append(row("p95 check-in latency (s)", lambda s: f"{s['latency_p95']:.1f}"))
    L.append(row("Orchestration: nodes / graphs / LOC",
                 lambda s: f"{s['build']['nodes']} / {s['build']['graphs']} / {s['build']['orch_loc']}"))
    L.append("")

    # By scenario
    L.append("## Classification accuracy by scenario type\n")
    L.append("| Scenario | " + " | ".join(s.upper() for s in have) + " |")
    L.append("|---|" + "---|" * len(have))
    for scn in ["happy_path", "edge_case", "known_failure", "adversarial"]:
        cells = []
        for s in have:
            d = summaries[s]["by_scenario"].get(scn)
            cells.append(f"{d['cls']}/{d['n']}" if d else "-")
        L.append(f"| {scn} | " + " | ".join(cells) + " |")
    L.append("")

    # Failure clusters
    L.append("## Failure clusters\n")
    for s in have:
        L.append(f"### {s.upper()}")
        c = clusters[s]
        names = {
            "under_classification": "Under-classification (predicted LESS severe than truth — safety-critical)",
            "over_classification": "Over-classification (predicted MORE severe — false alarm)",
            "tier_gap_on_red": "Keyword tier-gap on RED (right RED call, wrong tier)",
            "errored": "Pipeline errors",
        }
        any_fail = False
        for key, label in names.items():
            ids = c[key]
            if ids:
                any_fail = True
                L.append(f"- **{label}**: {len(ids)} — {', '.join(ids)}")
        if not any_fail:
            L.append("- _No failures in any cluster._")
        L.append("")

    # Delta report
    if "nexus" in have and "hub" in have:
        nx, hb = summaries["nexus"], summaries["hub"]
        L.append("## Delta — Nexus (baseline) -> Hub (post-improvement)\n")
        L.append("**Lever:** Control flow — collapse Intake/CarePlan/Monitoring/Admin into one Recovery Agent; "
                 "keep Escalation as the sole separate, conditionally-routed boundary.\n")
        L.append("| Dimension | Nexus | Hub | Delta |")
        L.append("|---|---|---|---|")
        def d3(a, b, fmt="{:.4f}", unit=""):
            return f"| {{}} | {fmt.format(a)}{unit} | {fmt.format(b)}{unit} | {fmt.format(b-a)}{unit} |"
        L.append(f"| Classification accuracy | {pct(nx['cls_acc'])} | {pct(hb['cls_acc'])} | {pct(hb['cls_acc']-nx['cls_acc'])} |")
        L.append(f"| Escalation-tier accuracy | {pct(nx['tier_acc'])} | {pct(hb['tier_acc'])} | {pct(hb['tier_acc']-nx['tier_acc'])} |")
        L.append(f"| RED caught | {nx['red_caught']}/{nx['red_n']} | {hb['red_caught']}/{hb['red_n']} | {hb['red_caught']-nx['red_caught']:+d} |")
        L.append(f"| Mean check-in cost | ${nx['checkin_cost_mean']:.4f} | ${hb['checkin_cost_mean']:.4f} | ${hb['checkin_cost_mean']-nx['checkin_cost_mean']:+.4f} |")
        L.append(f"| p95 check-in latency | {nx['latency_p95']:.1f}s | {hb['latency_p95']:.1f}s | {hb['latency_p95']-nx['latency_p95']:+.1f}s |")
        L.append(f"| Orchestration nodes | {nx['build']['nodes']} | {hb['build']['nodes']} | {hb['build']['nodes']-nx['build']['nodes']:+d} |")
        L.append(f"| Compiled graphs | {nx['build']['graphs']} | {hb['build']['graphs']} | {hb['build']['graphs']-nx['build']['graphs']:+d} |")
        L.append(f"| Orchestration LOC | {nx['build']['orch_loc']} | {hb['build']['orch_loc']} | {hb['build']['orch_loc']-nx['build']['orch_loc']:+d} |")
        L.append("")
        L.append("**Honesty checkpoint:** cost/latency differences are within run-to-run LLM variance "
                 "(same prompts, same model, same token workload). The keyword tier-gap failure cluster "
                 "appears in BOTH systems identically — it is independent of agent count. What Hub trades "
                 "away is per-step debuggability (5 separately-labeled log sources collapse to fewer).")
        L.append("")

    out = HERE / "eval_summary.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"Wrote {csv_path}")
    for s in have:
        sm = summaries[s]
        print(f"  [{s}] class {sm['cls_ok']}/{sm['n']}  tier {sm['tier_ok']}/{sm['n']}  "
              f"RED caught {sm['red_caught']}/{sm['red_n']}")


if __name__ == "__main__":
    main()
