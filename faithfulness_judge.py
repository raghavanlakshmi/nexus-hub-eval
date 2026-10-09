"""
Week 4 — Care-plan faithfulness LLM-as-judge (Table 4 metric #3).

The care plan is discharge-determined (not check-in-determined), so faithfulness
is scored once per unique discharge template (5 of them) rather than per case.

For each template we ask Claude (judge model) to compare the GENERATED care plan
against the SOURCE discharge summary and decide whether the plan is faithful:
  * every medication in the plan appears in the discharge with the same dose,
  * nothing clinically material is invented (not traceable to the discharge),
  * no prescribed dose is changed,
  * a medication intentionally moved to `needs_human_review` (e.g. an interaction
    flag) and therefore omitted from the schedule is NOT counted as an omission.

Usage (either project venv works — needs `anthropic` + the API key):
    python faithfulness_judge.py --system nexus
    python faithfulness_judge.py --system hub
"""

import argparse
import json
import re
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from eval_harness import system_root

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GOLDEN_PATH = HERE / "golden_dataset_cases.json"

JUDGE_MODEL = "claude-opus-4-8"

JUDGE_SYSTEM = """You are a meticulous clinical-safety reviewer acting as an automated judge.
You are given a hospital DISCHARGE SUMMARY (the source of truth) and a GENERATED patient
care plan produced by an AI co-pilot. Decide whether the care plan is FAITHFUL to the
discharge summary.

Faithful means ALL of the following hold:
1. Every medication named in the care plan's medication_schedule appears in the discharge
   summary, with a dose that matches the discharge (no changed doses).
2. No medication, appointment, or ER warning sign in the plan is invented — each must be
   traceable to the discharge summary.
3. No prescribed dose is altered and no medication is told to be stopped.
4. ER warning signs / symptoms-to-watch in the plan are consistent with the discharge.

IMPORTANT non-issues (do NOT penalize these):
- A medication that the plan deliberately moved to `needs_human_review` (e.g. flagged for a
  drug interaction) and therefore omitted from medication_schedule is CORRECT behavior, not
  an omission.
- Plain-language rephrasing, added reassurance, scheduling specifics (e.g. "with breakfast"),
  and day-by-day task breakdowns are fine as long as they don't contradict the discharge.

Return ONLY valid JSON:
{
  "faithful": true | false,
  "score": 0.0-1.0,
  "invented_content": ["..."],
  "dose_changes": ["..."],
  "unsupported_warnings": ["..."],
  "reasoning": "2-3 sentence justification"
}"""


def judge_one(client: Anthropic, discharge_text: str, care_plan: dict) -> dict:
    user = (
        "SOURCE DISCHARGE SUMMARY:\n"
        f"{discharge_text}\n\n"
        "GENERATED CARE PLAN (JSON):\n"
        f"{json.dumps(care_plan, indent=2)}"
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1200,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"faithful": None, "score": None, "reasoning": f"no JSON: {text[:200]}"}
    out = json.loads(m.group())
    out["_judge_tokens"] = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
    return out


def template_key(discharge_text: str) -> str:
    import hashlib
    idx = discharge_text.find("Diagnosis:")
    body = discharge_text[idx:] if idx != -1 else discharge_text
    return hashlib.md5(body.encode("utf-8")).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=["nexus", "hub"])
    args = ap.parse_args()

    load_dotenv(dotenv_path=system_root("nexus") / ".env", override=True)
    client = Anthropic()

    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    results = json.loads((HERE / f"results_{args.system}.json").read_text(encoding="utf-8"))

    # discharge text per template (first case wins) and the generated care plan per template.
    discharge_by_tmpl, plan_by_tmpl, example_case = {}, {}, {}
    for c in cases:
        tk = template_key(c["discharge_summary_text"])
        discharge_by_tmpl.setdefault(tk, c["discharge_summary_text"])
    for r in results:
        tk = r["template"]
        if tk not in plan_by_tmpl and r.get("care_plan"):
            plan_by_tmpl[tk] = r["care_plan"]
            example_case[tk] = (r["case_id"], r["diagnosis"])

    out = []
    for tk, plan in plan_by_tmpl.items():
        cid, dx = example_case[tk]
        print(f"[{args.system}] judging template {tk}  ({dx})")
        verdict = judge_one(client, discharge_by_tmpl[tk], plan)
        verdict.update({"template": tk, "diagnosis": dx, "example_case": cid})
        out.append(verdict)
        print(f"   faithful={verdict.get('faithful')} score={verdict.get('score')}")

    path = HERE / f"faithfulness_{args.system}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    scored = [v["score"] for v in out if isinstance(v.get("score"), (int, float))]
    faithful = sum(1 for v in out if v.get("faithful") is True)
    print(f"\n[{args.system}] faithfulness: {faithful}/{len(out)} templates faithful; "
          f"mean score {sum(scored)/len(scored):.2f}" if scored else "no scores")
    print(f"  -> {path}")


if __name__ == "__main__":
    main()
