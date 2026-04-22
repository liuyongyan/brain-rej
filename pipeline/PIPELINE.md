# Self-Improving Research Pipeline

A general framework for automated, self-critiquing biomedical data integration.

## Design

```
seed_plan ──> LOOP {
    analyze(plan_n) ──> result_n
    critique(result_n, plan_n) ──> [issues]
    triage(issues, tried_fixes) ──┐
                                  ├─ all auto-fixable: revise → plan_{n+1}
                                  ├─ any escalation:  STOP
                                  ├─ no issues:       CONVERGED
                                  └─ same issue twice: STOP
} ──> {best_result, full_log, escalations}
```

## Core abstractions

### Plan (JSON, hashable)

A complete specification of the analysis. All thresholds, filter sets, scoring weights, and exclusion lists live here. **No hardcoded constants in `analyze.py`.**

Required fields: `version`, `data_sources`, `universe`, `evidence_streams`, `combination`, `filters_post`, `tried_fixes`.

### Result (JSON)

What `analyze` returns. Includes the top-N table, summary metrics, and a path to the full output tsv.

### Issue

What a check returns when it fires:
- `check_name`
- `severity`: `critical | major | minor`
- `evidence`: human-readable string
- `fix_recipe`: dict with `action` + `args`, or `None` if not auto-fixable
- `escalation_question`: structured question for human, or `None`

### Check

A function `(result, plan) → Issue | None`. Checks live in `checks.py` as a registered list. Adding a new failure mode = adding one function.

## Loop control — exit conditions

| Condition | Outcome |
|---|---|
| 0 issues fired | CONVERGED |
| All issues are auto-fixable, none in `tried_fixes` | revise → next iteration |
| Same issue fired in 2 consecutive iterations | ESCALATE (fix didn't work) |
| Any issue with no fix_recipe | ESCALATE (needs human) |
| `max_iter` reached | ESCALATE (budget exhausted) |
| Top-N stable for 2 iterations (Jaccard ≥ 0.9) | CONVERGED |

## What gets escalated

Escalations are not "I'm confused". They are structured:

```
ISSUE: {check_name}
EVIDENCE: {what triggered the check}
WHY ESCALATED: {one of: no_fix_available | fix_didn't_work | judgment_call}
QUESTION FOR HUMAN: {specific structured question}
RELEVANT FILES: {paths}
SUGGESTED ALTERNATIVES (with pros/cons): [...]
```

## File layout

```
pipeline/
  PIPELINE.md            this file
  loop.py                orchestrator (entry point)
  core.py                Plan, Result, Issue dataclasses + state mgmt
  analyze.py             parameterized analysis (refactored from integrate.py)
  checks.py              all checks in one place
  revise.py              applies fix_recipes
state/
  seed_plan.json         iteration-0 plan
  iter_log.jsonl         one line per iteration
  iter_NNN/              snapshot per iteration (plan, result, issues)
  escalations.md         appended each time pipeline escalates
```

## How to add a new failure mode

1. Add a check function to `checks.py`:
   ```python
   def check_<name>(result, plan) -> Optional[Issue]: ...
   register(check_<name>)
   ```
2. If auto-fixable: add a handler in `revise.py` for the new `action` name.
3. The next loop run will catch it automatically.

## Why this design

- **Audit trail**: every iteration's plan + result + issues are preserved as JSON. The pipeline's reasoning is fully replayable.
- **Bounded**: 5 hard exit conditions prevent infinite loops.
- **Domain-agnostic core**: checks are pluggable. Same loop machinery serves brain-aging today and any other integration tomorrow.
- **Honest escalation**: when the system can't fix something, it asks a *specific* question with the conflicting evidence laid out — not vague "needs review".
