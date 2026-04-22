"""Apply fix_recipes to a Plan, returning a new Plan."""

from __future__ import annotations
from copy import deepcopy
from .core import Plan, Issue


def revise(plan: Plan, issues: list[Issue]) -> Plan:
    """Apply all auto-fixable issues' recipes and return a new Plan.
    Records the applied fixes in plan.tried_fixes."""
    p = deepcopy(plan)
    for iss in issues:
        if iss.fix_recipe is None:
            continue
        action = iss.fix_recipe["action"]
        args = iss.fix_recipe.get("args", {})

        if action == "exclude_ligands":
            # add to plasma stream's exclude_ligands
            for s in p.evidence_streams:
                if s["name"] == "plasma":
                    cur = set(s.setdefault("params", {}).setdefault("exclude_ligands", []))
                    cur |= set(args["ligands"])
                    s["params"]["exclude_ligands"] = sorted(cur)

        elif action == "add_permutation_null":
            p.combination = {"method": "stoufferZ_with_permutation_null",
                             "params": {"n_perm": int(args.get("n_perm", 1000))}}

        elif action == "exclude_receptors":
            p.filters_post.append({"name": "exclude_receptors",
                                   "args": {"receptors": list(args["receptors"])}})

        elif action == "broaden_universe":
            p.universe = args["new_universe"]

        else:
            raise ValueError(f"unknown fix action: {action}")

        fh = iss.fix_hash()
        if fh and fh not in p.tried_fixes:
            p.tried_fixes.append(fh)

    p.version += 1
    return p
