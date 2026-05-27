# routes.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Iterable, Dict, Any
import os

import pandas as pd
from flask import Blueprint, current_app, render_template, abort

bp = Blueprint("routes", __name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for p in paths:
        try:
            if p and p.exists():
                return p
        except Exception:
            continue
    return None


def _job_dir(job_id: str) -> Path:
    """
    Resolve job directory safely from JOBS_DIR.
    """
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        abort(400, description="Invalid job_id")
    base = current_app.config.get("JOBS_DIR", "jobs")
    return (Path(base) / job_id).resolve()


def _load_csv_if_exists(path: Path, sep: str | None = None) -> Optional[pd.DataFrame]:
    if not path or not path.exists():
        return None
    try:
        if sep is None:
            return pd.read_csv(path, dtype=str)
        return pd.read_csv(path, sep=sep, dtype=str)
    except Exception:
        return None


def _norm_str(s: pd.Series) -> pd.Series:
    # returns string series with stripped values; safe if s is mixed dtype
    return s.astype(str).fillna("").str.strip()


def _ensure_col(df: pd.DataFrame, col: str, default: Any = "") -> None:
    if col not in df.columns:
        df[col] = default


def _to_num(df: pd.DataFrame, col: str) -> None:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")


def _normalize_percent_series(x: pd.Series) -> pd.Series:
    """
    Normalize a percent-ish series to 0..100.
    - If values look like fractions (<=1), multiply by 100.
    - If values look like already percent (>1), keep.
    - Guard against double-multiplied values (>1000), divide by 100.
    """
    s = pd.to_numeric(x, errors="coerce")
    if s.dropna().empty:
        return s

    mx = s.max(skipna=True)
    if pd.notna(mx) and mx <= 1.0:
        s = s * 100.0

    # guard: if somehow 5280 etc., bring down once
    mx2 = s.max(skipna=True)
    if pd.notna(mx2) and mx2 > 1000.0:
        s = s / 100.0

    # clamp
    s = s.clip(lower=0.0, upper=100.0)
    return s


def _compute_ligand3(df: pd.DataFrame) -> pd.Series:
    """
    Always return the canonical 3-letter ligand code used by MCS_Output filenames.

    Priority:
      1) Warhead if it is exactly 3 chars (authoritative if present)
      2) Ligand_Resolved if it is exactly 3 chars
      3) Ligand5_Resolved first 3 chars (last resort)
      4) Ligand_Resolved first 3 chars (very last resort; better than blank)
    """
    ligand3 = pd.Series([""] * len(df), index=df.index, dtype="object")

    war = _norm_str(df["Warhead"]).str.upper() if "Warhead" in df.columns else pd.Series([""] * len(df), index=df.index)
    lig_res = _norm_str(df["Ligand_Resolved"]).str.upper() if "Ligand_Resolved" in df.columns else pd.Series([""] * len(df), index=df.index)
    lig5 = _norm_str(df["Ligand5_Resolved"]).str.upper() if "Ligand5_Resolved" in df.columns else pd.Series([""] * len(df), index=df.index)

    # 1) Warhead if exactly 3
    use_war = war.str.len() == 3
    ligand3.loc[use_war] = war.loc[use_war]

    # 2) Ligand_Resolved only if exactly 3 and ligand3 not filled
    use_ligres = (ligand3 == "") & (lig_res.str.len() == 3)
    ligand3.loc[use_ligres] = lig_res.loc[use_ligres]

    # 3) Ligand5 fallback slice
    use_lig5 = (ligand3 == "") & (lig5 != "")
    ligand3.loc[use_lig5] = lig5.loc[use_lig5].str.slice(0, 3)

    # 4) final fallback: slice Ligand_Resolved
    use_ligres_slice = (ligand3 == "") & (lig_res != "")
    ligand3.loc[use_ligres_slice] = lig_res.loc[use_ligres_slice].str.slice(0, 3)

    ligand3 = ligand3.replace({"NAN": "", "NONE": "", "?": ""})
    return ligand3


# -----------------------------------------------------------------------------
# Load + normalize Resolved_SASA_Summary
# -----------------------------------------------------------------------------
def load_resolved_sasa_summary(job_id: str) -> pd.DataFrame:
    jd = _job_dir(job_id)

    fp = _first_existing(
        [
            jd / "Resolved_SASA_Summary.tsv",
            jd / "Resolved_SASA_Summary.csv",
            jd / "TARGET_RESULTS" / "Resolved_SASA_Summary.tsv",
            jd / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
        ]
    )
    if not fp:
        return pd.DataFrame()

    sep = "\t" if fp.suffix.lower() == ".tsv" else ","

    tmp = _load_csv_if_exists(fp, sep=sep)
    df = tmp if tmp is not None else pd.DataFrame()
    if df.empty:
        return pd.DataFrame()

    # normalize column names (strip)
    df.columns = [str(c).strip() for c in df.columns]

    # handle legacy/truncated column names
    rename = {
        "Exposed_ato": "Exposed_atoms",
        "Exposed_atom": "Exposed_atoms",
        "SASA_in_com": "SASA_in_complex_A2",
        "SASA_in_complex": "SASA_in_complex_A2",
        "%Exposed ": "%Exposed",
        "%Buried ": "%Buried",
        "pdb": "pdb_id",
    }
    df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)

    # ensure expected columns exist
    _ensure_col(df, "pdb_id", "")
    _ensure_col(df, "Chain", "A")
    _ensure_col(df, "Warhead", "")
    _ensure_col(df, "Ligand_Resolved", "")
    _ensure_col(df, "Ligand5_Resolved", "")
    _ensure_col(df, "SMILES", "")
    _ensure_col(df, "Target", "")
    _ensure_col(df, "Residue_ID", "")
    _ensure_col(df, "Variant", "")

    _ensure_col(df, "Total_atoms", "")
    _ensure_col(df, "Exposed_atoms", "")
    _ensure_col(df, "SASA_in_complex_A2", "")
    _ensure_col(df, "%Exposed", "")
    _ensure_col(df, "%Buried", "")

    # normalize pdb + chain
    df["pdb_id"] = _norm_str(df["pdb_id"]).str.lower()
    df["Chain"] = (
        _norm_str(df["Chain"]).str.upper()
        .replace({"NAN": "A", "NONE": "A", "": "A", "?": "A"})
    )

    # strip common string cols
    for col in ("Warhead", "Ligand_Resolved", "Ligand5_Resolved", "SMILES", "Target"):
        if col in df.columns:
            df[col] = _norm_str(df[col])

    # numeric columns
    for col in ("Residue_ID", "Total_atoms", "Exposed_atoms", "SASA_in_complex_A2", "%Exposed", "%Buried"):
        _to_num(df, col)

    # normalize percent columns to 0..100
    if "%Exposed" in df.columns:
        df["%Exposed"] = _normalize_percent_series(df["%Exposed"])
    if "%Buried" in df.columns:
        df["%Buried"] = _normalize_percent_series(df["%Buried"])

    # Variant back-compat if missing/blank
    if "Variant" not in df.columns or df["Variant"].isna().all():
        if "Residue_ID" in df.columns:
            df["Variant"] = pd.to_numeric(df["Residue_ID"], errors="coerce").fillna(0).astype(int).astype(str)
        else:
            df["Variant"] = "0"
    else:
        # keep as string-y for template usage
        df["Variant"] = _norm_str(df["Variant"]).replace({"nan": "", "NaN": "", "NAN": ""})

    # If Residue_ID is missing/blank, derive from Variant
    if "Residue_ID" in df.columns:
        resid_num = pd.to_numeric(df["Residue_ID"], errors="coerce")
        var_num = pd.to_numeric(df["Variant"], errors="coerce")
        df["Residue_ID"] = resid_num.fillna(var_num)
    else:
        df["Residue_ID"] = pd.to_numeric(df["Variant"], errors="coerce")

    # ---- Canonical 3-letter enforcement ----
    df["Ligand3_Display"] = _compute_ligand3(df)

    # IMPORTANT: make ALL frontend-visible ligand fields 3-letter
    df["Warhead"] = df["Ligand3_Display"]
    df["Ligand_Resolved"] = df["Ligand3_Display"]  # <-- THIS FIXES YOUR TEMPLATE CHOOSING 5-LETTER
    df["ligand"] = df["Ligand3_Display"]           # <-- optional alias for other templates/JS

    # Drop the 5-letter column so it can't leak back out
    if "Ligand5_Resolved" in df.columns:
        df.drop(columns=["Ligand5_Resolved"], inplace=True)

    return df


# -----------------------------------------------------------------------------
# Build the rows you show in the gallery
# -----------------------------------------------------------------------------
def build_pose_rows(job_id: str) -> pd.DataFrame:
    df = load_resolved_sasa_summary(job_id)
    if df is None or df.empty:
        return pd.DataFrame()

    # group best-first (max %Exposed) per pose key
    # Use canonical 3-letter columns now (Warhead)
    group_cols = ["pdb_id", "Warhead", "Chain", "Residue_ID"]
    group_cols = [c for c in group_cols if c in df.columns]

    if "%Exposed" in df.columns:
        exp = pd.to_numeric(df["%Exposed"], errors="coerce").fillna(0.0)
        df["_exp_for_rank"] = exp
    else:
        df["_exp_for_rank"] = 0.0

    if group_cols:
        idx = df.groupby(group_cols, dropna=False)["_exp_for_rank"].idxmax()
        df = df.loc[idx].copy()

    # sort best-first
    df = df.sort_values("_exp_for_rank", ascending=False)

    # cleanup helpers
    if "Ligand3_Display" in df.columns:
        df.drop(columns=["Ligand3_Display"], inplace=True)
    if "_exp_for_rank" in df.columns:
        df.drop(columns=["_exp_for_rank"], inplace=True)

    return df

from pathlib import Path
import pandas as pd

def _read_protein_for_job(job_id: str) -> str:
    # Adjust if your blueprint file is not in APP_ROOT; easiest is importing job_root
    job_path = job_root(job_id)  # if available to import
    fp = job_path / "Protein_Data.csv"
    if not fp.exists():
        return ""

    try:
        df = pd.read_csv(fp, dtype=str).fillna("")
        if df.empty:
            return ""
        return (df.iloc[0].get("protein") or "").strip()
    except Exception:
        return ""

# -----------------------------------------------------------------------------
# Route
# -----------------------------------------------------------------------------
@bp.route("/results/<job_id>")
def view_results(job_id: str):
    df = build_pose_rows(job_id)
    if df is None or df.empty:
        abort(404, description="Resolved_SASA_Summary (csv/tsv) not found or empty for this job.")

    # Pull Target directly (no inference)
    target_col = "Target" if "Target" in df.columns else None
    target_name = ""
    if target_col:
        target_name = str(df.iloc[0][target_col]).strip()

    results = df.to_dict(orient="records")
    return render_template(
        "results_gallery.html",
        job_id=job_id,
        target_name=target_name,
        results=results,
        protac_builder_base=os.environ.get(
            "PROTAC_BUILDER_BASE",
            current_app.config.get("PROTAC_BUILDER_BASE", "https://protacbuilder.com/copy/COPYindex"),
        ),
    )
