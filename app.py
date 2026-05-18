#!/usr/bin/env python3
# PROTAC Target Module Web Server
# by Joseph-Michael Schulz

from __future__ import annotations

import os
import re
import csv
import zipfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from flask import jsonify
from pathlib import Path
import pandas as pd
import pandas as pd
import requests
import urllib3
from flask import (
    Flask, render_template, request, redirect,
    url_for, send_from_directory, jsonify, abort, send_file, Response
)

from job_runner import start_job, JOB_STORE
from api.sasa_api import bp as sasa_bp
from routes import bp as routes_bp
from api.handoff_server import hand_bp
from pathlib import Path
from flask import render_template, current_app
import time

# Suppress "Unverified HTTPS" warnings so logs stay clean
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -----------------------------
#     APP + PATH CONFIG
# -----------------------------
APP_ROOT = Path(__file__).resolve().parent
JOBS_DIR = (APP_ROOT / "jobs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = str(APP_ROOT / "uploads")
app.config["JOBS_DIR"] = str(JOBS_DIR)
app.register_blueprint(sasa_bp)
app.register_blueprint(routes_bp)
app.register_blueprint(hand_bp)


# Subfolders for organization (uploads area)
FOLDERS = {
    "sasa": "sasa",
    "mcs": "mcs",
    "metadata": "metadata",
    "scaffold": "scaffold",
    "structures": "structures",
}

for folder in FOLDERS.values():
    (Path(app.config["UPLOAD_FOLDER"]) / folder).mkdir(parents=True, exist_ok=True)

# Expected filename: 7pcd_A_70I.pdb
PDB_RE = re.compile(
    r"^([0-9a-z]{4})_([A-Za-z0-9])_([A-Za-z0-9]{2,12})\.pdb$",
    re.IGNORECASE
)

# -----------------------------
#     SMALL HELPERS
# -----------------------------
@app.context_processor
def inject_year():
    from datetime import datetime
    return {"current_year": datetime.now().year}


def job_root(job_id: str) -> Path:
    return JOBS_DIR / job_id


def target_results_dir(job_id: str) -> Path:
    return job_root(job_id) / "TARGET_RESULTS"


def _first_existing(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        if p and p.exists():
            return p
    return None


def _is_under(base: Path, p: Path) -> bool:
    try:
        base = base.resolve()
        p = p.resolve()
        return str(p).startswith(str(base))
    except Exception:
        return False


def safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


def _find_col_case_insensitive(df: pd.DataFrame, options: List[str]) -> Optional[str]:
    cols = list(df.columns)
    for opt in options:
        for c in cols:
            if c.lower() == opt.lower():
                return c
    return None


def sasa_bucket(exposure) -> str:
    x = safe_float(exposure, default=None)
    if x is None:
        return "low"
    if x < 15.0:
        return "low"
    if x < 35.0:
        return "medium"
    return "high"


# -----------------------------
#     RESULTS CSV LOADING
# -----------------------------
def load_results_display(job_id: str) -> Optional[pd.DataFrame]:
    """
    Look for Results_Display.csv in:
      1) jobs/<job>/TARGET_RESULTS/Results_Display.csv
      2) jobs/<job>/Results_Display.csv
    """
    fp = _first_existing([
        target_results_dir(job_id) / "Results_Display.csv",
        job_root(job_id) / "Results_Display.csv",
    ])
    if not fp:
        return None

    df = pd.read_csv(fp, dtype=str).fillna("")

    # normalize expected keys for templates
    if "pdb" in df.columns and "pdb_id" not in df.columns:
        df["pdb_id"] = df["pdb"]

    # normalize exposure column name
    if "%Exposed" not in df.columns:
        if "FracExposed" in df.columns:
            df["%Exposed"] = df["FracExposed"]
        elif "percent_exposed" in df.columns:
            df["%Exposed"] = df["percent_exposed"]
        else:
            df["%Exposed"] = "0.0"

    return df


def infer_chain_from_results(job_id: str, pdb: str, warhead: str) -> Optional[str]:
    """
    If multiple chains exist for a PDB/warhead, pick the chain with max %Exposed.
    """
    df = load_results_display(job_id)
    if df is None or df.empty:
        return None

    needed = {"pdb_id", "Chain", "Warhead", "%Exposed"}
    if not needed.issubset(df.columns):
        return None

    pdb_l = str(pdb).lower().strip()
    war_u = str(warhead).upper().strip()

    sub = df[
        (df["pdb_id"].astype(str).str.lower() == pdb_l) &
        (df["Warhead"].astype(str).str.upper() == war_u)
    ].copy()

    if sub.empty:
        return None

    sub["%Exposed_num"] = pd.to_numeric(sub["%Exposed"], errors="coerce").fillna(0.0)
    best = sub.sort_values("%Exposed_num", ascending=False).iloc[0]
    c = str(best.get("Chain", "")).strip().upper()
    return c if c else None



def mcs_svgs_dir(job_id: str) -> Optional[Path]:
    return _first_existing([
        target_results_dir(job_id) / "MCS_Output" / "MCS_SVG",
        job_root(job_id) / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG",
        job_root(job_id) / "MCS_Output" / "MCS_SVG",
    ])


def infer_residue_from_mcs(job_id: str, pdb: str, chain: str, warhead: str) -> Optional[str]:
    """
    Pick the Residue_ID for this ligand occurrence from Ligand_MCS_Map.csv.
    If multiple residues exist, choose the one with the most mapped rows.
    """
    fp = ligand_mcs_map_path(job_id)
    if not fp or not fp.exists():
        return None

    pdb_l = str(pdb).lower().strip()
    chain_u = str(chain).upper().strip()
    war_u = str(warhead).upper().strip()

    try:
        df = pd.read_csv(fp, dtype=str).fillna("")
    except Exception:
        return None

    needed = {"pdb_id", "Chain", "Ligand", "Residue_ID"}
    if not needed.issubset(df.columns):
        return None

    sub = df[
        (df["pdb_id"].astype(str).str.lower().str.strip() == pdb_l) &
        (df["Chain"].astype(str).str.upper().str.strip() == chain_u) &
        (df["Ligand"].astype(str).str.upper().str.strip() == war_u)
    ].copy()

    if sub.empty:
        return None

    # choose residue id with the most mapped atoms
    counts = sub["Residue_ID"].astype(str).str.strip().value_counts()
    resid = counts.index[0] if len(counts) else None
    return resid or None







def ligand_svgs_dir(job_id: str) -> Optional[Path]:
    return _first_existing([
        # target_results_dir(job_id) / "LIGAND_SVGS",
        job_root(job_id) / "MCS_Output" / "MCS_SVGS",
        # job_root(job_id) / "LIGAND_SVGS",
    ])


def warhead_sasa_atoms_path(job_id: str) -> Optional[Path]:
    return _first_existing([
        target_results_dir(job_id) / "Warhead_SASA_atoms.csv",
        job_root(job_id) / "TARGET_RESULTS" / "Warhead_SASA_atoms.csv",
        job_root(job_id) / "Warhead_SASA_atoms.csv",
    ])


def ligand_mcs_map_path(job_id: str) -> Optional[Path]:
    return _first_existing([
        target_results_dir(job_id) / "MCS_Output" / "Ligand_MCS_Map.csv",
        job_root(job_id) / "TARGET_RESULTS" / "MCS_Output" / "Ligand_MCS_Map.csv",
        job_root(job_id) / "MCS_Output" / "Ligand_MCS_Map.csv",
    ])


# -----------------------------
# PDB lookup for full complex files
# -----------------------------
def lookup_pdb_file(job_id: str, pdb: str, chain: str, warhead: str) -> Optional[Path]:
    """
    Preferred: use Results_Display.csv 'pdb_path' (fast/correct).
    Accepts pdb_path under either:
      - jobs/<job>/...
      - jobs/<job>/TARGET_RESULTS/...
    Fallback: search WAR_PDB under both locations.
    """
    pdb = str(pdb).lower().strip()
    chain = str(chain).upper().strip()
    warhead = str(warhead).upper().strip()

    df = load_results_display(job_id)
    if df is not None and not df.empty:
        needed = {"pdb_id", "Chain", "Warhead"}
        if needed.issubset(df.columns):
            sub = df[
                (df["pdb_id"].astype(str).str.lower() == pdb) &
                (df["Chain"].astype(str).str.upper() == chain) &
                (df["Warhead"].astype(str).str.upper() == warhead)
            ]
            if not sub.empty:
                p = str(sub.iloc[0].get("pdb_path", "")).strip()
                if p:
                    path = Path(p)
                    if not path.is_absolute():
                        tr = (target_results_dir(job_id) / p).resolve()
                        jr = (job_root(job_id) / p).resolve()
                        if tr.exists() and _is_under(job_root(job_id), tr):
                            return tr
                        if jr.exists() and _is_under(job_root(job_id), jr):
                            return jr
                    else:
                        path = path.resolve()
                        if path.exists() and _is_under(job_root(job_id), path):
                            return path

    # Fallback search
    fname = f"{pdb}_{chain}_{warhead}.pdb"
    fallback_roots = [
        target_results_dir(job_id) / "WAR_PDB",
        job_root(job_id) / "WAR_PDB",
    ]
    for base in fallback_roots:
        if not base.exists():
            continue

        # WAR_PDB may contain either files directly or per-target subdirs
        for fp in base.rglob(fname):
            if fp.exists():
                return fp.resolve()

    return None


# -----------------------------
# Ligand SDF (authoritative)
# -----------------------------
def ligand_sdf_path(job_id: str, pdb: str, chain: str, warhead: str) -> Optional[Path]:
    pdb = pdb.lower().strip()
    chain = chain.upper().strip()
    warhead = warhead.upper().strip()

    candidates = [
        target_results_dir(job_id) / "LIGAND_SDF" / f"{pdb}_{chain}_{warhead}.sdf",
        job_root(job_id) / "TARGET_RESULTS" / "LIGAND_SDF" / f"{pdb}_{chain}_{warhead}.sdf",
        job_root(job_id) / "LIGAND_SDF" / f"{pdb}_{chain}_{warhead}.sdf",
    ]

    for p in candidates:
        if p.exists() and _is_under(job_root(job_id), p):
            return p
    return None


# -----------------------------
#        ROUTES (PAGES)
# -----------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/upload_manual")
def upload_manual():
    return render_template("upload.html")


@app.route("/hunter")
def hunter():
    return render_template("warhead_hunter.html")



from datetime import datetime

def _safe_job_id(job_id: str) -> bool:
    if not job_id:
        return False
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        return False
    # your job ids look hex-ish (5ba87d93), but don't over-restrict
    return True


def _read_protein_data_csv(job_path: Path) -> Optional[Dict[str, str]]:
    """
    Returns: {protein, search_query, fasta} from Protein_Data.csv if present.
    Handles your format where fasta includes a header line starting with ">"
    and the sequence continues on next lines (possibly quoted).
    """
    fp = job_path / "Protein_Data.csv"
    if not fp.exists():
        return None

    try:
        # Use pandas to tolerate commas/quotes/newlines in fasta field
        df = pd.read_csv(fp, dtype=str).fillna("")
        if df.empty:
            return None
        row = df.iloc[0].to_dict()
        protein = (row.get("protein") or "").strip()
        query = (row.get("search_query") or "").strip()
        fasta = (row.get("fasta") or "").strip()
        return {"protein": protein, "search_query": query, "fasta": fasta}
    except Exception:
        # fallback naive read
        try:
            txt = fp.read_text(errors="ignore")
            # very rough fallback: try to parse first 2 commas
            # protein,search_query,fasta
            lines = txt.splitlines()
            if len(lines) < 2:
                return None
            header = lines[0].split(",")
            # assume second line begins data; keep the rest as fasta
            first = lines[1]
            parts = first.split(",", 2)
            if len(parts) < 3:
                return None
            return {"protein": parts[0].strip(), "search_query": parts[1].strip(), "fasta": parts[2].strip()}
        except Exception:
            return None


def _job_has_results(job_path: Path) -> bool:
    # a few “likely exists” checks
    candidates = [
        job_path / "TARGET_RESULTS" / "Results_Display.csv",
        job_path / "Results_Display.csv",
        job_path / "TARGET_RESULTS",
        job_path / "Target_Table",
    ]
    return any(p.exists() for p in candidates)



def ligand_sdf_dir(job_id: str) -> Optional[Path]:
    return _first_existing([
        target_results_dir(job_id) / "LIGAND_SDF",
        job_root(job_id) / "TARGET_RESULTS" / "LIGAND_SDF",
        job_root(job_id) / "LIGAND_SDF",
    ])



@app.route("/browse")
def browse():
    jobs_root = Path(app.config["JOBS_DIR"])
    jobs = []

    for d in sorted(jobs_root.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        jid = d.name
        if not _safe_job_id(jid):
            continue

        meta = _read_protein_data_csv(d) or {}
        fasta = (meta.get("fasta") or "").strip()
        # tidy: if fasta is quoted by CSV, leave it; template wraps
        fasta_len = 0
        if fasta:
            # rough AA count (strip header lines starting with > and non-letters)
            seq = "\n".join([ln for ln in fasta.splitlines() if not ln.strip().startswith(">")])
            seq = re.sub(r"[^A-Za-z]", "", seq)
            fasta_len = len(seq)

        # last modified time (for sorting display later if you want)
        try:
            mtime = datetime.fromtimestamp(d.stat().st_mtime)
            mtime_s = mtime.strftime("%Y-%m-%d %H:%M")
        except Exception:
            mtime_s = ""

        jobs.append({
            "job_id": jid,
            "protein": (meta.get("protein") or "").strip(),
            "search_query": (meta.get("search_query") or "").strip(),
            "fasta": fasta,
            "fasta_len": fasta_len,
            "has_results": _job_has_results(d),
            "mtime": mtime_s,
        })

    # Optional: newest first by mtime
    jobs.sort(key=lambda x: x.get("mtime", ""), reverse=True)

    return render_template("browse.html", jobs=jobs)


@app.get("/open_job/<job_id>")
def open_job(job_id):
    if not _safe_job_id(job_id):
        abort(400, "Invalid job_id")

    # If it’s an in-memory active job, use monitor (live logs)
    if job_id in JOB_STORE:
        return redirect(url_for("job_monitor", job_id=job_id))

    # If it exists on disk, prefer results view if available
    jp = job_root(job_id)
    if not jp.exists():
        abort(404, "Job not found on disk.")

    if _job_has_results(jp):
        return redirect(f"/results/{job_id}")

    # Otherwise: could optionally show a “job summary” page, but keep it simple
    return render_template("error.html", message="This job exists on disk but has no results to display yet."), 404

import io

@app.get("/api/jobs/<job_id>/download")
def api_download_job(job_id):
    if not _safe_job_id(job_id):
        abort(400, "Invalid job_id")

    base = job_root(job_id)
    if not base.exists():
        abort(404, "Job not found")

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for fp in base.rglob("*"):
            if fp.is_dir():
                continue
            # keep relative inside job folder
            rel = fp.relative_to(base)
            z.write(fp, arcname=str(Path(job_id) / rel))

    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{job_id}.zip"
    )


@app.route("/explore")
def explore():
    root = Path(app.config["UPLOAD_FOLDER"]) / "structures"
    pdb_files = sorted([f.name for f in root.iterdir() if f.suffix.lower() in [".pdb", ".cif"]])
    return render_template("explore.html", files=pdb_files)



@app.get("/api/jobs/export")
def api_export_jobs():
    jobs_root = Path(app.config["JOBS_DIR"])
    rows = []
    for d in sorted(jobs_root.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        jid = d.name
        if not _safe_job_id(jid):
            continue
        meta = _read_protein_data_csv(d) or {}
        rows.append({
            "job_id": jid,
            "protein": (meta.get("protein") or "").strip(),
            "search_query": (meta.get("search_query") or "").strip(),
            "has_results": "yes" if _job_has_results(d) else "no"
        })

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=["job_id", "protein", "search_query", "has_results"])
    w.writeheader()
    for r in rows:
        w.writerow(r)

    return Response(out.getvalue(), mimetype="text/csv")



@app.get("/api/job_summary/<job_id>")
def job_summary(job_id):
    if not _safe_job_id(job_id):
        abort(400, "Invalid job_id")

    jp = job_root(job_id)
    if not jp.exists():
        return jsonify({
            "job_id": job_id,
            "target": None,
            "total_warheads": 0,
            "unique_warheads": 0,
            "structures_downloaded": None,
            "warhead_id_col": None
        }), 404

    # ---- infer target (best effort) ----
    target = None
    if job_id in JOB_STORE:
        target = (JOB_STORE[job_id].get("target") or "").strip() or None
    if not target:
        meta = _read_protein_data_csv(jp) or {}
        target = (meta.get("protein") or "").strip() or None

    # ---- find the CSV (you may store it in either place) ----
    csv_path = _first_existing([
        jp / "Resolved_SASA_Summary.csv",
        jp / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
        target_results_dir(job_id) / "Resolved_SASA_Summary.csv",
    ])

    if not csv_path or not csv_path.exists():
        return jsonify({
            "job_id": job_id,
            "target": target,
            "total_warheads": 0,
            "unique_warheads": 0,
            "structures_downloaded": None,
            "warhead_id_col": None
        })

    try:
        df = pd.read_csv(csv_path, dtype=str).fillna("")
    except Exception:
        return jsonify({
            "job_id": job_id,
            "target": target,
            "total_warheads": 0,
            "unique_warheads": 0,
            "structures_downloaded": None,
            "warhead_id_col": None
        })

    # Choose the best identity column for "unique"
    id_col = None
    for c in ["Ligand5_Resolved", "Ligand_Resolved", "Warhead", "Ligand5", "Ligand"]:
        if c in df.columns:
            id_col = c
            break

    if not id_col:
        total = int(len(df))
        uniq = int(len(df))
    else:
        s = df[id_col].astype(str).str.strip()
        s = s.replace({"nan": "", "None": ""})
        nonblank = s[s != ""]
        total = int(len(nonblank))
        uniq = int(nonblank.nunique())

    # OPTIONAL: structures_downloaded (best-effort)
    # If you store CIFs/PDBs somewhere standard, you can compute it.
    # For now keep None unless you want to uncomment this.
    structures_downloaded = None
    # structures_dir = jp / "TARGET_RESULTS" / "STRUCTURES"
    # if structures_dir.exists():
    #     structures_downloaded = len(list(structures_dir.glob("*.cif"))) + len(list(structures_dir.glob("*.pdb")))

    return jsonify({
        "job_id": job_id,
        "target": target,
        "total_warheads": total,
        "unique_warheads": uniq,
        "structures_downloaded": structures_downloaded,
        "warhead_id_col": id_col
    })

@app.route("/viewer/<path:filename>")
def viewer(filename):
    file_url = url_for("serve_structure", filename=filename)
    return render_template("viewer.html", file_url=file_url)


@app.route("/structures/<path:filename>")
def serve_structure(filename):
    folder = Path(app.config["UPLOAD_FOLDER"]) / "structures"
    return send_from_directory(folder, filename)


import csv
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import current_app, jsonify, render_template, request

# -----------------------------------
# Lightweight in-memory cache
# -----------------------------------
ABOUT_STATS_TTL_SECONDS = 60

_about_stats_cache = {
    "timestamp": 0.0,
    "stats": {
        "total_jobs": 0,
        "total_mcs": 0,
        "total_sasa": 0,
        "struct_count": 0,
    }
}

_builderjobs_lock = threading.Lock()


def _count_sdf_files(folder: Path) -> int:
    if not folder.exists() or not folder.is_dir():
        return 0

    return sum(
        1 for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() == ".sdf"
    )


def _builderjobs_csv_path() -> Path:
    return Path(current_app.root_path) / "static" / "data" / "builderjobs.csv"


def _ensure_builderjobs_csv_exists() -> Path:
    csv_path = _builderjobs_csv_path()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Date", "smile", "job"])

    return csv_path


def _append_builderjob(smile: str, job: str) -> None:
    csv_path = _ensure_builderjobs_csv_exists()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _builderjobs_lock:
        with csv_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([timestamp, smile, job])

    # Invalidate cached about stats immediately
    _about_stats_cache["timestamp"] = 0.0


def _count_builderjobs_rows() -> int:
    csv_path = _ensure_builderjobs_csv_exists()

    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        rows = list(reader)

    # subtract header
    return max(0, len(rows) - 1)


def _compute_about_stats() -> dict:
    jobs_dir = Path(current_app.root_path) / "jobs"

    total_jobs = 0
    total_mcs = 0

    if jobs_dir.exists() and jobs_dir.is_dir():
        job_dirs = [p for p in jobs_dir.iterdir() if p.is_dir()]
        total_jobs = len(job_dirs)

        for job_dir in job_dirs:
            mcs_sdf_dir = job_dir / "MCS_Output" / "MCS_SDF"
            total_mcs += _count_sdf_files(mcs_sdf_dir)

    total_sasa = total_mcs
    struct_count = _count_builderjobs_rows()

    return {
        "total_jobs": total_jobs,
        "total_mcs": total_mcs,
        "total_sasa": total_sasa,
        "struct_count": struct_count,
    }


def get_about_stats(force_refresh: bool = False) -> dict:
    now = time.time()
    cache_age = now - _about_stats_cache["timestamp"]

    if not force_refresh and cache_age < ABOUT_STATS_TTL_SECONDS:
        return _about_stats_cache["stats"]

    stats = _compute_about_stats()
    _about_stats_cache["timestamp"] = now
    _about_stats_cache["stats"] = stats
    return stats


@app.route("/api/log-builder-click", methods=["POST"])
def log_builder_click():
    payload = request.get_json(silent=True) or {}

    smile = (payload.get("smile") or "").strip()
    job = (payload.get("job") or "").strip()

    if not smile:
        return jsonify({"ok": False, "error": "Missing SMILES"}), 400

    if not job:
        return jsonify({"ok": False, "error": "Missing job id"}), 400

    _append_builderjob(smile=smile, job=job)
    return jsonify({"ok": True})


@app.route("/about")
def about():
    stats = get_about_stats()
    return render_template("about.html", **stats)


@app.route("/scout")
def rcsb_scout():
    return render_template("rcsb_scout.html")


# -----------------------------
#        ROUTES (UPLOAD)
# -----------------------------
@app.route("/upload", methods=["POST"])
def upload_files():
    def save_anything(upload, subfolder: str):
        if not upload or upload.filename == "":
            return None
        dst_dir = Path(app.config["UPLOAD_FOLDER"]) / subfolder
        dst_dir.mkdir(parents=True, exist_ok=True)
        path = dst_dir / upload.filename
        upload.save(str(path))
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path, "r") as z:
                z.extractall(str(dst_dir))
        return str(path)

    save_anything(request.files.get("sasa_file"), "sasa")
    save_anything(request.files.get("mcs_file"), "mcs")
    save_anything(request.files.get("metadata_file"), "metadata")
    save_anything(request.files.get("scaffold_file"), "scaffold")
    save_anything(request.files.get("structures_zip"), "structures")

    return redirect("/browse")


# -----------------------------
#        ROUTES (JOBS)
# -----------------------------
@app.route("/launch_job", methods=["POST"])
def launch_job():
    target = request.form.get("target_name")
    query = request.form.get("search_query")
    fasta = request.form.get("fasta_seq")

    if not target or not query:
        return "Missing Data", 400

    job_id = start_job(target, query, fasta)
    return redirect(url_for("job_monitor", job_id=job_id))


@app.route("/monitor/<job_id>")
def job_monitor(job_id):
    if job_id not in JOB_STORE:
        return "Job not found", 404
    return render_template("monitor.html", job=JOB_STORE[job_id], job_id=job_id)


@app.route("/api/job_log/<job_id>")
def job_log(job_id):
    if job_id not in JOB_STORE:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": JOB_STORE[job_id]["status"],
        "log": JOB_STORE[job_id]["log"],
    })


@app.route("/api/target-stats")
def target_stats():
    root = Path(app.config["UPLOAD_FOLDER"])
    out = {}
    for key, folder in FOLDERS.items():
        p = root / folder
        out[key] = len([x for x in p.iterdir() if x.exists()])
    return jsonify(out)


# -----------------------------
#        ROUTES (RCSB / FASTA)
# -----------------------------
@app.route("/api/proxy_fasta/<pdb_id>")
def proxy_fasta(pdb_id):
    if not pdb_id or len(pdb_id) != 4:
        return "Invalid PDB ID", 400

    pdb_id = pdb_id.upper()
    sources = [
        f"https://files.rcsb.org/download/{pdb_id}.fasta",
        f"https://www.ebi.ac.uk/pdbe/api/pdb/entry/molecules/{pdb_id}",
    ]

    for url in sources:
        try:
            resp = requests.get(url, timeout=10, verify=False)
            if resp.status_code != 200:
                continue

            content = resp.text
            if "<html" in content.lower() or "<!doctype" in content.lower():
                continue

            return Response(content, mimetype="text/plain")
        except Exception:
            continue

    return "Failed to fetch sequence. Please copy/paste manually from RCSB.", 500



# ----------------------------
# SVG
# ----------------------------
@app.route("/api/svg/<job_id>/<pdb>/<chain>/<warhead>")
@app.route("/api/svg/<job_id>/<pdb>/<warhead>")
def api_svg(job_id, pdb, warhead, chain=None):
    pdb = str(pdb).lower().strip()
    warhead = str(warhead).upper().strip()
    chain = (chain or infer_chain_from_results(job_id, pdb, warhead) or "A").upper()

    # --- NEW: try MCS SVGs first ---
    mcs_dir = mcs_svgs_dir(job_id)
    if mcs_dir:
        # allow caller override: /api/svg/... ?resid=1101
        resid = (request.args.get("resid") or "").strip()
        if not resid:
            resid = infer_residue_from_mcs(job_id, pdb, chain, warhead) or ""

        # 1) exact match if resid known
        if resid:
            fname = f"{pdb}_{chain}_{warhead}_{resid}_exposed.svg"
            fp = (mcs_dir / fname)
            if fp.exists():
                return send_file(fp)

        # 2) fallback: wildcard any residue
        hits = sorted(mcs_dir.glob(f"{pdb}_{chain}_{warhead}_*_exposed.svg"))
        if hits:
            return send_file(hits[0])

    # --- Legacy fallback (old LIGAND_SVGS behavior) ---
    base_dir = ligand_svgs_dir(job_id)
    if not base_dir:
        abort(404)

    fname = f"{pdb}_{chain}_{warhead}_exposed.svg"
    fp = base_dir / fname
    if fp.exists():
        return send_from_directory(base_dir, fname)

    abort(404, description="Exposed SVG not found")



@app.route("/api/svg-plain/<job_id>/<pdb>/<chain>/<warhead>")
@app.route("/api/svg-plain/<job_id>/<pdb>/<warhead>")
def api_svg_plain(job_id, pdb, warhead, chain=None):
    pdb = str(pdb).lower().strip()
    warhead = str(warhead).upper().strip()
    chain = (chain or infer_chain_from_results(job_id, pdb, warhead) or "A").upper()

    # --- NEW: try MCS SVGs first ---
    mcs_dir = mcs_svgs_dir(job_id)
    if mcs_dir:
        resid = (request.args.get("resid") or "").strip()
        if not resid:
            resid = infer_residue_from_mcs(job_id, pdb, chain, warhead) or ""

        # 1) exact match if resid known
        if resid:
            fname = f"{pdb}_{chain}_{warhead}_{resid}_plain.svg"
            fp = (mcs_dir / fname)
            if fp.exists():
                return send_file(fp)

        # 2) fallback: wildcard any residue
        hits = sorted(mcs_dir.glob(f"{pdb}_{chain}_{warhead}_*_plain.svg"))
        if hits:
            return send_file(hits[0])

    # --- Legacy fallback (old LIGAND_SVGS behavior) ---
    base_dir = ligand_svgs_dir(job_id)
    if not base_dir:
        abort(404, description="LIGAND_SVGS folder not found")

    fname = f"{pdb}_{chain}_{warhead}_plain.svg"
    fp = base_dir / fname
    if fp.exists():
        return send_from_directory(base_dir, fname)

    legacy = base_dir / f"{pdb}_{chain}_{warhead}.svg"
    if legacy.exists():
        return send_from_directory(base_dir, legacy.name)

    abort(404, description="Plain SVG not found")


# ----------------------------
# PDB (single authoritative route)
# /api/pdb/<job>/<pdb>_<chain>_<warhead>.pdb
# ----------------------------
@app.get("/api/pdb/<job_id>/<pdb_chain_warhead>.pdb")
def api_pdb(job_id, pdb_chain_warhead):
    parts = str(pdb_chain_warhead).split("_")
    if len(parts) < 3:
        abort(404)

    pdb = parts[0]
    chain = parts[1]
    warhead = "_".join(parts[2:])

    fp = lookup_pdb_file(job_id, pdb, chain, warhead)
    if not fp or not fp.exists():
        abort(404, description="PDB not found")

    return send_file(
        fp,
        mimetype="chemical/x-pdb",
        as_attachment=False,
        download_name=f"{pdb}_{chain}_{warhead}.pdb"
    )


# ----------------------------
# Protein-only PDB for NGL surface context
# ----------------------------
@app.get("/api/protein/<job_id>/<pdb>/<chain>")
def api_protein(job_id, pdb, chain):
    pdb = pdb.lower().strip()
    chain = chain.upper().strip()

    # Find ANY ligand PDB for this chain (then filter ATOM lines)
    base = target_results_dir(job_id)
    roots = [
        base / "WAR_PDB",
        job_root(job_id) / "WAR_PDB",
    ]

    candidates: List[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(list(root.rglob(f"{pdb}_{chain}_*.pdb")))

    if not candidates:
        abort(404, description="Protein PDB not found")

    src = candidates[0]

    lines = []
    with src.open() as fh:
        for ln in fh:
            if ln.startswith("ATOM") and ln[21].upper() == chain:
                lines.append(ln)
            elif ln.startswith("TER"):
                lines.append(ln)

    if not lines:
        abort(404, description="No protein atoms found")

    lines.append("END\n")

    return Response(
        "".join(lines),
        mimetype="chemical/x-pdb",
        headers={"Content-Disposition": f"inline; filename={pdb}_{chain}_protein.pdb"}
    )


# ----------------------------
# Ligand props (Ligand_Metadata.csv)
# ----------------------------
@app.get("/api/ligand_props/<job_id>/<ligand_code>")
def api_ligand_props(job_id, ligand_code):
    meta = _first_existing([
        target_results_dir(job_id) / "Ligand_Metadata.csv",
        job_root(job_id) / "TARGET_RESULTS" / "Ligand_Metadata.csv",
        job_root(job_id) / "Ligand_Metadata.csv",
    ])

    ligand_code_u = str(ligand_code).upper().strip()
    smiles = (request.args.get("smiles") or "").strip()

    # ---------- helper: compute from SMILES ----------
    def compute_from_smiles(smiles_str: str) -> Dict[str, Any]:
        try:
            from rdkit import Chem
            from rdkit.Chem import Descriptors, Crippen, Lipinski, rdMolDescriptors, QED
        except Exception:
            return {}

        mol = Chem.MolFromSmiles(smiles_str)
        if mol is None:
            return {}

        mw   = Descriptors.MolWt(mol)
        logp = Crippen.MolLogP(mol)
        tpsa = rdMolDescriptors.CalcTPSA(mol)
        hba  = Lipinski.NumHAcceptors(mol)
        hbd  = Lipinski.NumHDonors(mol)
        rot  = Lipinski.NumRotatableBonds(mol)
        rings = rdMolDescriptors.CalcNumRings(mol)
        arom  = rdMolDescriptors.CalcNumAromaticRings(mol)
        qed  = float(QED.qed(mol))

        # Basic “rule” checks (good enough for UI chips)
        lipinski = (mw <= 500 and logp <= 5 and hbd <= 5 and hba <= 10)
        veber    = (rot <= 10 and tpsa <= 140)

        # Lightweight approximations for the others (optional; remove if you prefer)
        # Ghose: MW 160-480, LogP -0.4 to 5.6, atoms 20-70, MR 40-130
        atoms = mol.GetNumAtoms()
        mr = Crippen.MolMR(mol)
        ghose = (160 <= mw <= 480 and -0.4 <= logp <= 5.6 and 20 <= atoms <= 70 and 40 <= mr <= 130)

        # Egan: TPSA <= 131.6 and LogP <= 5.88
        egan = (tpsa <= 131.6 and logp <= 5.88)

        # Muegge (very simplified): MW 200–600, LogP -2–5, TPSA <= 150, rings <= 7, rot <= 15, HBA<=10, HBD<=5
        muegge = (200 <= mw <= 600 and -2 <= logp <= 5 and tpsa <= 150 and rings <= 7 and rot <= 15 and hba <= 10 and hbd <= 5)

        return {
            "MW": mw,
            "LogP": logp,
            "TPSA": tpsa,
            "HBA": int(hba),
            "HBD": int(hbd),
            "Rotatable_Bonds": int(rot),
            "Ring_Count": int(rings),
            "Aromatic_Rings": int(arom),
            "QED": qed,
            "Lipinski_Pass": bool(lipinski),
            "Veber_Pass": bool(veber),
            "Ghose_Pass": bool(ghose),
            "Egan_Pass": bool(egan),
            "Muegge_Pass": bool(muegge),
        }

    # ---------- helper: normalize CSV row keys to what JS expects ----------
    def normalize_keys(d: Dict[str, Any]) -> Dict[str, Any]:
        # map common alternatives -> canonical keys your JS renders
        aliases = {
            "MW": ["MW", "MolWt", "MolecularWeight", "Molecular_Weight"],
            "LogP": ["LogP", "cLogP", "MolLogP", "XlogP"],
            "TPSA": ["TPSA", "tPSA"],
            "HBA": ["HBA", "NumHAcceptors", "H_Acceptors"],
            "HBD": ["HBD", "NumHDonors", "H_Donors"],
            "Rotatable_Bonds": ["Rotatable_Bonds", "NumRotatableBonds", "RotB"],
            "Ring_Count": ["Ring_Count", "NumRings", "Rings"],
            "Aromatic_Rings": ["Aromatic_Rings", "NumAromaticRings", "AromRings"],
            "QED": ["QED", "qed"],
            "Lipinski_Pass": ["Lipinski_Pass", "Lipinski", "LipinskiPass"],
            "Veber_Pass": ["Veber_Pass", "Veber", "VeberPass"],
            "Ghose_Pass": ["Ghose_Pass", "Ghose", "GhosePass"],
            "Muegge_Pass": ["Muegge_Pass", "Muegge", "MueggePass"],
            "Egan_Pass": ["Egan_Pass", "Egan", "EganPass"],
        }

        out = dict(d)
        # fill canonical keys from any alias if missing
        for canon, opts in aliases.items():
            if canon in out and out[canon] not in (None, "", "None"):
                continue
            for k in opts:
                if k in d and d[k] not in (None, "", "None"):
                    out[canon] = d[k]
                    break
        return out

    # ---------- 1) Try metadata lookup ----------
    if meta and meta.exists():
        try:
            df = pd.read_csv(meta, dtype=str).fillna("")
        except Exception:
            df = None

        if df is not None and not df.empty:
            # Try several possible identifier columns
            id_cols = [c for c in df.columns if c.lower() in ("ligand", "ligand5", "warhead", "resname", "ligand_code")]
            # fallback: if none matched by name, still try "Ligand" if present
            if "Ligand" in df.columns and "Ligand" not in id_cols:
                id_cols.append("Ligand")

            row = None
            for col in id_cols:
                sub = df[df[col].astype(str).str.upper().str.strip() == ligand_code_u]
                if not sub.empty:
                    row = sub.iloc[0]
                    break

            if row is not None:
                d = row.to_dict()
                # convert blanks -> None
                for k, v in list(d.items()):
                    if v == "" or (isinstance(v, float) and pd.isna(v)):
                        d[k] = None
                d = normalize_keys(d)
                return jsonify(d)

    # ---------- 2) Fallback: compute from SMILES ----------
    if smiles:
        d = compute_from_smiles(smiles)
        if d:
            return jsonify(d)

    return jsonify({})




def mcs_sdf_dir(job_id: str) -> Optional[Path]:
    return _first_existing([
        target_results_dir(job_id) / "MCS_Output" / "MCS_SDF",
        job_root(job_id) / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF",
        job_root(job_id) / "MCS_Output" / "MCS_SDF",
    ])


# ----------------------------
# Ligand SDF (authoritative)
# ----------------------------
@app.route("/api/sdf/<job_id>/<pdb>/<chain>/<ligand>")
@app.route("/api/sdf/<job_id>/<pdb>/<ligand>")
def api_sdf(job_id, pdb, ligand, chain=None):
    pdb = str(pdb).lower().strip()
    ligand = str(ligand).upper().strip()
    chain = (chain or infer_chain_from_results(job_id, pdb, ligand) or "A").upper()

    # --- NEW: try MCS SDF first ---
    mcs_dir = mcs_sdf_dir(job_id)
    if mcs_dir:
        resid = (request.args.get("resid") or "").strip()
        if not resid:
            resid = infer_residue_from_mcs(job_id, pdb, chain, ligand) or ""

        # exact if resid known
        if resid:
            fp = mcs_dir / f"{pdb}_{chain}_{ligand}_{resid}.sdf"
            if fp.exists():
                return send_file(fp)

        # wildcard fallback
        hits = sorted(mcs_dir.glob(f"{pdb}_{chain}_{ligand}_*.sdf"))
        if hits:
            return send_file(hits[0])

    # --- Legacy fallback (whatever you had before) ---
    base_dir = ligand_sdf_dir(job_id)  # your existing legacy helper
    if base_dir:
        fp = base_dir / f"{pdb}_{chain}_{ligand}.sdf"
        if fp.exists():
            return send_file(fp)

    abort(404, description="SDF not found")



# ----------------------------
# LIGAND CHAIN helper
# ----------------------------
@app.get("/api/ligand_chain/<job_id>/<pdb>/<warhead>")
def api_ligand_chain(job_id, pdb, warhead):
    chain = infer_chain_from_results(job_id, pdb, warhead)
    if chain:
        return jsonify({"chain": chain})

    fp = warhead_sasa_atoms_path(job_id)
    if not fp:
        return jsonify({"chain": None})

    df = pd.read_csv(fp, dtype=str).fillna("")
    sub = df[
        (df.get("pdb_id", "").astype(str).str.lower() == str(pdb).lower()) &
        (df.get("Warhead", "").astype(str).str.upper() == str(warhead).upper())
    ]
    if "Chain" not in df.columns or sub.empty:
        return jsonify({"chain": None})

    chains = sub["Chain"].dropna().astype(str).str.upper().unique().tolist()
    return jsonify({"chain": chains[0] if chains else None})


# ----------------------------
# SASA overlay points (your JS uses this)
# /api/sasa_overlay/<job>/<pdb>/<chain>/<warhead>
# ----------------------------
@app.get("/api/sasa_overlay/<job_id>/<pdb>/<chain>/<warhead>")
def api_sasa_overlay(job_id, pdb, chain, warhead):
    pdb = pdb.lower().strip()
    chain = chain.upper().strip()
    warhead = warhead.upper().strip()

    fp = warhead_sasa_atoms_path(job_id)
    if not fp:
        return jsonify([]), 200

    df = pd.read_csv(fp, dtype=str).fillna("")
    needed = {"pdb_id", "Chain", "Warhead", "x", "y", "z", "Exposure_A2"}
    if not needed.issubset(df.columns):
        return jsonify([]), 200

    sub = df[
        (df["pdb_id"].astype(str).str.lower().str.strip() == pdb) &
        (df["Chain"].astype(str).str.upper().str.strip() == chain) &
        (df["Warhead"].astype(str).str.upper().str.strip() == warhead)
    ].copy()

    if sub.empty:
        return jsonify([]), 200

    for c in ["x", "y", "z", "Exposure_A2"]:
        sub[c] = pd.to_numeric(sub[c], errors="coerce")
    sub = sub.dropna(subset=["x", "y", "z", "Exposure_A2"])

    def rgb01(exp: float):
        if exp > 35.0:
            return [0.835, 0.000, 0.000]
        if exp >= 15.0:
            return [1.000, 0.839, 0.000]
        return [0.000, 0.784, 0.325]

    out = []
    for _, r in sub.iterrows():
        exp = float(r["Exposure_A2"])
        out.append({
            "atom_id": int(float(r["atom_id"])) if str(r.get("atom_id", "")).strip() else None,
            "atom_name": str(r.get("exact_atom", "")).strip(),
            "x": float(r["x"]),
            "y": float(r["y"]),
            "z": float(r["z"]),
            "exposure": exp,
            "bucket": sasa_bucket(exp),
            "color": rgb01(exp),
        })

    # Deduplicate (keep max exposure) by atom_id if present, else by coordinate key
    best = {}
    for p in out:
        key = str(p["atom_id"]).strip() if p["atom_id"] is not None else f'{p["x"]:.3f},{p["y"]:.3f},{p["z"]:.3f}'
        if key not in best or p["exposure"] > best[key]["exposure"]:
            best[key] = p

    return jsonify(list(best.values())), 200


# ----------------------------
# SASA atommap (fixed: no hard-coded relative path)
# /api/sasa_atommap/<job>/<pdb>/<chain>/<warhead>
# ----------------------------
@app.get("/api/sasa_atommap/<job_id>/<pdb>/<chain>/<warhead>")
def api_sasa_atommap(job_id, pdb, chain, warhead):
    pdb = pdb.lower().strip()
    chain = chain.upper().strip()
    warhead = warhead.upper().strip()

    csv_path = _first_existing([
        target_results_dir(job_id) / "3DSASAmapped.csv",
        target_results_dir(job_id) / "Ligand_3D_Atoms_with_SASA.csv",
        target_results_dir(job_id) / "3DSASA_MASTER.csv",
    ])
    if not csv_path:
        return jsonify([]), 200

    out = []
    with csv_path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("pdb_id", "").lower().strip() != pdb:
                continue
            if row.get("Chain", "").upper().strip() != chain:
                continue
            if row.get("Warhead", "").upper().strip() != warhead:
                continue

            ai = row.get("AtomIndex") or row.get("atom_index") or row.get("atomindex") or ""
            exp = row.get("Exposure_A2") or row.get("exposure") or ""
            try:
                ai = int(float(ai))
                exp = float(exp)
            except Exception:
                continue

            out.append({"atomIndex": ai, "exposure": exp})

    return jsonify(out), 200




# ============================================================
# ACTIVE PROTAC BUILDER SESSION BRIDGE
# Stores the latest converted ligase session so warhead pages
# can send SMILES into the same Builder session.
# ============================================================

import os
import json
from datetime import datetime, timezone
from flask import request, jsonify

ACTIVE_BUILDER_SESSION_FILE = os.path.join(
    "static",
    "data",
    "active_protac_builder_session.json"
)

def _load_active_builder_sessions():
    try:
        if os.path.exists(ACTIVE_BUILDER_SESSION_FILE):
            with open(ACTIVE_BUILDER_SESSION_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print("⚠️ Could not load active builder sessions:", e)

    return {}

def _save_active_builder_sessions(data):
    os.makedirs(os.path.dirname(ACTIVE_BUILDER_SESSION_FILE), exist_ok=True)

    with open(ACTIVE_BUILDER_SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/api/protac-builder/active-session", methods=["GET", "POST"])
def active_protac_builder_session():
    data = _load_active_builder_sessions()

    # Use job_id if provided, otherwise global fallback.
    # This lets you keep per-job sessions but still have a last-used session.
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}

        session_id = str(payload.get("session_id") or "").strip()
        job_id = str(payload.get("job_id") or "global").strip() or "global"
        source = str(payload.get("source") or "").strip()

        if not session_id:
            return jsonify({
                "ok": False,
                "error": "Missing session_id"
            }), 400

        record = {
            "session_id": session_id,
            "job_id": job_id,
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        data[job_id] = record
        data["global"] = record

        _save_active_builder_sessions(data)

        print("💾 Active PROTAC Builder session saved:", record)

        return jsonify({
            "ok": True,
            **record
        })

    # GET
    job_id = str(request.args.get("job_id") or "global").strip() or "global"

    record = data.get(job_id) or data.get("global")

    if not record:
        return jsonify({
            "ok": False,
            "session_id": "",
            "error": "No active PROTAC Builder session"
        }), 404

    return jsonify({
        "ok": True,
        **record
    })

# -----------------------------
#      ERROR HANDLING
# -----------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", message="Page not found."), 404


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5070)





