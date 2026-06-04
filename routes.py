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


def _debug_archive_hint() -> str:
    if archive_enabled():
        return ""
    return (
        " Local job files were not found and RANDY archive access is not configured. "
        "Set RANDY_ARCHIVE_BASE_URL/RANDY_ARCHIVE_TOKEN or RANDY_BACKUP_BASE_URL/RANDY_BACKUP_TOKEN."
    )


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


def _series_key_str(series: pd.Series) -> pd.Series:
    return series.astype(str).fillna("").str.strip().replace({"nan": "", "NaN": "", "NAN": "", "None": "", "NONE": "", "?": ""})


def _series_int_key(series: pd.Series) -> pd.Series:
    nums = pd.to_numeric(series, errors="coerce")
    return nums.apply(lambda x: "" if pd.isna(x) else str(int(x)))


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


def _prepare_summary_match_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["_match_pdb"] = _series_key_str(out["pdb_id"]).str.lower()
    out["_match_chain"] = _series_key_str(out["Chain"]).str.upper()
    out["_match_ligand"] = _compute_ligand3(out).astype(str).str.upper().str.strip()
    out["_match_residue"] = _series_int_key(out["Residue_ID"]) if "Residue_ID" in out.columns else ""
    out["_match_variant"] = _series_int_key(out["Variant"]) if "Variant" in out.columns else ""
    out["_metric_total"] = pd.to_numeric(out.get("Total_atoms", 0), errors="coerce").fillna(0)
    out["_metric_exposed"] = pd.to_numeric(out.get("Exposed_atoms", 0), errors="coerce").fillna(0)
    out["_metric_sasa"] = pd.to_numeric(out.get("SASA_in_complex_A2", 0), errors="coerce").fillna(0.0)
    out["_metric_exposure"] = pd.to_numeric(out.get("%Exposed", 0), errors="coerce").fillna(0.0)
    out["_metric_rank"] = list(
        zip(
            (out["_metric_total"] > 0).astype(int),
            (out["_metric_sasa"] > 0).astype(int),
            out["_metric_total"],
            out["_metric_sasa"],
            out["_metric_exposure"],
        )
    )
    return out


def enrich_gallery_metrics_from_resolved_summary(display_df: pd.DataFrame, summary_df: pd.DataFrame) -> pd.DataFrame:
    if display_df is None or display_df.empty or summary_df is None or summary_df.empty:
        return display_df if display_df is not None else pd.DataFrame()

    display = _prepare_summary_match_frame(display_df)
    summary = _prepare_summary_match_frame(summary_df)
    if display.empty or summary.empty:
        return display_df.copy() if display_df is not None else pd.DataFrame()

    metric_cols = ["Total_atoms", "Exposed_atoms", "SASA_in_complex_A2"]
    for col in metric_cols:
        display[col] = pd.to_numeric(display.get(col), errors="coerce").astype(float)

    summary_by_pdb = {}
    for pdb_id, sdf in summary.groupby("_match_pdb", dropna=False):
        ranked = sdf.sort_values(
            by=["_metric_total", "_metric_sasa", "_metric_exposure"],
            ascending=[False, False, False],
            kind="stable",
        ).copy()
        summary_by_pdb[pdb_id] = ranked

    def needs_enrichment(row: pd.Series) -> bool:
        total = pd.to_numeric(row.get("Total_atoms"), errors="coerce")
        exposed = pd.to_numeric(row.get("Exposed_atoms"), errors="coerce")
        sasa = pd.to_numeric(row.get("SASA_in_complex_A2"), errors="coerce")
        return bool(
            pd.isna(total) or total <= 0 or
            pd.isna(exposed) or exposed < 0 or
            pd.isna(sasa) or sasa <= 0
        )

    def choose_best_match(row: pd.Series) -> pd.Series | None:
        pdb_matches = summary_by_pdb.get(row["_match_pdb"])
        if pdb_matches is None or pdb_matches.empty:
            return None

        ligand = row["_match_ligand"]
        chain = row["_match_chain"]
        residue = row["_match_residue"]
        variant = row["_match_variant"]

        candidate_frames = []
        if ligand and residue:
            candidate_frames.append(
                pdb_matches[
                    (pdb_matches["_match_chain"] == chain) &
                    (pdb_matches["_match_ligand"] == ligand) &
                    (pdb_matches["_match_residue"] == residue)
                ]
            )
        if ligand and variant:
            candidate_frames.append(
                pdb_matches[
                    (pdb_matches["_match_chain"] == chain) &
                    (pdb_matches["_match_ligand"] == ligand) &
                    (pdb_matches["_match_variant"] == variant)
                ]
            )
        if ligand:
            candidate_frames.append(
                pdb_matches[
                    (pdb_matches["_match_chain"] == chain) &
                    (pdb_matches["_match_ligand"] == ligand)
                ]
            )
        if ligand and residue:
            candidate_frames.append(
                pdb_matches[
                    (pdb_matches["_match_ligand"] == ligand) &
                    (pdb_matches["_match_residue"] == residue)
                ]
            )
        if ligand and variant:
            candidate_frames.append(
                pdb_matches[
                    (pdb_matches["_match_ligand"] == ligand) &
                    (pdb_matches["_match_variant"] == variant)
                ]
            )
        if ligand:
            candidate_frames.append(pdb_matches[pdb_matches["_match_ligand"] == ligand])
        candidate_frames.append(pdb_matches[pdb_matches["_match_chain"] == chain])
        candidate_frames.append(pdb_matches)

        for candidates in candidate_frames:
            if candidates is not None and not candidates.empty:
                return candidates.iloc[0]
        return None

    for idx, row in display.iterrows():
        if not needs_enrichment(row):
            continue
        match = choose_best_match(row)
        if match is None:
            continue
        for col in metric_cols:
            current = pd.to_numeric(display.at[idx, col], errors="coerce")
            candidate = pd.to_numeric(match.get(col), errors="coerce")
            if pd.notna(candidate) and candidate > 0 and (pd.isna(current) or current <= 0):
                display.at[idx, col] = candidate
        current_residue = _series_key_str(pd.Series([display.at[idx, "Residue_ID"]])).iloc[0]
        matched_residue = _series_int_key(pd.Series([match.get("Residue_ID", "")])).iloc[0]
        if not current_residue and matched_residue:
            display.at[idx, "Residue_ID"] = float(matched_residue)

    drop_cols = [c for c in display.columns if c.startswith("_match_") or c.startswith("_metric_")]
    return display.drop(columns=drop_cols, errors="ignore")


def _read_local_results_display_raw(job_id: str) -> pd.DataFrame:
    jd = _job_dir(job_id)
    fp = _first_existing([
        jd / "TARGET_RESULTS" / "Results_Display.csv",
        jd / "Results_Display.csv",
    ])
    if not fp:
        return pd.DataFrame()
    df = _load_csv_if_exists(fp)
    return _normalize_gallery_df(df) if df is not None else pd.DataFrame()


def _read_randy_results_display_raw(job_id: str) -> pd.DataFrame:
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


def _read_local_results_display(job_id: str) -> pd.DataFrame:
    display_df = _read_local_results_display_raw(job_id)
    if display_df is None or display_df.empty:
        return pd.DataFrame()
    summary_df = _read_local_resolved_summary(job_id)
    return enrich_gallery_metrics_from_resolved_summary(display_df, summary_df)


def _read_randy_results_display(job_id: str) -> pd.DataFrame:
    display_df = _read_randy_results_display_raw(job_id)
    if display_df is None or display_df.empty:
        return pd.DataFrame()
    summary_df = _read_randy_resolved_summary(job_id)
    return enrich_gallery_metrics_from_resolved_summary(display_df, summary_df)


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
    debug_enabled = str(current_app.config.get("DEBUG", False)).lower() in {"1", "true"} or str(os.getenv("FLASK_DEBUG", "")).strip().lower() in {"1", "true"} or str(os.getenv("ARTIFACT_DEBUG", "")).strip().lower() in {"1", "true"} or str(current_app.config.get("ENV", "")).lower() == "development"

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
            if debug_enabled:
                return render_template("error.html", message=f"Page not found.{_debug_archive_hint()}"), 404
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
