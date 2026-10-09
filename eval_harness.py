"""
Week 4 — Option C evaluation harness (single system per process).

Runs the 30-case golden dataset through ONE recovery co-pilot architecture and
records, per case: classification (GREEN/YELLOW/RED), escalation tier, the
discharge-determined care plan, and token/cost/latency from the system's own
usage tracker.

Invoke once per system, each with that project's venv:

    week3/nexus/venv/Scripts/python.exe eval_harness.py --system nexus
    week3/hub/venv/Scripts/python.exe   eval_harness.py --system hub

Why subprocess-per-system: Nexus and Hub both expose a module named
`agents.orchestrator` (different APIs, different venvs), so they cannot be
imported into the same Python process. Running each in its own process also
keeps the two LangSmith projects (nexus-eval / hub-eval) cleanly separated.

Design notes (documented for the write-up):
  * The discharge body is identical within a diagnosis (only patient name /
    hospital / dates differ), and the care plan is discharge-determined, not
    check-in-determined. So intake+care-plan is run ONCE per unique discharge
    template (5 of them) and reused; monitoring+escalation runs per case (30).
  * Escalation tier: the system only invokes the tiered escalation logic on RED.
    YELLOW is routed to the admin path (no tier). For scoring we map the system's
    effective response tier as GREEN->NONE, YELLOW->TIER_1 (its warranted "call
    within 24h" level), RED->the keyword-derived tier from escalation_agent.
    Tier therefore adds information beyond classification only on RED cases
    (TIER_2 vs TIER_3) — exactly where the documented keyword-matching gap lives.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # MasteringGenAI/
GOLDEN_PATH = HERE / "golden_dataset_cases.json"


def system_root(system: str) -> Path:
    """Where the nexus / hub checkout lives: $NEXUS_DIR / $HUB_DIR if set, else the
    original MasteringGenAI/week3/<system> layout, else a sibling clone (../<system>)."""
    env = os.getenv(f"{system.upper()}_DIR")
    if env:
        return Path(env).resolve()
    legacy = REPO / "week3" / system
    return legacy if legacy.exists() else HERE.parent / system


# ──────────────────────────────────────────────────────────────────────────────
# System wiring: insert the chosen project root on sys.path and stub out the
# external side effects we don't want firing 30x (Pinecone upserts, SMS/email).
# OpenFDA and the real Anthropic calls are KEPT — they are the pipeline under test.
# ──────────────────────────────────────────────────────────────────────────────

def _enable_langsmith(system: str, variant: str = "v1") -> bool:
    """Turn on LangSmith tracing for this system's run. MUST run before importing the
    orchestrator, because agents do `from anthropic import Anthropic` at import time —
    we patch the class so every `Anthropic()` they construct is trace-wrapped.

    Returns True if tracing was enabled (key present), else False (no-op)."""
    if not (os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")):
        print("[langsmith] no LANGCHAIN_API_KEY found — running WITHOUT tracing (local metrics only).")
        return False
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    proj = f"{system}-eval" if variant == "v1" else f"{system}-eval-{variant}"
    os.environ.setdefault("LANGCHAIN_PROJECT", proj)
    try:
        import anthropic
        from langsmith.wrappers import wrap_anthropic
        _orig = anthropic.Anthropic
        anthropic.Anthropic = lambda *a, **k: wrap_anthropic(_orig(*a, **k))  # type: ignore
        print(f"[langsmith] tracing ON -> project '{os.environ['LANGCHAIN_PROJECT']}'")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[langsmith] could not enable tracing ({e}); continuing local-only.")
        return False


def _load_system(system: str, langsmith: bool = False, variant: str = "v1"):
    if system not in ("nexus", "hub"):
        raise SystemExit(f"--system must be nexus|hub, got {system!r}")
    root = system_root(system)

    os.chdir(root)                       # agents write/read relative data/ paths
    sys.path.insert(0, str(root))

    # Load this project's .env BEFORE importing tools (pinecone_store reads keys at import).
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=root / ".env", override=True)

    # Enable tracing (if requested + key present) BEFORE importing agents.
    if langsmith:
        _enable_langsmith(system, variant)

    # Stub Pinecone storage (avoid polluting the prod index with 30 test patients).
    import tools.pinecone_store as pcs
    pcs.store_discharge_summary = lambda *a, **k: True
    pcs.store_check_in = lambda *a, **k: True
    pcs.retrieve_patient_history = lambda *a, **k: []

    # Stub outbound notifications (belt-and-suspenders; consent flags are off anyway).
    import tools.notification_tool as nt
    nt.send_sms_notification = lambda *a, **k: {"success": True, "stubbed": True}
    nt.send_email_notification = lambda *a, **k: {"success": True, "stubbed": True}

    import agents.orchestrator as orch
    return root, orch


# ──────────────────────────────────────────────────────────────────────────────
# Discharge text -> one (or more) page PDF, so the REAL intake PDF-parse path runs.
# ──────────────────────────────────────────────────────────────────────────────

def text_to_pdf(text: str, path: Path) -> None:
    import fitz  # PyMuPDF (already a project dep)
    doc = fitz.open()
    remaining = text
    while True:
        page = doc.new_page()
        rect = fitz.Rect(40, 40, 555, 800)
        # insert_textbox returns a negative number if text overflowed the box.
        leftover = page.insert_textbox(rect, remaining, fontsize=9, fontname="helv")
        if leftover >= 0:
            break
        # crude re-paginate: drop the characters that fit, keep the rest.
        # estimate fit by binary search on length until it fits one page.
        lo, hi = 0, len(remaining)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            probe = fitz.open()
            p = probe.new_page()
            if p.insert_textbox(rect, remaining[:mid], fontsize=9, fontname="helv") >= 0:
                lo = mid
            else:
                hi = mid - 1
            probe.close()
        # rewrite this page with only what fits, continue with the rest
        doc.delete_page(doc.page_count - 1)
        page = doc.new_page()
        page.insert_textbox(rect, remaining[:lo], fontsize=9, fontname="helv")
        remaining = remaining[lo:]
        if not remaining.strip():
            break
    doc.save(str(path))
    doc.close()


# ──────────────────────────────────────────────────────────────────────────────
# Golden-case -> RecoveryState helpers.
# ──────────────────────────────────────────────────────────────────────────────

def base_state(patient_id: str, patient_name: str, discharge_date: str, pdf_path: str) -> dict:
    return {
        "patient_id": patient_id,
        "patient_name": patient_name,
        "caregiver_name": None,
        "caregiver_email": None,                # unset -> admin sends no email
        "emergency_contact_name": None,
        "emergency_contact_phone": None,
        "emergency_contact_consented": False,   # off -> escalation sends no SMS
        "discharge_date": discharge_date,
        "recovery_day": 1,
        "diagnosis": "Unknown",
        "icd10_code": None,
        "medications": [],
        "appointments": [],
        "warning_signs_er": [],
        "warning_signs_call": [],
        "dietary_restrictions": [],
        "activity_restrictions": [],
        "current_agent": "intake_agent",
        "needs_clarification": [],
        "active_flags": [],
        "human_approval_queue": [],
        "check_in_history": [],
        "daily_vitals_log": [],
        "hospitalization_history": [],
        "messages": [],
        "intake_complete": False,
        "care_plan_complete": False,
        "last_check_in_date": None,
        "pinecone_namespace": f"patient_{patient_id}",
        "pdf_path": pdf_path,
        "token_log": [],
    }


def template_key(discharge_text: str) -> str:
    """Hash the medical body (from 'Diagnosis:' onward) so identical discharge
    content across patients maps to one template. Falls back to full-text hash."""
    idx = discharge_text.find("Diagnosis:")
    body = discharge_text[idx:] if idx != -1 else discharge_text
    return hashlib.md5(body.encode("utf-8")).hexdigest()[:12]


def patient_name_from(discharge_text: str) -> str:
    for line in discharge_text.splitlines():
        if line.startswith("Patient:"):
            return line.split("|")[0].replace("Patient:", "").strip()
    return "Test Patient"


def discharge_date_from(discharge_text: str) -> str:
    for line in discharge_text.splitlines():
        if "Discharge date:" in line:
            return line.split("Discharge date:")[1].strip().split("|")[0].strip()
    return "2026-05-01"


def map_checkin(checkin: dict, medications: list) -> dict:
    """Reproduce the UI's check-in submission dict from a golden case.

    - Medication adherence keyed `med_<normalized name>` to mirror generate_checkin_questions.
    - `weight`, `general_feeling`, `concerns` keys the agents read for vitals.
    - All remaining vitals / other_fields passed through so the classifier LLM sees them.
    """
    responses: dict = {}
    taken = checkin.get("medications_taken", {})

    # Match golden display names onto the names intake actually extracted.
    for med in medications:
        if med.get("interaction_flag"):
            continue
        name = med["name"]
        key = f"med_{name.replace(' ', '_').lower()}"
        val = None
        for disp, was_taken in taken.items():
            if disp.lower() in name.lower() or name.lower() in disp.lower():
                val = bool(was_taken)
                break
        if val is None:
            val = True  # not asked about -> assume taken (neutral)
        responses[key] = val

    if checkin.get("weight_lbs") is not None:
        responses["weight"] = checkin["weight_lbs"]
    responses["concerns"] = checkin.get("free_text_concern", "")

    # Pass through every other reported field verbatim (vitals + other_fields).
    for k, v in checkin.items():
        if k in ("medications_taken", "free_text_concern", "weight_lbs", "day"):
            continue
        if k == "other_fields" and isinstance(v, dict):
            for ok, ov in v.items():
                responses.setdefault(ok, ov)
        else:
            responses.setdefault(k, v)
    return responses


def build_prior_history(case: dict, medications: list) -> list:
    """Convert prior_checkin_history into check_in_record-shaped entries so the
    monitoring agent's trend logic (3 consecutive YELLOW -> RED) can see them."""
    history = []
    for prior in case.get("prior_checkin_history", []):
        history.append({
            "day": prior.get("day"),
            "timestamp": f"2026-05-01T00:00:{prior.get('day', 0):02d}",
            "responses": map_checkin(prior, medications),
            "classification": prior.get("expected_classification", "YELLOW"),
            "flags": [],
            "summary": prior.get("free_text_concern", "")[:160],
            "recommended_action": "",
        })
    return history


def system_tier(classification: str, escalation_tier) -> str:
    """Documented mapping from system output to a comparable escalation tier."""
    if classification == "RED":
        return escalation_tier or "TIER_1"
    if classification == "YELLOW":
        return "TIER_1"
    return "NONE"


# ──────────────────────────────────────────────────────────────────────────────
# Per-system run drivers.
# ──────────────────────────────────────────────────────────────────────────────

def run_intake_careplan(system: str, orch, state: dict) -> dict:
    if system == "nexus":
        return orch.intake_graph.invoke(state)
    else:  # hub
        state["phase"] = "intake"
        return orch.run_intake(state)


def run_checkin(system: str, orch, state: dict, responses: dict) -> dict:
    if system == "nexus":
        state["todays_checkin_responses"] = responses
        return orch.monitoring_graph.invoke(state)
    else:  # hub
        return orch.run_daily_checkin(state, responses)


# ──────────────────────────────────────────────────────────────────────────────
# Main.
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=["nexus", "hub"])
    ap.add_argument("--limit", type=int, default=0, help="run only the first N cases (smoke test)")
    ap.add_argument("--only", type=str, default="", help="comma-separated case_ids to run")
    ap.add_argument("--langsmith", action="store_true",
                    help="trace this run into LangSmith project <system>-eval (needs LANGCHAIN_API_KEY)")
    ap.add_argument("--variant", choices=["v1", "v2"], default="v1",
                    help="v2 enables the HUB_V2 improvements (semantic tier, trend floor, dyspnea guardrail)")
    args = ap.parse_args()

    if args.variant == "v2":
        os.environ["HUB_V2"] = "1"  # read at call-time by Hub's escalation + monitoring logic
        print("[harness] HUB_V2 improvements ENABLED for this run.")

    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if args.only:
        wanted = {c.strip() for c in args.only.split(",")}
        cases = [c for c in cases if c["case_id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    root, orch = _load_system(args.system, langsmith=args.langsmith, variant=args.variant)

    pdf_dir = HERE / "_pdfs"
    pdf_dir.mkdir(exist_ok=True)

    # Cache of discharge-determined state (intake + care plan) per template.
    template_cache: dict = {}

    results = []
    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        dtext = case["discharge_summary_text"]
        tkey = template_key(dtext)
        print(f"\n{'='*70}\n[{args.system}] case {i}/{len(cases)}  {cid}  (template {tkey})\n{'='*70}")

        if tkey not in template_cache:
            pdf_path = pdf_dir / f"{tkey}.pdf"
            if not pdf_path.exists():
                text_to_pdf(dtext, pdf_path)
            st = base_state(
                patient_id=f"tmpl_{tkey}",
                patient_name=patient_name_from(dtext),
                discharge_date=discharge_date_from(dtext),
                pdf_path=str(pdf_path),
            )
            t0 = time.perf_counter()
            st = run_intake_careplan(args.system, orch, st)
            intake_secs = time.perf_counter() - t0
            template_cache[tkey] = {
                "diagnosis": st.get("diagnosis"),
                "icd10_code": st.get("icd10_code"),
                "medications": st.get("medications", []),
                "appointments": st.get("appointments", []),
                "warning_signs_er": st.get("warning_signs_er", []),
                "warning_signs_call": st.get("warning_signs_call", []),
                "dietary_restrictions": st.get("dietary_restrictions", []),
                "activity_restrictions": st.get("activity_restrictions", []),
                "care_plan": st.get("care_plan", {}),
                "discharge_token_log": list(st.get("token_log", [])),
                "intake_secs": round(intake_secs, 2),
            }
            print(f"[{args.system}] template {tkey} cached: dx={template_cache[tkey]['diagnosis']!r}, "
                  f"{len(template_cache[tkey]['medications'])} meds")

        tmpl = template_cache[tkey]
        meds = tmpl["medications"]

        # Fresh per-case state seeded from the cached discharge extraction.
        pdf_path = pdf_dir / f"{tkey}.pdf"
        st = base_state(
            patient_id=f"case_{cid}",
            patient_name=patient_name_from(dtext),
            discharge_date=discharge_date_from(dtext),
            pdf_path=str(pdf_path),
        )
        st.update({
            "diagnosis": tmpl["diagnosis"],
            "icd10_code": tmpl["icd10_code"],
            "medications": meds,
            "appointments": tmpl["appointments"],
            "warning_signs_er": tmpl["warning_signs_er"],
            "warning_signs_call": tmpl["warning_signs_call"],
            "dietary_restrictions": tmpl["dietary_restrictions"],
            "activity_restrictions": tmpl["activity_restrictions"],
            "care_plan": tmpl["care_plan"],
            "intake_complete": True,
            "care_plan_complete": True,
        })

        checkin = case["checkin_response"]
        st["recovery_day"] = checkin.get("day", 1)
        st["check_in_history"] = build_prior_history(case, meds)
        responses = map_checkin(checkin, meds)

        t0 = time.perf_counter()
        try:
            st = run_checkin(args.system, orch, st, responses)
            err = None
        except Exception as e:  # noqa: BLE001 — record, don't crash the whole run
            err = f"{type(e).__name__}: {e}"
            print(f"[{args.system}] {cid} ERROR: {err}")
        checkin_secs = time.perf_counter() - t0

        mon = st.get("last_monitoring_result", {}) if not err else {}
        classification = mon.get("classification") if not err else "ERROR"
        esc_tier = st.get("escalation_tier") if not err else None
        derived_tier = system_tier(classification, esc_tier) if not err else "ERROR"

        # token_log here holds only this check-in's monitoring call; add the
        # discharge (intake+careplan) entries for a full per-run accounting.
        checkin_log = list(st.get("token_log", []))
        full_log = tmpl["discharge_token_log"] + checkin_log

        results.append({
            "case_id": cid,
            "scenario": _scenario(cid),
            "template": tkey,
            "diagnosis": tmpl["diagnosis"],
            "expected_classification": case["expected_classification"],
            "expected_escalation_tier": case["expected_escalation_tier"],
            "got_classification": classification,
            "got_escalation_tier_raw": esc_tier,
            "got_escalation_tier": derived_tier,
            "classification_correct": (classification == case["expected_classification"]),
            "tier_correct": (derived_tier == case["expected_escalation_tier"]),
            "flags": mon.get("flags", []),
            "summary": mon.get("summary", ""),
            "checkin_secs": round(checkin_secs, 2),
            "checkin_token_log": checkin_log,
            "discharge_token_log": tmpl["discharge_token_log"],
            "care_plan": tmpl["care_plan"],
            "error": err,
        })

    suffix = "" if args.variant == "v1" else f"_{args.variant}"
    out = HERE / f"results_{args.system}{suffix}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    n = len(results)
    cls_ok = sum(r["classification_correct"] for r in results)
    tier_ok = sum(r["tier_correct"] for r in results)
    print(f"\n{'#'*70}")
    print(f"[{args.system} {args.variant}] DONE  {n} cases")
    print(f"  classification accuracy : {cls_ok}/{n} = {cls_ok/n:.1%}")
    print(f"  escalation-tier accuracy: {tier_ok}/{n} = {tier_ok/n:.1%}")
    print(f"  results -> {out}")
    print(f"{'#'*70}")


def _scenario(cid: str) -> str:
    return {"H": "happy_path", "E": "edge_case", "F": "known_failure", "A": "adversarial"}.get(cid[0], "other")


if __name__ == "__main__":
    main()
