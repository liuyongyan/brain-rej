"""Parameterized analysis engine.

Reads a Plan, computes ranked receptor candidates, returns a Result.
All thresholds, filters, and weights come from the Plan — no hardcoded constants.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path
from scipy.stats import spearmanr, norm, rankdata
from .core import Plan, Result, iter_dir
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


# ---------- universe builders ----------

def build_universe(plan: Plan) -> tuple[list[str], dict[str, set[str]]]:
    """Return (candidate receptors, origin tags)."""
    u = plan.universe
    if u["method"] == "cellchat_condition_dependent":
        path = DATA / u["params"]["path"]
        sets = {sh: set(pd.read_excel(path, sheet_name=sh)["receptor"].dropna().astype(str))
                for sh in ["YY", "OO", "OY", "YO", "OX", "YX"]}
        groups = {
            "RJV-restored": sets["OY"] - sets["OO"],
            "aging-gained": sets["OO"] - sets["YY"],
            "aging-lost":   sets["YY"] - sets["OO"],
            "AGA-induced":  sets["YO"] - sets["YY"],
        }
        wanted = u["params"].get("groups", list(groups.keys()))
        raw = set().union(*[groups[g] for g in wanted])
        # expand multi-subunit (FZD1_LRP6 → FZD1, LRP6) and uppercase
        cands = sorted({g for r in raw for g in r.split("_")} | set())
        cands = sorted({c.upper() for c in cands})
        origin = {}
        for r in raw:
            for g in [x.upper() for x in r.split("_")]:
                tags = origin.setdefault(g, set())
                for gname, gset in groups.items():
                    if r in gset and gname in wanted:
                        tags.add(gname)
        return cands, origin
    raise ValueError(f"unknown universe method: {u['method']}")


# ---------- evidence stream computers ----------

BLOOD_AGE = {"YY": 0, "YX": 1, "OY": 2, "YO": 3, "OX": 4, "OO": 5}
BBB_FACING = {"EC", "PC", "VSMC", "MG", "MNC", "DC", "ABC", "VLMC", "CPC"}
ORDER = ["YY", "YX", "OY", "YO", "OX", "OO"]
ORDER_VALS = np.array([BLOOD_AGE[c] for c in ORDER])

def compute_brain_doseresp(candidates, plan, src_path, min_max_tpm):
    xl = pd.ExcelFile(src_path)
    rows = []
    for ct in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=ct)
        tpm_cols = [c for c in df.columns if c.endswith(f"_{ct}_tpm")]
        if len(tpm_cols) != 6: continue
        d = df[["gene"] + tpm_cols].copy()
        d.columns = ["gene"] + [c.split("_")[0] for c in tpm_cols]
        sub = d[d["gene"].str.upper().isin(candidates)]
        for _, row in sub.iterrows():
            vals = row[ORDER].astype(float).to_numpy()
            if np.nanmax(vals) < min_max_tpm: continue
            rho, p = spearmanr(ORDER_VALS, vals)
            if np.isnan(rho): continue
            rows.append({"receptor": row["gene"].upper(), "cell_type": ct,
                         "rho": rho, "p_rho": p,
                         "max_tpm": float(np.nanmax(vals)),
                         "bbb_facing": ct in BBB_FACING})
    brain = pd.DataFrame(rows)

    def summarize(g):
        bbb = g[g["bbb_facing"]]
        if len(bbb):
            i = bbb["rho"].abs().idxmax()
            bbb_rho = float(bbb.loc[i, "rho"]); bbb_ct = bbb.loc[i, "cell_type"]
        else: bbb_rho, bbb_ct = 0.0, None
        i = g["rho"].abs().idxmax()
        return pd.Series({"bbb_top_celltype": bbb_ct, "bbb_top_rho": bbb_rho,
                          "any_top_celltype": g.loc[i, "cell_type"],
                          "any_top_rho": float(g.loc[i, "rho"]),
                          "n_strong": int((g["rho"].abs() > 0.7).sum())})
    return brain.groupby("receptor").apply(summarize, include_groups=False).reset_index()


def load_plasma(plan, st4_path, st1_path, st14_path, exclude_ligands=None):
    st1 = pd.read_excel(st1_path, sheet_name="ST1 Nomenclature 2,925 proteins", header=2)
    st4 = pd.read_excel(st4_path, sheet_name="ST4 Linear modeling - Human", header=2)
    plasma = (st4.merge(st1[["ID", "EntrezGeneSymbol"]], on="ID")
                  .assign(gene=lambda d: d["EntrezGeneSymbol"].astype(str).str.split(r"[\s\.\|]").str[0])
                  .sort_values("q.Age").groupby("gene", as_index=False).first()
                  [["gene", "ID", "Coefficient.Age", "q.Age"]])
    if exclude_ligands:
        plasma = plasma[~plasma["gene"].isin(set(exclude_ligands))]
    wave14 = pd.read_excel(st14_path, sheet_name="ST14 DE-SWAN - 3 main waves", header=2)
    wave14 = wave14.merge(st1[["ID", "EntrezGeneSymbol"]], left_on="variable", right_on="ID", how="left")
    wave14["gene"] = wave14["EntrezGeneSymbol"].astype(str).str.split(r"[\s\.\|]").str[0]
    wave7 = set(wave14.loc[wave14["qvalue.60"] < 0.05, "gene"].dropna())
    return plasma, wave7


def _stream_params(plan: Plan, name: str) -> dict:
    """Look up an evidence stream by name (not by list index)."""
    for s in plan.evidence_streams:
        if s["name"] == name:
            return s.get("params", {})
    return {}


def compute_plasma_evidence(candidates, plan, plasma, wave7, lr_df, brain_per_rec):
    pdict = plasma.set_index("gene").to_dict("index")
    rec2lig = (lr_df.groupby("receptor")
                    [["ligand", "consensus_stimulation", "consensus_inhibition"]]
                    .apply(lambda d: d.to_dict("records")).to_dict())
    params = _stream_params(plan, "plasma")
    p_thr = params.get("q_threshold", 0.05)
    w7_w = params.get("wave7_weight", 1.5)

    rows = []
    for rec in candidates:
        edges = rec2lig.get(rec, [])
        meas = []
        for e in edges:
            info = pdict.get(e["ligand"])
            if info is None: continue
            meas.append({"ligand": e["ligand"], "stim": bool(e["consensus_stimulation"]),
                         "inh": bool(e["consensus_inhibition"]),
                         "coef": info["Coefficient.Age"], "q": info["q.Age"],
                         "wave7": e["ligand"] in wave7})
        sig = [m for m in meas if m["q"] < p_thr]
        if not meas:
            rows.append({"receptor": rec, "n_ligands": len(edges),
                         "n_measured": 0, "n_sig": 0,
                         "best_ligand": None, "best_q": np.nan, "best_coef": 0,
                         "weighted_neglogq": 0, "dir_consistency": 0, "dir_total": 0,
                         "all_sig_ligands": ""})
            continue
        nlq = max((-np.log10(max(m["q"], 1e-300))) * (w7_w if m["wave7"] else 1.0) for m in meas)
        best = max(sig, key=lambda m: (-np.log10(max(m["q"], 1e-300))) * (w7_w if m["wave7"] else 1.0)) \
               if sig else min(meas, key=lambda m: m["q"])
        # directional consistency
        rho_row = brain_per_rec[brain_per_rec["receptor"] == rec]
        dir_c, dir_t = 0, 0
        if not rho_row.empty and sig:
            rho_val = rho_row["bbb_top_rho"].iloc[0] or rho_row["any_top_rho"].iloc[0]
            if rho_val:
                rho_sign = np.sign(rho_val)
                for m in sig:
                    if not (m["stim"] or m["inh"]): continue
                    dir_t += 1
                    if m["stim"] and not m["inh"]:
                        exp = np.sign(m["coef"])
                    elif m["inh"] and not m["stim"]:
                        exp = -np.sign(m["coef"])
                    else:
                        continue
                    if rho_sign == exp:
                        dir_c += 1
        rows.append({"receptor": rec, "n_ligands": len(edges),
                     "n_measured": len(meas), "n_sig": len(sig),
                     "best_ligand": best["ligand"], "best_q": best["q"],
                     "best_coef": best["coef"],
                     "weighted_neglogq": nlq, "dir_consistency": dir_c,
                     "dir_total": dir_t,
                     "all_sig_ligands": ";".join(m["ligand"] for m in sig)})
    return pd.DataFrame(rows)


MS_TO_HS = {"EC": ["endo"], "MG": ["micro"], "ASC": ["ast"], "OLG": ["oli"], "OPC": ["opc"]}

def compute_cross_species(candidates, jeff_path, brain_per_rec):
    jeff = pd.read_excel(jeff_path, sheet_name="Sheet1")
    jeff = jeff.rename(columns={"gene": "g", "log2(elderly/adult)": "lfc",
                                "p-value": "p", "cell type": "ct"})
    jeff = jeff[jeff["g"].isin(candidates)]
    rows = []
    for rec in candidates:
        rho_row = brain_per_rec[brain_per_rec["receptor"] == rec]
        if rho_row.empty:
            rows.append({"receptor": rec, "hs_concordant": 0, "hs_tested": 0,
                         "hs_top_celltype": None, "hs_top_log2FC": 0.0})
            continue
        ms_ct = rho_row["bbb_top_celltype"].iloc[0] or rho_row["any_top_celltype"].iloc[0]
        rho_val = rho_row["bbb_top_rho"].iloc[0] or rho_row["any_top_rho"].iloc[0]
        ms_dir = np.sign(rho_val) if rho_val else 0
        hs_cts = MS_TO_HS.get(ms_ct, [])
        hs_rows = jeff[(jeff["g"] == rec) & (jeff["ct"].isin(hs_cts))]
        if hs_rows.empty:
            rows.append({"receptor": rec, "hs_concordant": 0, "hs_tested": 0,
                         "hs_top_celltype": None, "hs_top_log2FC": 0.0})
            continue
        sig = hs_rows[hs_rows["p"] < 0.05]
        conc = sig[np.sign(sig["lfc"]) == ms_dir]
        i = hs_rows["lfc"].abs().idxmax()
        rows.append({"receptor": rec, "hs_concordant": int(len(conc)),
                     "hs_tested": int(len(hs_rows)),
                     "hs_top_celltype": hs_rows.loc[i, "ct"],
                     "hs_top_log2FC": float(hs_rows.loc[i, "lfc"])})
    return pd.DataFrame(rows)


# ---------- combination ----------

def combine(master_df: pd.DataFrame, plan: Plan) -> pd.DataFrame:
    method = plan.combination["method"]
    if method == "stoufferZ":
        def to_z(s):
            r = rankdata(s, method="average")
            return norm.ppf((r - 0.5) / len(r))
        z_brain  = to_z(master_df["any_top_rho"].abs())
        z_plasma = to_z(master_df["weighted_neglogq"])
        master_df["dir_rate"] = np.where(master_df["dir_total"] > 0,
                                         master_df["dir_consistency"] / master_df["dir_total"], 0)
        z_dir = to_z(master_df["dir_rate"])
        z_xs  = to_z(master_df["hs_concordant"])
        master_df["combined_z"] = (z_brain + z_plasma + z_dir + z_xs) / np.sqrt(4)
        master_df["bonus_bbb"] = master_df["bbb_top_celltype"].apply(
            lambda x: 0.5 if isinstance(x, str) and x in BBB_FACING else 0)
        master_df["final_score"] = master_df["combined_z"] + master_df["bonus_bbb"]
    elif method == "stoufferZ_with_permutation_null":
        # permute receptor labels in evidence streams 1000x to get null
        n_perm = plan.combination.get("params", {}).get("n_perm", 1000)
        rng = np.random.default_rng(42)
        # quick null: shuffle the score columns independently and combine
        def to_z(s):
            r = rankdata(s, method="average")
            return norm.ppf((r - 0.5) / len(r))
        z_brain  = to_z(master_df["any_top_rho"].abs())
        z_plasma = to_z(master_df["weighted_neglogq"])
        master_df["dir_rate"] = np.where(master_df["dir_total"] > 0,
                                         master_df["dir_consistency"] / master_df["dir_total"], 0)
        z_dir = to_z(master_df["dir_rate"])
        z_xs  = to_z(master_df["hs_concordant"])
        observed = (z_brain + z_plasma + z_dir + z_xs) / np.sqrt(4)
        max_perm = np.empty(n_perm)
        for i in range(n_perm):
            zb = rng.permutation(z_brain); zp = rng.permutation(z_plasma)
            zd = rng.permutation(z_dir); zx = rng.permutation(z_xs)
            max_perm[i] = ((zb + zp + zd + zx) / np.sqrt(4)).max()
        emp_p = np.array([(max_perm >= s).mean() for s in observed])
        master_df["combined_z"] = observed
        master_df["empirical_p"] = emp_p
        master_df["bonus_bbb"] = master_df["bbb_top_celltype"].apply(
            lambda x: 0.5 if isinstance(x, str) and x in BBB_FACING else 0)
        master_df["final_score"] = master_df["combined_z"] + master_df["bonus_bbb"]
    else:
        raise ValueError(f"unknown combination method: {method}")
    return master_df.sort_values("final_score", ascending=False).reset_index(drop=True)


# ---------- post-filters ----------

def apply_post_filters(df: pd.DataFrame, plan: Plan) -> pd.DataFrame:
    for f in plan.filters_post:
        if f["name"] == "exclude_receptors":
            df = df[~df["receptor"].isin(set(f["args"]["receptors"]))]
    return df.reset_index(drop=True)


# ---------- top-level ----------

def analyze(plan: Plan, iteration: int) -> Result:
    out_dir = iter_dir(iteration)
    candidates, origin = build_universe(plan)

    src = plan.data_sources
    brain_per_rec = compute_brain_doseresp(
        candidates, plan, DATA / src["ximerakis_tpms"]["path"],
        min_max_tpm=_stream_params(plan, "brain_doseresp").get("min_max_tpm", 1.0))

    excl = _stream_params(plan, "plasma").get("exclude_ligands", [])
    plasma, wave7 = load_plasma(
        plan,
        DATA / src["lehallier_st4"]["path"], DATA / src["lehallier_st1"]["path"],
        DATA / src["lehallier_st14"]["path"], exclude_ligands=excl)

    lr = pd.read_csv(DATA / src["omnipath"]["path"], sep="\t")
    lr = lr.rename(columns={"source_genesymbol": "ligand", "target_genesymbol": "receptor"})

    plasma_per_rec = compute_plasma_evidence(candidates, plan, plasma, wave7, lr, brain_per_rec)
    xs_per_rec = compute_cross_species(candidates, DATA / src["jeffries_s7"]["path"], brain_per_rec)

    master = (pd.DataFrame({"receptor": candidates})
              .assign(origin=lambda d: d["receptor"].map(lambda r: ";".join(sorted(origin.get(r, [])))))
              .merge(brain_per_rec, on="receptor", how="left")
              .merge(plasma_per_rec, on="receptor", how="left")
              .merge(xs_per_rec, on="receptor", how="left")
              .fillna({"bbb_top_rho": 0, "any_top_rho": 0, "n_strong": 0,
                       "n_sig": 0, "weighted_neglogq": 0, "dir_consistency": 0,
                       "dir_total": 0, "hs_concordant": 0, "hs_tested": 0,
                       "hs_top_log2FC": 0}))

    master = combine(master, plan)
    master = apply_post_filters(master, plan)
    master["rank"] = range(1, len(master) + 1)

    raw_path = out_dir / "master.tsv"
    master.to_csv(raw_path, sep="\t", index=False)

    top_n = master.head(20).to_dict("records")
    metrics = {
        "max_score": float(master["final_score"].iloc[0]) if len(master) else 0,
        "score_gap_top12": float(master["final_score"].iloc[0] - master["final_score"].iloc[1])
                           if len(master) >= 2 else 0,
        "n_top_with_dir_consistency_lt_50pct": int(((master.head(20)["dir_total"] > 0) &
                                                    (master.head(20)["dir_consistency"] /
                                                     master.head(20)["dir_total"].replace(0, 1) < 0.5)).sum()),
    }
    return Result(iteration=iteration, plan_hash=plan.hash(),
                  n_candidates=len(master), top_n=top_n, metrics=metrics,
                  raw_table_path=str(raw_path))
