"""Plan / Result / Issue dataclasses + state I/O."""

from __future__ import annotations
import json
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# ---------- dataclasses ----------

@dataclass
class Plan:
    """Complete specification of an analysis. Hashable via JSON."""
    version: int
    data_sources: dict
    universe: dict
    evidence_streams: list
    combination: dict
    filters_post: list = field(default_factory=list)
    tried_fixes: list = field(default_factory=list)   # list of fix-recipe hashes

    def hash(self) -> str:
        # exclude tried_fixes from hash so the plan content (not history) defines identity
        d = {k: v for k, v in asdict(self).items() if k != "tried_fixes"}
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Plan":
        return cls(**json.loads(s))


@dataclass
class Result:
    iteration: int
    plan_hash: str
    n_candidates: int
    top_n: list                                   # list of dicts
    metrics: dict
    raw_table_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


@dataclass
class Issue:
    check_name: str
    severity: str                                 # critical | major | minor
    evidence: str
    fix_recipe: Optional[dict] = None             # {"action": "...", "args": {...}}
    escalation_question: Optional[str] = None

    def fix_hash(self) -> Optional[str]:
        if self.fix_recipe is None: return None
        return hashlib.sha1(
            json.dumps(self.fix_recipe, sort_keys=True).encode()
        ).hexdigest()[:10]


# ---------- state I/O ----------

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)

def iter_dir(n: int) -> Path:
    d = STATE / f"iter_{n:03d}"
    d.mkdir(exist_ok=True)
    return d

def save_iteration(n: int, plan: Plan, result: Result, issues: list[Issue]) -> None:
    d = iter_dir(n)
    (d / "plan.json").write_text(plan.to_json())
    (d / "result.json").write_text(result.to_json())
    (d / "issues.json").write_text(json.dumps([asdict(i) for i in issues], indent=2))
    log_path = STATE / "iter_log.jsonl"
    with log_path.open("a") as f:
        f.write(json.dumps({
            "iteration": n,
            "plan_hash": plan.hash(),
            "n_candidates": result.n_candidates,
            "top_5": [r["receptor"] for r in result.top_n[:5]],
            "max_score": result.metrics.get("max_score"),
            "n_issues": len(issues),
            "issues_by_check": [i.check_name for i in issues],
            "all_auto_fixable": all(i.fix_recipe is not None for i in issues),
        }) + "\n")

def append_escalation(n: int, issues: list[Issue], reason: str) -> None:
    p = STATE / "escalations.md"
    with p.open("a") as f:
        f.write(f"\n## Iteration {n} — {reason}\n\n")
        for i in issues:
            f.write(f"### {i.check_name} ({i.severity})\n")
            f.write(f"- **Evidence**: {i.evidence}\n")
            if i.escalation_question:
                f.write(f"- **Question for human**: {i.escalation_question}\n")
            if i.fix_recipe:
                f.write(f"- **Auto-fix attempted but did not resolve**: `{i.fix_recipe}`\n")
            f.write("\n")
