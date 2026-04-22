# Pipeline Usage

## Setup (once)

```bash
pip install -r requirements.txt
bash scripts/download_data.sh                  # ~770 MB
export ANTHROPIC_API_KEY=sk-ant-...            # optional but recommended
```

The pipeline runs without `ANTHROPIC_API_KEY` (the LLM critic gracefully degrades to a single `llm:no_api_key` minor issue), but the expert review is the most valuable part of each iteration.

## Three commands you'll actually use

```bash
# Run the loop (resumes from latest state by default; uses seed plan if state is empty)
python -m pipeline.loop

# Browse what happened
python -m pipeline.inspect              # summary of all iterations
python -m pipeline.inspect 1            # detail of iteration 1 (top hits, issues, files)
python -m pipeline.inspect diff 0 1     # what changed in the plan between iter 0 and 1
```

After every loop run, **`PLAN.md` and `RESULTS.md` at the repo root are auto-regenerated** from the latest state. Read those for the current best understanding; read `state/escalations.md` for what needs your input.

## CLI reference for `python -m pipeline.loop`

| Flag | Purpose |
|---|---|
| `--fresh` | Clear `state/` and start from the seed plan |
| `--resume` | Continue from the latest `iter_NNN/plan.json` (default behavior if state exists) |
| `--plan PATH` | Start from a specific plan JSON file |
| `--max-iter N` | Iteration budget for this run (default 5) |
| `--no-llm` | Skip the LLM critic (sets `PIPELINE_NO_LLM=1` for this run) |

## The two critique layers

Each iteration runs two critique layers in sequence:

1. **Hardcoded checks** (`pipeline/checks.py`) — fast (~ms), deterministic, free. Each check is a Python function that detects a specific known failure mode (ECM-ligand inflation, missing permutation null, SomaScan crossreactivity for top hit, etc.). Auto-fixable when the failure mode has a known recipe; cheap to extend.
2. **LLM-as-biomedical-expert** (`pipeline/llm_critic.py`) — slower (~30–60s/iter), uses Claude Opus 4.7 with adaptive thinking. Reads `PLAN.md` + `RESULTS.md` + the latest result data and produces 2–5 expert critique items the hardcoded checks would miss. Always escalates to human (does not auto-fix). Cost ~$0.10–$0.30/iter after the system prompt is cached.

| Setting | Default | Override |
|---|---|---|
| LLM critic enabled | yes (if `ANTHROPIC_API_KEY` set) | `--no-llm` flag |
| Model | `claude-opus-4-7` | `LLM_CRITIC_MODEL=claude-sonnet-4-6` |
| Effort | `high` | `LLM_CRITIC_EFFORT=xhigh` (hardest reviews) or `medium` (cheaper) |
| Caching | system prompt is cached server-side (5-min TTL) | edit `SYSTEM_PROMPT` in `llm_critic.py` to invalidate |

## The lifecycle

```
fresh start  →  loop runs  →  hits escalation  →  you read & decide  →  edit plan  →  resume
                                                                         ↓
                                                                    converged? done.
```

### Step 1 — Start fresh
```bash
python -m pipeline.loop --fresh --max-iter 5
```
Clears `state/`, runs from the seed plan, up to 5 iterations.

### Step 2 — Read what the loop produced
```bash
python -m pipeline.inspect              # iteration summary
cat RESULTS.md                          # current best findings (auto-regenerated)
cat PLAN.md                             # current methodology (auto-regenerated)
cat state/escalations.md                # what needs your input
python -m pipeline.inspect 2            # detail of iteration 2
python -m pipeline.inspect diff 0 1     # see how the plan evolved
```

### Step 3 — Address an escalation
The loop saved its current plan at `state/iter_NNN/plan.json` (highest N). Edit it directly to apply your decision. Examples:

**Escalation: "authorize Pálovics download?"** → Add a `palovics` data source to the plan:
```json
"data_sources": {
  "palovics": {"path": "palovics_2022/brain_subset.h5ad"}
}
```

**Escalation: "single-receptor framing brittle, report tier?"** → no plan change needed; this is a presentation choice. Read the top-10 in `state/iter_NNN/master.tsv` and decide.

**Escalation: "broaden universe?"** → Edit the `universe` block:
```json
"universe": {
  "method": "cellchat_all_receptors",
  "params": {...}
}
```

**LLM-flagged escalation** (prefix `llm:`) — the LLM's question is in `escalation_question`. Many of these don't need a plan edit; they're directional ("consider also checking dataset X") that you address by either editing the plan or just noting.

### Step 4 — Resume
```bash
python -m pipeline.loop --resume
```
Loads the latest `iter_NNN/plan.json`, continues from `iter_{N+1}`. The pipeline warns if your plan is unchanged from the previous iteration (re-running with the same plan gives the same result).

Or to start from a hand-edited plan elsewhere:
```bash
python -m pipeline.loop --plan path/to/my_plan.json
```

## Adding a new hardcoded check

You discovered a new way analyses can go wrong. Codify it:

```python
# pipeline/checks.py
def check_top_hits_share_ligand(result, plan):
    """If 2+ of top 5 share the same ligand, ranking is multi-collinear."""
    from collections import Counter
    top5 = result.top_n[:5]
    most_common, n = Counter(r["best_ligand"] for r in top5).most_common(1)[0]
    if n >= 2:
        return Issue(
            check_name="top_hits_share_ligand",
            severity="major",
            evidence=f"{n} of top 5 share ligand {most_common}",
            fix_recipe=None,
            escalation_question=f"Top hits share {most_common} (multicollinearity). "
                                f"Collapse to one representative or keep all?",
        )

CHECKS.append(check_top_hits_share_ligand)
```

Re-run `python -m pipeline.loop --resume` — the new check fires automatically.

If the check is auto-fixable, also add a handler to `revise.py`:
```python
elif action == "merge_share_ligand_hits":
    # ... your fix here
```

## Adding domain knowledge for the LLM critic

The LLM critic reads its expertise from `SYSTEM_PROMPT` in `pipeline/llm_critic.py`. To teach it about a new controversy, dataset, or methodology pitfall, edit the relevant section. The prompt is cached server-side, so the first iteration after the edit pays a one-time cache rewrite (~$0.05); subsequent iterations within the 5-minute TTL hit the new cache.

Keep it substantive — the prompt is currently ~4.2K tokens to clear Opus 4.7's cache floor. Don't pad with filler; add real content (named papers, specific datasets, mechanistic detail).

## Adding a new curated gene list

Knowledge files in `pipeline/knowledge/` are TSV with a `gene` column (plus optional `source`, `reason`, etc.). Edit them directly — no code change needed, the next loop run picks them up.

Currently:
- `ecm_ligands.tsv` — ECM proteins not credibly blood-borne
- `somascan_questionable.tsv` — SomaScan reagents with documented crossreactivity
- `known_low_abundance_receptors.tsv` — receptors expected to be missing from CellChat

To add `hpa_blood_secreted.tsv` (Human Protein Atlas blood-secreted list):
1. Drop the TSV in `pipeline/knowledge/`
2. Load it in `checks.py` via `_load_gene_set("hpa_blood_secreted.tsv")`
3. Write a check that uses it

## Exit conditions (when the loop stops)

| Condition | Outcome |
|---|---|
| 0 actionable issues fired | CONVERGED |
| Top-10 stable across consecutive iterations (Jaccard ≥ 0.9) | CONVERGED |
| All remaining issues need human input | ESCALATED |
| A `critical` severity issue fired | ESCALATED (immediate) |
| Same fix already in `tried_fixes` | ESCALATED for that issue |
| Same issue check fired in two consecutive iterations | ESCALATED (fix not effective) |
| `--max-iter` reached | BUDGET_EXHAUSTED (treated as escalate) |
| `analyze` or `revise` raised an exception | ESCALATED with traceback |

`PLAN.md` and `RESULTS.md` are regenerated regardless of exit condition.

## What lives where

```
brain-rej/
├── PLAN.md                    auto-generated each loop run (problem + data + method)
├── RESULTS.md                 auto-generated each loop run (findings)
├── README.md                  project intro
├── requirements.txt           pip deps (anthropic SDK is the only non-stdlib dep)
├── paper/                     the 4 source PDFs
├── pipeline/
│   ├── PIPELINE.md            architecture
│   ├── USAGE.md               this file
│   ├── loop.py                orchestrator
│   ├── inspect.py             CLI for browsing state
│   ├── core.py                Plan / Result / Issue dataclasses + I/O
│   ├── analyze.py             parameterized analysis engine
│   ├── checks.py              hardcoded critique checks
│   ├── llm_critic.py          Claude Opus 4.7 expert critic
│   ├── revise.py              applies fix_recipes
│   ├── report.py              regenerates PLAN.md / RESULTS.md
│   └── knowledge/*.tsv        editable curated gene lists
├── scripts/
│   └── download_data.sh       recreates data/ from public sources
├── state/                     pipeline state (committed for reproducibility)
│   ├── iter_log.jsonl         one JSON line per iteration
│   ├── iter_NNN/              plan.json + result.json + issues.json + master.tsv
│   └── escalations.md         structured questions for human review
└── data/                      NOT committed; regenerated by scripts/download_data.sh
```
