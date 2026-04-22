"""Pipeline orchestrator.

Usage:
  python -m pipeline.loop                       # resume from latest state, or seed if empty
  python -m pipeline.loop --fresh               # clear state and start from seed
  python -m pipeline.loop --plan PATH           # start from a specific plan JSON
  python -m pipeline.loop --max-iter N          # iteration budget (default 5)
  python -m pipeline.loop --resume              # explicitly continue from latest plan
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from .core import Plan, save_iteration, append_escalation, STATE
from .analyze import analyze
from .checks import critique
from .revise import revise
from .report import regenerate_all


def seed_plan() -> Plan:
    """The default starting plan — equivalent to the manually-coded analysis."""
    return Plan(
        version=1,
        data_sources={
            "ximerakis_cellchat": {"path": "ximerakis_2023/S20.xlsx"},
            "ximerakis_tpms":     {"path": "ximerakis_2023/S6.xlsx"},
            "lehallier_st1":      {"path": "lehallier_2019/suppl_tables.xlsx"},
            "lehallier_st4":      {"path": "lehallier_2019/suppl_tables.xlsx"},
            "lehallier_st14":     {"path": "lehallier_2019/suppl_tables.xlsx"},
            "jeffries_s7":        {"path": "jeffries_2025/2023-09-17055D-s3/S7_Linear_Model.xlsx"},
            "omnipath":           {"path": "resources/omnipath_ligrec.tsv"},
        },
        universe={
            "method": "cellchat_condition_dependent",
            "params": {"path": "ximerakis_2023/S20.xlsx",
                       "groups": ["RJV-restored", "aging-gained", "aging-lost", "AGA-induced"]},
        },
        evidence_streams=[
            {"name": "brain_doseresp", "method": "spearman_blood_age",
             "params": {"min_max_tpm": 1.0, "min_rho": 0.7}},
            {"name": "plasma", "method": "max_neglogq_with_wave7",
             "params": {"q_threshold": 0.05, "wave7_weight": 1.5,
                        "exclude_ligands": []}},
            {"name": "directional", "method": "consensus_stim_inh", "params": {}},
            {"name": "cross_species", "method": "jeffries_celltype_match", "params": {}},
        ],
        combination={"method": "stoufferZ", "params": {}},
        filters_post=[],
        tried_fixes=[],
    )


def latest_iter_index() -> int:
    """Highest existing iter_NNN dir, or -1 if none."""
    dirs = [d for d in STATE.glob("iter_*") if d.is_dir() and d.name.split("_")[1].isdigit()]
    if not dirs: return -1
    return max(int(d.name.split("_")[1]) for d in dirs)


def load_plan(path: Path) -> Plan:
    return Plan.from_json(path.read_text())


def clear_state():
    """Delete iter_*/ dirs, log, escalations. Used by --fresh."""
    import shutil
    for d in STATE.glob("iter_*"):
        if d.is_dir():
            shutil.rmtree(d)
    for f in ["iter_log.jsonl", "escalations.md"]:
        p = STATE / f
        if p.exists(): p.unlink()


def safe_critique(result, plan):
    """Run hardcoded checks + LLM critique, swallowing per-check exceptions so
    one bad check doesn't kill the loop."""
    out = []
    from .checks import CHECKS
    for c in CHECKS:
        try:
            iss = c(result, plan)
            if iss is not None:
                out.append(iss)
        except Exception as e:
            out.append(_internal_issue(
                check_name=f"{c.__name__}_failed",
                evidence=f"Check raised exception: {type(e).__name__}: {e}",
            ))
    # LLM-as-biomedical-expert layer (skipped if PIPELINE_NO_LLM=1 or no API key)
    try:
        from .llm_critic import llm_review
        out.extend(llm_review(result, plan))
    except Exception as e:
        out.append(_internal_issue(
            check_name="llm_critic_failed",
            evidence=f"LLM critic module raised: {type(e).__name__}: {e}",
        ))
    return out


def _internal_issue(check_name, evidence):
    """An Issue representing a pipeline-internal failure (not a data issue)."""
    from .core import Issue
    return Issue(
        check_name=check_name,
        severity="critical",
        evidence=evidence,
        fix_recipe=None,
        escalation_question="A check or fix raised an exception — this is a "
                            "pipeline bug, not a data problem. See traceback.",
    )


def triage(issues, tried_fixes_set, iter_idx, last_issue_names):
    """Decide what to do.

    Severity contract:
      critical = pipeline-internal failure (analyze/check/revise crashed,
                 missing data file). Stops loop immediately.
      major    = data/methodology issue. Auto-fix if possible, else escalate.
      minor    = log only; never blocks the loop.
    """
    # critical = pipeline-internal failure, always escalate immediately
    critical = [i for i in issues if i.severity == "critical"]
    if critical:
        return {"action": "escalate", "issues": critical,
                "reason": f"{len(critical)} critical pipeline failure(s)"}

    # minor issues never block; treat as advisory
    actionable = [i for i in issues if i.severity != "minor"]
    if not actionable:
        return {"action": "converged", "reason": "no actionable issues"}

    fixable = [i for i in actionable if i.fix_recipe is not None]
    escalations = [i for i in actionable if i.fix_recipe is None]

    # fix already tried but issue still firing → escalate it
    for i in fixable:
        if i.fix_hash() in tried_fixes_set and i not in escalations:
            escalations.append(i)

    # issue fired in two consecutive iterations → escalate (fix not effective)
    if iter_idx > 0:
        for i in actionable:
            if i.check_name in last_issue_names and i not in escalations:
                escalations.append(i)

    new_fixable = [i for i in fixable
                   if i.fix_hash() not in tried_fixes_set
                   and i not in escalations]

    if not new_fixable and escalations:
        return {"action": "escalate", "issues": escalations,
                "reason": "no auto-fixable issues remain"}
    if not new_fixable and not escalations:
        return {"action": "converged", "reason": "no actionable issues"}
    return {"action": "fix_and_log", "fix": new_fixable, "escalations": escalations}


def run(start_plan: Plan, start_iter: int, max_iter: int):
    plan = start_plan
    last_issue_names: list[str] = []
    tried_fixes_set: set[str] = set(plan.tried_fixes)
    last_top: list[str] = []
    result = None

    for offset in range(max_iter):
        i = start_iter + offset
        print(f"\n{'='*60}\nITERATION {i}  (plan v{plan.version}, hash {plan.hash()})\n{'='*60}")

        try:
            result = analyze(plan, iteration=i)
        except Exception as e:
            traceback.print_exc()
            print(f"\n>>> ANALYZE FAILED in iter {i} — escalating")
            issues = [_internal_issue("analyze_crashed",
                                       f"{type(e).__name__}: {e}\n{traceback.format_exc()}")]
            save_iteration(i, plan, _empty_result(i, plan), issues)
            append_escalation(i, issues, "analyze raised exception")
            return None

        print(f"  top 5: {[r['receptor'] for r in result.top_n[:5]]}")
        print(f"  metrics: {result.metrics}")

        issues = safe_critique(result, plan)
        print(f"  {len(issues)} issues fired:")
        for iss in issues:
            mark = "✓auto" if iss.fix_recipe else "→human"
            print(f"    [{iss.severity}] {iss.check_name} ({mark})")

        save_iteration(i, plan, result, issues)

        # Jaccard convergence on top-10
        cur_top = [r["receptor"] for r in result.top_n[:10]]
        if i > start_iter and last_top:
            inter = len(set(cur_top) & set(last_top))
            union = len(set(cur_top) | set(last_top))
            jaccard = inter / union if union else 1.0
            if jaccard >= 0.9:
                print(f"\n>>> CONVERGED: top-10 Jaccard = {jaccard:.2f} ≥ 0.9")
                return result

        decision = triage(issues, tried_fixes_set, i, last_issue_names)
        print(f"\n  triage: {decision['action']}")

        if decision["action"] == "converged":
            print(f">>> CONVERGED: {decision['reason']}")
            return result
        if decision["action"] == "escalate":
            append_escalation(i, decision["issues"], decision["reason"])
            print(f">>> ESCALATED ({decision['reason']}); see state/escalations.md")
            print(f">>> To resume after addressing: edit state/iter_{i:03d}/plan.json then "
                  f"`python -m pipeline.loop --resume`")
            return result

        fixes = decision.get("fix", [])
        if "escalations" in decision and decision["escalations"]:
            append_escalation(i, decision["escalations"], "human review while loop continues")
            print(f"  (also logged {len(decision['escalations'])} items for human review)")

        try:
            plan = revise(plan, fixes)
        except Exception as e:
            traceback.print_exc()
            print(f"\n>>> REVISE FAILED at iter {i}")
            issues = [_internal_issue("revise_crashed",
                                       f"{type(e).__name__}: {e}\n{traceback.format_exc()}")]
            append_escalation(i, issues, "revise raised exception")
            return result

        tried_fixes_set.update(f.fix_hash() for f in fixes if f.fix_hash())
        last_issue_names = [iss.check_name for iss in issues]
        last_top = cur_top

    print(f"\n>>> BUDGET EXHAUSTED after {max_iter} iterations from {start_iter}")
    return result


def _empty_result(i, plan):
    from .core import Result
    return Result(iteration=i, plan_hash=plan.hash(),
                  n_candidates=0, top_n=[], metrics={}, raw_table_path="")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-iter", type=int, default=5,
                    help="iteration budget for this run (default 5)")
    ap.add_argument("--fresh", action="store_true",
                    help="clear state/* and start from seed plan")
    ap.add_argument("--plan", type=str, default=None,
                    help="path to a Plan JSON to start from")
    ap.add_argument("--resume", action="store_true",
                    help="continue from the latest iter_NNN/plan.json")
    ap.add_argument("--no-llm", action="store_true",
                    help="disable the LLM-as-biomedical-expert critique layer")
    args = ap.parse_args()

    if args.no_llm:
        os.environ["PIPELINE_NO_LLM"] = "1"
        print("LLM critic disabled (--no-llm)")

    if args.fresh:
        clear_state()
        print("state/* cleared")

    latest = latest_iter_index()
    if args.plan:
        plan = load_plan(Path(args.plan))
        start_iter = latest + 1 if latest >= 0 else 0
        print(f"loaded plan from {args.plan}; starting at iter {start_iter}")
    elif args.resume or (latest >= 0 and not args.fresh):
        if latest < 0:
            print("--resume requested but no prior iterations exist; using seed_plan")
            plan = seed_plan()
            start_iter = 0
        else:
            plan = load_plan(STATE / f"iter_{latest:03d}" / "plan.json")
            start_iter = latest + 1
            print(f"resuming from iter {latest}; next = iter {start_iter}")
            # warn if plan unchanged from previous iteration: re-running gets same answer
            if latest >= 1:
                prev = load_plan(STATE / f"iter_{latest-1:03d}" / "plan.json")
                if plan.hash() == prev.hash():
                    print(f"\n  ⚠️  WARNING: plan in iter_{latest:03d} is identical to iter_{latest-1:03d}.")
                    print(f"     Resuming will produce the same result. Edit state/iter_{latest:03d}/plan.json")
                    print(f"     to address the escalations before resuming.\n")
    else:
        plan = seed_plan()
        start_iter = 0
        print("starting from seed plan at iter 0")

    final = run(plan, start_iter, args.max_iter)
    print(f"\n=== FINAL ===")
    if final and final.top_n:
        print(f"top 5: {[r['receptor'] for r in final.top_n[:5]]}")
    # always regenerate top-level docs so PLAN.md / RESULTS.md reflect latest state
    try:
        regenerate_all()
        print("PLAN.md and RESULTS.md regenerated from latest state")
    except Exception as e:
        print(f"WARNING: failed to regenerate top-level docs: {e}")
    print(f"see state/iter_log.jsonl for full trace")


if __name__ == "__main__":
    main()
