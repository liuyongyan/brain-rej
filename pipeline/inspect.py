"""Browse pipeline state.

Usage:
  python -m pipeline.inspect                # summary of all iterations
  python -m pipeline.inspect <N>            # detail of iteration N
  python -m pipeline.inspect diff <A> <B>   # plan diff between iter A and B
"""

import argparse
import json
import sys
import difflib
from pathlib import Path
from .core import STATE


def list_iterations():
    log = STATE / "iter_log.jsonl"
    if not log.exists():
        print("no iterations yet")
        return
    print(f"{'iter':>4}  {'hash':10}  {'#cand':>5}  {'#issues':>7}  top-5")
    print("-" * 80)
    for line in log.read_text().splitlines():
        d = json.loads(line)
        print(f"{d['iteration']:>4}  {d['plan_hash']}  {d['n_candidates']:>5}  "
              f"{d['n_issues']:>7}  {','.join(d['top_5'])}")
    esc = STATE / "escalations.md"
    if esc.exists():
        print(f"\nEscalations recorded — see {esc}")


def detail(n: int):
    d = STATE / f"iter_{n:03d}"
    if not d.exists():
        print(f"iter_{n:03d} does not exist")
        return
    plan = json.loads((d / "plan.json").read_text())
    result = json.loads((d / "result.json").read_text())
    issues = json.loads((d / "issues.json").read_text())

    print(f"=== Iteration {n} (plan v{plan['version']}) ===\n")
    print(f"top 10:")
    for r in result["top_n"][:10]:
        print(f"  {r.get('rank','?'):>3}  {r['receptor']:<12}  score={r.get('final_score',0):.2f}  "
              f"best_ligand={r.get('best_ligand')}  origin={r.get('origin','')}")
    print(f"\nmetrics: {result['metrics']}")
    print(f"\n{len(issues)} issues:")
    for iss in issues:
        mark = "[auto]" if iss.get("fix_recipe") else "[human]"
        print(f"  {mark} [{iss['severity']}] {iss['check_name']}")
        print(f"      evidence: {iss['evidence']}")
        if iss.get("escalation_question"):
            print(f"      ask: {iss['escalation_question']}")
    print(f"\nfiles: {d}/")


def diff_plans(a: int, b: int):
    pa = (STATE / f"iter_{a:03d}" / "plan.json").read_text()
    pb = (STATE / f"iter_{b:03d}" / "plan.json").read_text()
    pa = json.dumps(json.loads(pa), indent=2, sort_keys=True).splitlines()
    pb = json.dumps(json.loads(pb), indent=2, sort_keys=True).splitlines()
    for line in difflib.unified_diff(pa, pb, lineterm="",
                                     fromfile=f"iter_{a:03d}", tofile=f"iter_{b:03d}"):
        print(line)


def main():
    args = sys.argv[1:]
    if not args:
        list_iterations()
    elif args[0] == "diff" and len(args) == 3:
        diff_plans(int(args[1]), int(args[2]))
    elif args[0].isdigit():
        detail(int(args[0]))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
