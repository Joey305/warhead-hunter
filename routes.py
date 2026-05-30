# routes.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
from flask import Blueprint, abort, current_app, render_template
import job_state as disk_jobs

try:
    from api.randy_archive_client import (
        archive_enabled,
        get_job_index as randy_get_job_index,
        get_table_dataframe as randy_get_table_dataframe,
        job_exists as randy_job_exists,
        last_table_diagnostic as randy_last_table_diagnostic,
    )
except Exception:  # keep local/dev boot safe even before api/randy_archive_client.py is dropped in
    def archive_enabled() -> bool:
        return False

    def randy_get_job_index(job_id: str):
        return None

    def randy_get_table_dataframe(job_id: str, names):
        return None

    def randy_job_exists(job_id: str) -> bool:
        return False

    def randy_last_table_diagnostic() -> dict:
        return {}


bp = Blueprint("routes", __name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _safe_job_id(job_id: str) -> bool:
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        return False
    return True


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for p in paths:
        try:
            if p and p.exists():
                return p
        except Exception:
            continue
    return None


def _job_dir(job_id: str) -> Path:
    """Resolve local Heroku/dev job directory safely from JOBS_DIR."""
    if not _safe_job_id(job_id):
        abort(400, description="Invalid job_id")
    base = Path(current_app.config.get("JOBS_DIR", "jobs")).resolve()
    job_dir = (base / job_id).resolve()
    try:
        job_dir.relative_to(base)
    except Exception:
        abort(400, description="Invalid job path")
    return job_dir


def _load_csv_if_exists(path: Path, sep: str | None = None) -> Optional[pd.DataFrame]:
    if not path or not path.exists():
        return None
    try:
        if sep is None:
            return pd.read_csv(path, dtype=str).fillna("")
        return pd.read_csv(path, sep=sep, dtype=str).fillna("")
    except Exception:
        return None


def _norm_str(s: pd.Series) -> pd.Series:
    return s.astype(str).fillna("").str.strip()


def _ensure_col(df: pd.DataFrame, col: str, default: Any = "") -> None:
    if col not in df.columns:
        df[col] = default


def _to_num(df: pd.DataFrame, col: str) -> None:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")


def _normalize_percent_series(x: pd.Series) -> pd.Series:
    s = pd.to_numeric(x, errors="coerce")
    if s.dropna().empty:
        return s
    mx = s.max(skipna=True)
    if pd.notna(mx) and mx <= 1.0:
        s = s * 100.0
    mx2 = s.max(skipna=True)
    if pd.notna(mx2) and mx2 > 1000.0:
        s = s / 100.0
    return s.clip(lower=0.0, upper=100.0)


def _compute_ligand3(df: pd.DataFrame) -> pd.Series:
    ligand3 = pd.Series([""] * len(df), index=df.index, dtype="object")

    war = _norm_str(df["Warhead"]).str.upper() if "Warhead" in df.columns else pd.Series([""] * len(df), index=df.index)
    lig_res = _norm_str(df["Ligand_Resolved"]).str.upper() if "Ligand_Resolved" in df.columns else pd.Series([""] * len(df), index=df.index)
    lig5 = _norm_str(df["Ligand5_Resolved"]).str.upper() if "Ligand5_Resolved" in df.columns else pd.Series([""] * len(df), index=df.index)

    use_war = war.str.len() == 3
    ligand3.loc[use_war] = war.loc[use_war]

    use_ligres = (ligand3 == "") & (lig_res.str.len() == 3)
    ligand3.loc[use_ligres] = lig_res.loc[use_ligres]

    use_lig5 = (ligand3 == "") & (lig5 != "")
    ligand3.loc[use_lig5] = lig5.loc[use_lig5].str.slice(0, 3)

    use_ligres_slice = (ligand3 == "") & (lig_res != "")
    ligand3.loc[use_ligres_slice] = lig_res.loc[use_ligres_slice].str.slice(0, 3)

    return ligand3.replace({"NAN": "", "NONE": "", "?": ""})


def _normalize_gallery_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy().fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    rename = {
        "pdb": "pdb_id",
        "Exposed_ato": "Exposed_atoms",
        "Exposed_atom": "Exposed_atoms",
        "SASA_in_com": "SASA_in_complex_A2",
        "SASA_in_complex": "SASA_in_complex_A2",
        "%Exposed ": "%Exposed",
        "%Buried ": "%Buried",
    }
    df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)

    for col, default in {
        "pdb_id": "",
        "Chain": "A",
        "Warhead": "",
        "Ligand_Resolved": "",
        "Ligand5_Resolved": "",
        "ligand": "",
        "SMILES": "",
        "Target": "",
        "Residue_ID": "",
        "Variant": "",
        "Total_atoms": "0",
        "Exposed_atoms": "0",
        "SASA_in_complex_A2": "0",
        "%Exposed": "0",
        "%Buried": "",
    }.items():
        _ensure_col(df, col, default)

    df["pdb_id"] = _norm_str(df["pdb_id"]).str.lower()
    df["Chain"] = _norm_str(df["Chain"]).str.upper().replace({"NAN": "A", "NONE": "A", "": "A", "?": "A"})

    for col in ("Warhead", "Ligand_Resolved", "Ligand5_Resolved", "SMILES", "Target", "Variant"):
        if col in df.columns:
            df[col] = _norm_str(df[col])

    for col in ("Residue_ID", "Total_atoms", "Exposed_atoms", "SASA_in_complex_A2", "%Exposed", "%Buried"):
        _to_num(df, col)

    df["%Exposed"] = _normalize_percent_series(df["%Exposed"])
    buried = pd.to_numeric(df["%Buried"], errors="coerce")
    if buried.isna().all():
        df["%Buried"] = 100.0 - pd.to_numeric(df["%Exposed"], errors="coerce").fillna(0.0)
    else:
        df["%Buried"] = _normalize_percent_series(df["%Buried"])

    # Preserve residue identifiers as string-ish for file names.
    resid_num = pd.to_numeric(df["Residue_ID"], errors="coerce")
    var_num = pd.to_numeric(df["Variant"], errors="coerce")
    df["Residue_ID"] = resid_num.fillna(var_num)
    df["Variant"] = df["Variant"].astype(str).replace({"nan": "", "NaN": "", "NAN": ""})

    # Canonical 3-letter ligand enforcement.
    df["Ligand3_Display"] = _compute_ligand3(df)
    df["Warhead"] = df["Ligand3_Display"]
    df["Ligand_Resolved"] = df["Ligand3_Display"]
    df["ligand"] = df["Ligand3_Display"]
    if "Ligand5_Resolved" in df.columns:
        df.drop(columns=["Ligand5_Resolved"], inplace=True)
    if "Ligand3_Display" in df.columns:
        df.drop(columns=["Ligand3_Display"], inplace=True)

    return df


def _read_local_results_display(job_id: str) -> pd.DataFrame:
    jd = _job_dir(job_id)
    fp = _first_existing([
        jd / "TARGET_RESULTS" / "Results_Display.csv",
        jd / "Results_Display.csv",
    ])
    if not fp:
        return pd.DataFrame()
    df = _load_csv_if_exists(fp)
    return _normalize_gallery_df(df) if df is not None else pd.DataFrame()


def _read_randy_results_display(job_id: str) -> pd.DataFrame:
    df = randy_get_table_dataframe(job_id, ["Results_Display.csv"])
    return _normalize_gallery_df(df) if df is not None else pd.DataFrame()


def _read_local_resolved_summary(job_id: str) -> pd.DataFrame:
    jd = _job_dir(job_id)
    fp = _first_existing([
        jd / "Resolved_SASA_Summary.tsv",
        jd / "Resolved_SASA_Summary.csv",
        jd / "TARGET_RESULTS" / "Resolved_SASA_Summary.tsv",
        jd / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
    ])
    if not fp:
        return pd.DataFrame()
    sep = "\t" if fp.suffix.lower() == ".tsv" else ","
    df = _load_csv_if_exists(fp, sep=sep)
    return _normalize_gallery_df(df) if df is not None else pd.DataFrame()


def _read_randy_resolved_summary(job_id: str) -> pd.DataFrame:
    df = randy_get_table_dataframe(job_id, ["Resolved_SASA_Summary.csv", "Resolved_SASA_Summary.tsv"])
    return _normalize_gallery_df(df) if df is not None else pd.DataFrame()


# -----------------------------------------------------------------------------
# Public loaders used by app.py and this blueprint
# -----------------------------------------------------------------------------
def load_resolved_sasa_summary(job_id: str) -> pd.DataFrame:
    df = _read_local_resolved_summary(job_id)
    if df is not None and not df.empty:
        return df
    return _read_randy_resolved_summary(job_id)


def build_pose_rows(job_id: str) -> pd.DataFrame:
    df = load_resolved_sasa_summary(job_id)
    if df is None or df.empty:
        return pd.DataFrame()

    group_cols = [c for c in ["pdb_id", "Warhead", "Chain", "Residue_ID"] if c in df.columns]
    df = df.copy()
    df["_exp_for_rank"] = pd.to_numeric(df.get("%Exposed", 0), errors="coerce").fillna(0.0)

    if group_cols:
        idx = df.groupby(group_cols, dropna=False)["_exp_for_rank"].idxmax()
        df = df.loc[idx].copy()

    df = df.sort_values("_exp_for_rank", ascending=False)
    df.drop(columns=["_exp_for_rank"], inplace=True, errors="ignore")
    return df


def _read_protein_for_local_job(job_id: str) -> str:
    fp = _job_dir(job_id) / "Protein_Data.csv"
    if not fp.exists():
        return ""
    try:
        df = pd.read_csv(fp, dtype=str).fillna("")
        if df.empty:
            return ""
        return (df.iloc[0].get("protein") or "").strip()
    except Exception:
        return ""


def _local_job_state(job_id: str) -> dict | None:
    try:
        return disk_jobs.hydrate_job_from_disk(job_id, current_app.config.get("JOBS_DIR"))
    except Exception:
        return None


def _randy_job_state(job_id: str) -> dict | None:
    data = randy_get_job_index(job_id)
    if not data:
        return None
    tables = data.get("tables", {}) if isinstance(data.get("tables"), dict) else {}
    available_tables = data.get("available_tables", {}) if isinstance(data.get("available_tables"), dict) else {}
    return {
        "job_id": job_id,
        "status": "completed",
        "target": data.get("target_name") or "",
        "results_ready": bool(
            tables.get("Results_Display.csv")
            or tables.get("Resolved_SASA_Summary.csv")
            or available_tables.get("Results_Display.csv")
            or available_tables.get("Resolved_SASA_Summary.csv")
        ),
        "source": data.get("source", "randy_hunter_job_archive"),
        "current_step": "",
        "error": None,
        "archive_layout": data.get("archive_layout") or {},
        "available_tables": available_tables,
    }


# -----------------------------------------------------------------------------
# Route
# -----------------------------------------------------------------------------
@bp.route("/results/<job_id>")
def view_results(job_id: str):
    """
    Durable results gallery route.

    Source order:
      1) local Heroku/dev Results_Display.csv
      2) RANDY archived Results_Display.csv
      3) local Resolved_SASA_Summary.csv fallback
      4) RANDY archived Resolved_SASA_Summary.csv fallback
      5) waiting/error page based on local or RANDY state
    """
    if not _safe_job_id(job_id):
        abort(400, description="Invalid job_id")

    df = _read_local_results_display(job_id)
    source = "local_results_display"

    if df is None or df.empty:
        df = _read_randy_results_display(job_id)
        source = "randy_results_display"

    if df is None or df.empty:
        df = build_pose_rows(job_id)
        source = "summary_fallback"

    if df is None or df.empty:
        job = _local_job_state(job_id) or _randy_job_state(job_id)
        if job is None:
            abort(404, description="Job not found.")

        status = str(job.get("status") or "unknown").lower()
        if status in {"queued", "pending", "running", "unknown"}:
            return render_template(
                "job_waiting.html",
                job_id=job_id,
                title="Results are still being prepared",
                message="The backend job is still running or packaging final artifacts. You can safely refresh this page later.",
                status=status,
                current_step=job.get("current_step", ""),
                status_url=f"/api/jobs/{job_id}",
                results_api_url=f"/api/jobs/{job_id}/results",
                refresh_url=f"/results/{job_id}",
            ), 202

        if status == "failed":
            err = job.get("error") or {}
            reason = err.get("message") if isinstance(err, dict) else str(err or "")
            message = f"Job {job_id} failed before producing result artifacts."
            if reason:
                message = f"{message} {reason}"
            return render_template("error.html", message=message), 409

        if job.get("source") == "randy_hunter_job_archive":
            diag = randy_last_table_diagnostic()
            attempted = diag.get("attempted_paths") if isinstance(diag, dict) else []
            attempted_text = ""
            if attempted:
                attempted_paths = []
                for item in attempted[:12]:
                    if isinstance(item, dict):
                        attempted_paths.append(str(item.get("relative_path") or ""))
                    else:
                        attempted_paths.append(str(item))
                attempted_text = " Paths attempted: " + ", ".join([p for p in attempted_paths if p])
            message = (
                f"Archived job {job_id} exists on RANDY, but the gallery table artifact could not be resolved."
                f"{attempted_text} Next diagnostic: python scripts/debug_randy_results_fallback.py {job_id}"
            )
            return render_template(
                "job_waiting.html",
                job_id=job_id,
                title="Archived results need attention",
                message=message,
                status="archived-incomplete",
                current_step="RANDY archive table lookup",
                status_url=f"/api/jobs/{job_id}",
                results_api_url=f"/api/jobs/{job_id}/results",
                refresh_url=f"/results/{job_id}",
            ), 424

        return render_template(
            "job_waiting.html",
            job_id=job_id,
            title="Results not ready yet",
            message="The job exists, but the final gallery artifact is not readable yet. Try refreshing in a moment.",
            status=status,
            current_step=job.get("current_step", ""),
            status_url=f"/api/jobs/{job_id}",
            results_api_url=f"/api/jobs/{job_id}/results",
            refresh_url=f"/results/{job_id}",
        ), 202

    target_name = ""
    if "Target" in df.columns and not df.empty:
        target_name = str(df.iloc[0].get("Target") or "").strip()

    if not target_name:
        local_job = _local_job_state(job_id)
        if local_job:
            target_name = str(local_job.get("target") or local_job.get("target_name") or "").strip()

    if not target_name:
        randy_job = _randy_job_state(job_id)
        if randy_job:
            target_name = str(randy_job.get("target") or "").strip()

    results = df.to_dict(orient="records")
    return render_template(
        "results_gallery.html",
        job_id=job_id,
        target_name=target_name,
        results=results,
        results_source=source,
        protac_builder_base=os.environ.get(
            "PROTAC_BUILDER_BASE",
            current_app.config.get("PROTAC_BUILDER_BASE", "https://protacbuilder.com/copy/COPYindex"),
        ),
    )
