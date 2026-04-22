"""Critique checks. Each check: (result, plan) -> Issue | None.

To add a new failure mode:
  1. Write a check function returning Issue | None
  2. Append it to CHECKS
  3. If auto-fixable, ensure revise.py handles the fix_recipe action

Knowledge files (gene lists curated from literature) live in pipeline/knowledge/
and can be edited by the user without changing code.
"""

from __future__ import annotations
import pandas as pd
from pathlib import Path
from .core import Result, Plan, Issue

ROOT = Path(__file__).resolve().parents[1]
KNOW = Path(__file__).resolve().parent / "knowledge"


def _load_gene_set(filename: str) -> set[str]:
    return set(pd.read_csv(KNOW / filename, sep="\t")["gene"].dropna().astype(str))

ECM_LIGANDS = _load_gene_set("ecm_ligands.tsv")
SOMASCAN_QUESTIONABLE_LIGANDS = _load_gene_set("somascan_questionable.tsv")
KNOWN_LOW_ABUNDANCE = _load_gene_set("known_low_abundance_receptors.tsv")


# --- check functions ---

def check_ecm_ligand_inflation(result: Result, plan: Plan) -> Issue | None:
    """≥3 of top-20 hits driven by ECM ligands → exclude ECM from ligand pool."""
    top = result.top_n[:20]
    bad = [r for r in top if r.get("best_ligand") in ECM_LIGANDS]
    if len(bad) >= 3:
        # find which ECM ligands are actually driving hits
        ligs_in_use = sorted({r["best_ligand"] for r in bad})
        return Issue(
            check_name="ecm_ligand_inflation",
            severity="major",
            evidence=f"{len(bad)} of top 20 hits driven by ECM ligands "
                     f"({ligs_in_use}); these are not blood-borne signaling factors.",
            fix_recipe={"action": "exclude_ligands", "args": {"ligands": sorted(ECM_LIGANDS)}},
        )
    return None


def check_somascan_crossreactive_top_ligand(result: Result, plan: Plan) -> Issue | None:
    """Top hit's best ligand is a known crossreactive SomaScan reagent."""
    top1 = result.top_n[0] if result.top_n else None
    if top1 and top1.get("best_ligand") in SOMASCAN_QUESTIONABLE_LIGANDS:
        return Issue(
            check_name="somascan_crossreactive_top_ligand",
            severity="major",
            evidence=f"Top hit ({top1['receptor']}) is driven by ligand "
                     f"{top1['best_ligand']}, which has documented SomaScan "
                     f"reagent crossreactivity (Egerman 2015, Schafer 2016). "
                     f"The plasma age effect for this ligand is unreliable.",
            fix_recipe={"action": "exclude_ligands",
                        "args": {"ligands": sorted(SOMASCAN_QUESTIONABLE_LIGANDS)}},
        )
    return None


def check_no_permutation_null(result: Result, plan: Plan) -> Issue | None:
    """Combined Z reported without a null distribution."""
    if plan.combination["method"] == "stoufferZ":
        return Issue(
            check_name="no_permutation_null",
            severity="major",
            evidence="Combined Z scores reported without a permutation null. "
                     "Cannot assert significance of top-1 hit relative to chance.",
            fix_recipe={"action": "add_permutation_null", "args": {"n_perm": 1000}},
        )
    return None


def check_top_hit_score_gap_too_small(result: Result, plan: Plan) -> Issue | None:
    """If the gap between top-1 and top-2 score is small, the 'single best' framing is brittle.
    Only fires if a permutation null exists and the gap is small relative to it."""
    if "empirical_p" not in pd.read_csv(result.raw_table_path, sep="\t").columns:
        return None  # no null available yet — let the no_permutation_null check fire first
    df = pd.read_csv(result.raw_table_path, sep="\t")
    if len(df) < 2: return None
    gap = df["final_score"].iloc[0] - df["final_score"].iloc[1]
    top_p = df["empirical_p"].iloc[0]
    if gap < 0.5 and top_p > 0.01:
        return Issue(
            check_name="top_hit_score_gap_too_small",
            severity="major",
            evidence=f"Gap between top-1 ({df['receptor'].iloc[0]}) and top-2 "
                     f"({df['receptor'].iloc[1]}) is only {gap:.2f}; top-1 empirical p = {top_p:.3f}. "
                     f"The 'single best receptor' claim is not robust.",
            fix_recipe=None,
            escalation_question=f"Should the answer be reported as a tier (top-N) "
                                f"rather than a single receptor? Top candidates with similar "
                                f"scores: {df['receptor'].head(5).tolist()}",
        )
    return None


def check_no_independent_replication(result: Result, plan: Plan) -> Issue | None:
    """Top 5 hits have not been validated in an independent cohort (Pálovics)."""
    if "palovics" in plan.data_sources:
        return None  # already integrated
    return Issue(
        check_name="no_independent_replication",
        severity="major",
        evidence=f"Top 5 hits ({[r['receptor'] for r in result.top_n[:5]]}) "
                 f"are based on a single mouse parabiosis cohort (Ximerakis). "
                 f"No independent replication.",
        fix_recipe=None,  # auto-download possible but flagged for human approval
        escalation_question="Pálovics 2022 brain subset (Figshare project 119145) "
                            "is the natural replication cohort but requires ~hundreds MB download. "
                            "Authorize download for replication of top 5?",
    )


def check_directional_consistency_low_for_top(result: Result, plan: Plan) -> Issue | None:
    """Top hit has low directional consistency rate."""
    if not result.top_n: return None
    top1 = result.top_n[0]
    dt = top1.get("dir_total", 0)
    dc = top1.get("dir_consistency", 0)
    if dt >= 2 and dc / dt < 0.5:
        return Issue(
            check_name="directional_consistency_low_for_top",
            severity="major",
            evidence=f"Top hit {top1['receptor']} has directional consistency "
                     f"{dc}/{dt} = {dc/dt:.0%}; less than half of its annotated "
                     f"agonist/antagonist ligands are sign-coherent with brain receptor change.",
            fix_recipe=None,
            escalation_question=f"Receptor {top1['receptor']}'s ligand→brain coherence is mixed. "
                                f"Possible explanations: (a) compensatory upregulation, "
                                f"(b) decoy/desensitization, (c) the OmniPath stim/inh annotation "
                                f"is wrong, (d) multiple ligands acting in opposite directions. "
                                f"Which interpretation should be assumed?",
        )
    return None


def check_positive_controls_missing(result: Result, plan: Plan) -> Issue | None:
    """Expected positive controls absent from candidate universe — but only flag
    those NOT in the known low-abundance set (which are expected to be missing)."""
    df = pd.read_csv(result.raw_table_path, sep="\t")
    universe = set(df["receptor"])
    expected = {"LRP6", "CD44", "ITGB1", "CXCR4", "CXCR3", "C3AR1", "C5AR1", "IL6ST", "TGFBR2"}
    missing = expected - universe
    # filter out the known low-abundance ones — their absence is expected
    really_missing = missing - KNOWN_LOW_ABUNDANCE
    if len(really_missing) >= 2:
        return Issue(
            check_name="positive_controls_missing",
            severity="major",
            evidence=f"Expected positive control receptors absent from universe: "
                     f"{sorted(really_missing)}. Universe construction may be too restrictive.",
            fix_recipe=None,
            escalation_question="Should the receptor universe be broadened beyond the CellChat "
                                "condition-dependent set? E.g., include all CellChat receptors "
                                "(not just condition-dependent) or all OmniPath receptors with "
                                "brain expression > threshold?",
        )
    return None


# Registry — order matters only for human readability of issue list
CHECKS = [
    check_ecm_ligand_inflation,
    check_somascan_crossreactive_top_ligand,
    check_no_permutation_null,
    check_top_hit_score_gap_too_small,
    check_no_independent_replication,
    check_directional_consistency_low_for_top,
    check_positive_controls_missing,
]


def critique(result: Result, plan: Plan) -> list[Issue]:
    return [i for c in CHECKS if (i := c(result, plan)) is not None]
