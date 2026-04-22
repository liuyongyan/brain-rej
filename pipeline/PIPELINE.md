# Self-Improving Research Pipeline

A general framework for automated, self-critiquing biomedical data integration. The brain-rejuvenation receptor analysis is the seed application; the loop, critique, and revision machinery are domain-agnostic.

## Design

```
seed_plan ──> LOOP {
    analyze(plan_n) ──> result_n
    critique(result_n, plan_n) ──> [issues]               # two layers in sequence:
                                                          #   (a) hardcoded checks   (deterministic)
                                                          #   (b) LLM expert critic  (Claude Opus 4.7)
    triage(issues, tried_fixes) ──┐
                                  ├─ all auto-fixable: revise → plan_{n+1}
                                  ├─ any escalation:  STOP (write structured questions)
                                  ├─ no issues:       CONVERGED
                                  ├─ same issue twice: STOP (fix wasn't effective)
                                  └─ top-N stable across 2 iters: CONVERGED
} ──> {best_result, full_log, structured_escalations}
                                  │
                                  └──> regenerate PLAN.md + RESULTS.md from latest state
```

After every loop run (converged, escalated, or budget-exhausted), the orchestrator regenerates `PLAN.md` (problem + data + method) and `RESULTS.md` (findings) as final-product narrative documents from `state/`. Iteration internals stay in `state/iter_NNN/`.

## Core abstractions

### Plan (JSON, hashable)

A complete specification of the analysis. All thresholds, filter sets, scoring weights, and exclusion lists live here. **No hardcoded constants in `analyze.py`.**

Required fields: `version`, `data_sources`, `universe`, `evidence_streams`, `combination`, `filters_post`, `tried_fixes`.

### Result (JSON)

What `analyze` returns. Includes the top-N table, summary metrics, and a path to the full master tsv at `state/iter_NNN/master.tsv`.

### Issue

What a check returns when it fires:
- `check_name` — `snake_case` for hardcoded checks; LLM-generated issues are prefixed `llm:`
- `severity` — `critical` (pipeline-internal failure; immediate stop) | `major` | `minor` (advisory only)
- `evidence` — human-readable string with specific numbers/genes/citations
- `fix_recipe` — `{"action": "...", "args": {...}}` if auto-fixable, else `None`
- `escalation_question` — structured question for the human if not auto-fixable

### Check

A function `(result, plan) → Issue | None`. Two kinds:

1. **Hardcoded checks** in `pipeline/checks.py` — pattern-based detectors written in Python; deterministic; auto-fixable when the failure mode has a known recipe.
2. **LLM critic** in `pipeline/llm_critic.py` — Claude Opus 4.7 with adaptive thinking; reads `PLAN.md` + `RESULTS.md` + the latest result data; produces 2–5 expert critique items with structured-output schema enforcement; always escalates (never auto-fixes).

## Severity contract

| Severity | Behavior |
|---|---|
| `critical` | Pipeline-internal failure (analyze/check/revise crashed, missing data file). Stops loop immediately. |
| `major` | Data/methodology issue. Auto-fix if a recipe exists; otherwise escalate. |
| `minor` | Advisory only. Logged but never blocks the loop. |

## Loop control — exit conditions

| Condition | Outcome |
|---|---|
| 0 actionable issues fired | CONVERGED |
| Top-10 receptors stable across consecutive iterations (Jaccard ≥ 0.9) | CONVERGED |
| All remaining issues need human input (no `fix_recipe`) | ESCALATE |
| A `critical` severity issue fired | ESCALATE (immediate) |
| Same fix-hash already in `tried_fixes` | ESCALATE for that issue |
| Same issue check fired in two consecutive iterations | ESCALATE (fix not effective) |
| `--max-iter` reached | ESCALATE (budget exhausted) |
| `analyze` or `revise` raised an exception | ESCALATE with traceback |

## Escalations are structured

```markdown
ISSUE: {check_name}
SEVERITY: major | minor
EVIDENCE: {what triggered the check, with specific numbers}
QUESTION FOR HUMAN: {specific structured question}
```

Written to `state/escalations.md`. The human reads, edits `state/iter_NNN/plan.json` to apply their decision, and re-runs `python -m pipeline.loop --resume`.

## File layout

```
pipeline/
  PIPELINE.md          this file — architecture
  USAGE.md             day-to-day workflow
  loop.py              orchestrator (entry point: `python -m pipeline.loop`)
  inspect.py           CLI for browsing state (`python -m pipeline.inspect`)
  core.py              Plan, Result, Issue dataclasses + state I/O
  analyze.py           parameterized analysis engine
  checks.py            hardcoded critique checks (pattern-based)
  llm_critic.py        LLM-as-biomedical-expert critique layer (Claude Opus 4.7)
  revise.py            applies fix_recipes to produce next plan
  report.py            regenerates PLAN.md / RESULTS.md from latest state
  knowledge/*.tsv      editable curated gene lists (ECM, SomaScan-questionable, etc.)
state/
  iter_log.jsonl       one JSON line per iteration
  iter_NNN/            plan.json + result.json + issues.json + master.tsv
  escalations.md       structured questions appended at each escalation
```

## How to add a new failure mode

### Hardcoded check (deterministic, fast)

```python
# pipeline/checks.py
def check_<name>(result, plan) -> Issue | None:
    """Docstring becomes the inspect summary."""
    if <condition>:
        return Issue(
            check_name="<name>",
            severity="major",
            evidence=f"specific observation: ...",
            fix_recipe={"action": "...", "args": {...}},  # or None to escalate
        )

CHECKS.append(check_<name>)
```

If auto-fixable, add a handler in `revise.py` for the new `action` name. Next loop run picks it up automatically.

### Curated gene list (no code change)

Drop a TSV into `pipeline/knowledge/` with a `gene` column. Reference it from a check via `_load_gene_set("<filename>.tsv")`.

### Domain expertise the LLM should know

Edit `SYSTEM_PROMPT` in `pipeline/llm_critic.py` — the prompt is cached server-side, so changes take effect on the next iteration after a one-time cache rewrite.

## Why this design

- **Audit trail**: every iteration's plan + result + issues are preserved as JSON. The pipeline's reasoning is fully replayable.
- **Two critique layers complement each other**: hardcoded checks catch *known* failure modes cheaply and deterministically; the LLM critic catches *novel* issues that require domain reasoning the hardcoded set hasn't been written for yet.
- **Bounded**: 7 hard exit conditions prevent infinite loops or runaway spend.
- **Domain-agnostic core**: checks are pluggable. Same loop machinery serves brain-aging today and any other multi-source integration tomorrow.
- **Honest escalation**: when the system can't fix something, it asks a *specific* question with the conflicting evidence laid out — not vague "needs review".
- **Final products auto-update**: humans read `PLAN.md` and `RESULTS.md`; both reflect the latest iteration without manual maintenance.
