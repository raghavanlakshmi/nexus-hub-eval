"""
Upload the golden dataset to LangSmith as a versioned Dataset.

Per the handout, the golden set should live as a LangSmith Dataset (re-runnable, versioned),
not a loose spreadsheet. This reads golden_dataset_cases.json and creates/updates a dataset
whose examples carry the check-in inputs and the hand-written ground-truth outputs.

Prereq: set LANGCHAIN_API_KEY (and optionally LANGSMITH_ENDPOINT) in the environment or in
the nexus .env (see eval_harness.system_root). Free tier at https://smith.langchain.com works.

    python upload_dataset_langsmith.py
    python upload_dataset_langsmith.py --name nexus-hub-golden-v1
"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from eval_harness import system_root

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GOLDEN_PATH = HERE / "golden_dataset_cases.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="nexus-hub-recovery-golden-v1")
    ap.add_argument("--description",
                    default="Nexus vs Hub — 30-case post-discharge golden set "
                            "(15 happy / 9 edge / 4 known-failure / 2 adversarial). "
                            "Ground truth: classification + escalation tier, hand-labeled.")
    args = ap.parse_args()

    # Pick up keys from either the process env or the project .env.
    load_dotenv(dotenv_path=system_root("nexus") / ".env", override=False)
    if not os.getenv("LANGCHAIN_API_KEY") and not os.getenv("LANGSMITH_API_KEY"):
        raise SystemExit("Set LANGCHAIN_API_KEY (or LANGSMITH_API_KEY) first — see smith.langchain.com.")

    from langsmith import Client
    client = Client()

    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    if client.has_dataset(dataset_name=args.name):
        ds = client.read_dataset(dataset_name=args.name)
        print(f"Dataset '{args.name}' already exists ({ds.id}); adding any new examples.")
    else:
        ds = client.create_dataset(dataset_name=args.name, description=args.description)
        print(f"Created dataset '{args.name}' ({ds.id}).")

    # Avoid duplicating examples on re-run: key by case_id stored in metadata.
    existing = set()
    for ex in client.list_examples(dataset_id=ds.id):
        cid = (ex.metadata or {}).get("case_id")
        if cid:
            existing.add(cid)

    inputs, outputs, metadatas = [], [], []
    for c in cases:
        if c["case_id"] in existing:
            continue
        inputs.append({
            "case_id": c["case_id"],
            "discharge_summary_text": c["discharge_summary_text"],
            "checkin_response": c["checkin_response"],
            "prior_checkin_history": c.get("prior_checkin_history", []),
        })
        outputs.append({
            "expected_classification": c["expected_classification"],
            "expected_escalation_tier": c["expected_escalation_tier"],
        })
        metadatas.append({"case_id": c["case_id"], "scenario": _scn(c["case_id"]), "version": "v1-baseline"})

    if not inputs:
        print("No new examples to add — dataset already complete.")
        return

    client.create_examples(dataset_id=ds.id, inputs=inputs, outputs=outputs, metadata=metadatas)
    print(f"Added {len(inputs)} examples. Dataset '{args.name}' now has "
          f"{len(existing) + len(inputs)} cases. Tag this version 'v1-baseline' in the UI.")


def _scn(cid: str) -> str:
    return {"H": "happy_path", "E": "edge_case", "F": "known_failure", "A": "adversarial"}.get(cid[0], "other")


if __name__ == "__main__":
    main()
