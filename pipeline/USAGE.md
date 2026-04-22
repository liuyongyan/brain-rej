# Pipeline Usage

## Three commands you'll actually use

```bash
# Run the loop (resumes from latest state by default; uses seed plan if empty)
python -m pipeline.loop

# Browse what happened
python -m pipeline.inspect              # summary of all iterations
python -m pipeline.inspect 1            # detail of iteration 1
python -m pipeline.inspect diff 0 1     # what changed in the plan between iter 0 and 1
```

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
python -m pipeline.inspect              # see the iteration summary
cat state/escalations.md                # see what needs your input
python -m pipeline.inspect 2            # detail of iteration 2
python -m pipeline.inspect diff 0 1     # see how the plan evolved
```

### Step 3 — Address an escalation
The loop saved its current plan at `state/iter_NNN/plan.json` (highest N). Edit it directly to apply your decision. Examples:

**Escalation: "authorize Pálovics download?"** → Add a `palovics` data source to the plan:
```json
"data_sources": {
  ...,
  "palovics": {"path": "palovics_2022/brain_subset.h5ad"}
}
```

**Escalation: "single-receptor framing brittle, report tier?"** → no plan change needed; this is a presentation choice. Re-running won't help — go look at the top-10 in `state/iter_NNN/master.tsv`.

**Escalation: "broaden universe?"** → Edit the `universe` block:
```json
"universe": {
  "method": "cellchat_all_receptors",   # not yet implemented; would need new universe builder
  "params": {...}
}
```

### Step 4 — Resume
```bash
python -m pipeline.loop --resume
```
Loads the latest `iter_NNN/plan.json`, continues from `iter_{N+1}`.

Or to start from a hand-edited plan elsewhere:
```bash
python -m pipeline.loop --plan path/to/my_plan.json
```

## Adding a new failure mode

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

If the new check is auto-fixable, also add a handler to `revise.py`:
```python
elif action == "merge_share_ligand_hits":
    # ... your fix here
```

## Adding a new domain knowledge list

Knowledge files in `pipeline/knowledge/` are TSV with a `gene` column (plus optional `source`, `reason`, etc.). Edit them directly — no code change needed, the next loop run picks them up.

Currently:
- `ecm_ligands.tsv` — ECM proteins not credibly blood-borne
- `somascan_questionable.tsv` — SomaScan reagents with documented crossreactivity
- `known_low_abundance_receptors.tsv` — receptors expected to be missing from CellChat

To add `"hpa_blood_secreted.tsv"` (Human Protein Atlas blood-secreted list):
1. Drop the TSV in `pipeline/knowledge/`
2. Load it in `checks.py` via `_load_gene_set("hpa_blood_secreted.tsv")`
3. Write a check that uses it

## Exit conditions (when the loop stops)

| Condition | Outcome |
|---|---|
| 0 issues fired | CONVERGED |
| Top-10 Jaccard ≥ 0.9 across consecutive iterations | CONVERGED |
| All remaining issues need human input | ESCALATED |
| A `critical` severity issue fired | ESCALATED (immediate) |
| Same fix already in `tried_fixes` | ESCALATED for that issue |
| `--max-iter` reached | BUDGET_EXHAUSTED |
| `analyze` or `revise` raised an exception | ESCALATED with traceback |

## What lives where

```
pipeline/
  loop.py              orchestrator (entry point)
  inspect.py           CLI for browsing state
  core.py              Plan / Result / Issue dataclasses + I/O
  analyze.py           parameterized analysis
  checks.py            critique checks (the IP)
  revise.py            applies fix_recipes
  knowledge/*.tsv      editable curated gene lists
  PIPELINE.md          architecture
  USAGE.md             this file
state/
  iter_log.jsonl       one JSON line per iteration
  iter_NNN/            plan.json, result.json, issues.json, master.tsv
  escalations.md       structured questions appended at each escalation
```
