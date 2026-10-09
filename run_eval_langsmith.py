"""
Week 4 — LangSmith experiment runner (build-guide Step 3.1 / 3.2).

Runs the golden dataset through ONE system as a LangSmith *experiment* using
`langsmith.evaluate(...)`, so each case is scored in-UI by three evaluators and
the two experiments (Nexus baseline vs Hub post-improvement) line up in the
side-by-side comparison view.

Evaluators (exactly the three from Table 4 / the build guide):
  * exact_match_classification     — GREEN/YELLOW/RED vs ground truth
  * exact_match_escalation_tier    — NONE/TIER_1/2/3 vs ground truth
  * care_plan_faithfulness_judge   — LLM-as-judge (cached per discharge template)

Reuses the per-case pipeline logic from eval_harness so there is one source of truth.

Run each system with its own venv (after LANGCHAIN_API_KEY is set):
    week3/nexus/venv/Scripts/python.exe run_eval_langsmith.py --system nexus
    week3/hub/venv/Scripts/python.exe   run_eval_langsmith.py --system hub

Then open the dataset in LangSmith and compare experiments `nexus-baseline-*` and
`hub-post-improvement-*`.
"""

import argparse
from pathlib import Path

import eval_harness as H
import faithfulness_judge as FJ

DATASET_DEFAULT = "nexus-hub-recovery-golden-v1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=["nexus", "hub"])
    ap.add_argument("--dataset", default=DATASET_DEFAULT)
    ap.add_argument("--concurrency", type=int, default=1,
                    help="keep at 1 — the orchestrators + shared template cache are not built for parallel runs")
    args = ap.parse_args()

    # Enables tracing (wraps Anthropic) + loads the project's orchestrator.
    root, orch = H._load_system(args.system, langsmith=True)

    import os
    if not (os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")):
        raise SystemExit("LANGCHAIN_API_KEY not set — add it to the project's .env first.")

    import anthropic
    judge_client = anthropic.Anthropic()  # wrapped -> judge calls are traced too

    pdf_dir = H.HERE / "_pdfs"
    pdf_dir.mkdir(exist_ok=True)

    template_cache: dict = {}      # discharge-determined intake+careplan, per template
    judge_cache: dict = {}         # faithfulness score, per template (5 LLM calls total)

    # ── target: dataset example inputs -> system outputs ──────────────────────
    def run_case(inputs: dict) -> dict:
        case = {
            "case_id": inputs["case_id"],
            "discharge_summary_text": inputs["discharge_summary_text"],
            "checkin_response": inputs["checkin_response"],
            "prior_checkin_history": inputs.get("prior_checkin_history", []),
        }
        dtext = case["discharge_summary_text"]
        tkey = H.template_key(dtext)

        if tkey not in template_cache:
            pdf = pdf_dir / f"{tkey}.pdf"
            if not pdf.exists():
                H.text_to_pdf(dtext, pdf)
            st = H.base_state(f"tmpl_{tkey}", H.patient_name_from(dtext),
                              H.discharge_date_from(dtext), str(pdf))
            st = H.run_intake_careplan(args.system, orch, st)
            template_cache[tkey] = {
                "diagnosis": st.get("diagnosis"), "icd10_code": st.get("icd10_code"),
                "medications": st.get("medications", []), "appointments": st.get("appointments", []),
                "warning_signs_er": st.get("warning_signs_er", []),
                "warning_signs_call": st.get("warning_signs_call", []),
                "dietary_restrictions": st.get("dietary_restrictions", []),
                "activity_restrictions": st.get("activity_restrictions", []),
                "care_plan": st.get("care_plan", {}),
            }

        tmpl = template_cache[tkey]
        meds = tmpl["medications"]
        pdf = pdf_dir / f"{tkey}.pdf"
        st = H.base_state(f"case_{case['case_id']}", H.patient_name_from(dtext),
                          H.discharge_date_from(dtext), str(pdf))
        st.update({k: tmpl[k] for k in (
            "diagnosis", "icd10_code", "medications", "appointments",
            "warning_signs_er", "warning_signs_call", "dietary_restrictions",
            "activity_restrictions", "care_plan")})
        st["intake_complete"] = True
        st["care_plan_complete"] = True

        ci = case["checkin_response"]
        st["recovery_day"] = ci.get("day", 1)
        st["check_in_history"] = H.build_prior_history(case, meds)
        responses = H.map_checkin(ci, meds)

        st = H.run_checkin(args.system, orch, st, responses)
        mon = st.get("last_monitoring_result", {})
        cls = mon.get("classification")
        tier = H.system_tier(cls, st.get("escalation_tier"))
        return {
            "got_classification": cls,
            "got_escalation_tier": tier,
            "care_plan": tmpl["care_plan"],
            "template": tkey,
            "flags": mon.get("flags", []),
            "summary": mon.get("summary", ""),
        }

    # ── evaluators (run, example) -> {key, score} ─────────────────────────────
    def exact_match_classification(run, example):
        got = (run.outputs or {}).get("got_classification")
        exp = (example.outputs or {}).get("expected_classification")
        return {"key": "classification_correct", "score": int(got == exp)}

    def exact_match_escalation_tier(run, example):
        got = (run.outputs or {}).get("got_escalation_tier")
        exp = (example.outputs or {}).get("expected_escalation_tier")
        return {"key": "escalation_tier_correct", "score": int(got == exp)}

    def care_plan_faithfulness_judge(run, example):
        tkey = (run.outputs or {}).get("template")
        if tkey not in judge_cache:
            verdict = FJ.judge_one(
                judge_client,
                (example.inputs or {}).get("discharge_summary_text", ""),
                (run.outputs or {}).get("care_plan", {}),
            )
            judge_cache[tkey] = verdict.get("score")
        return {"key": "care_plan_faithfulness", "score": judge_cache.get(tkey)}

    prefix = "nexus-baseline" if args.system == "nexus" else "hub-post-improvement"
    arch = "5-agent" if args.system == "nexus" else "2-agent-consolidated"

    from langsmith import evaluate
    results = evaluate(
        run_case,
        data=args.dataset,
        evaluators=[exact_match_classification, exact_match_escalation_tier, care_plan_faithfulness_judge],
        experiment_prefix=prefix,
        max_concurrency=args.concurrency,
        metadata={"system": args.system, "architecture": arch, "week": "4", "option": "C"},
    )

    print(f"\n[{args.system}] LangSmith experiment submitted: prefix '{prefix}'.")
    try:
        print(f"  experiment: {results.experiment_name}")
    except Exception:
        pass
    print("  Open the dataset in LangSmith and compare 'nexus-baseline-*' vs 'hub-post-improvement-*'.")


if __name__ == "__main__":
    main()
