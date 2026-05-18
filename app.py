#!/usr/bin/env python3
# PROTAC Target Module Web Server
# by Joseph-Michael Schulz

from __future__ import annotations

import os
import re
import json
import csv
import io
import zipfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from urllib.parse import quote
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

# API docs / manifest config
PRIMARY_API_BASE = os.getenv("WARHEAD_API_PRIMARY_BASE", "http://cartman.rove-vernier.ts.net").rstrip("/")
SECONDARY_API_BASE = os.getenv("WARHEAD_API_SECONDARY_BASE", "https://warheadhunter.com").rstrip("/")
API_VERSION = "0.1"
APP_ENVIRONMENT = (
    os.getenv("WARHEAD_ENVIRONMENT")
    or os.getenv("FLASK_ENV")
    or "development"
)

app.config["PRIMARY_API_BASE"] = PRIMARY_API_BASE
app.config["SECONDARY_API_BASE"] = SECONDARY_API_BASE
app.config["API_VERSION"] = API_VERSION
app.config["APP_ENVIRONMENT"] = APP_ENVIRONMENT

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

API_BASE_OPTIONS = [
    {
        "label": "Internal / Tailscale",
        "key": "internal",
        "value": PRIMARY_API_BASE,
        "description": "Use this first for current internal and Tailscale-based development.",
    },
    {
        "label": "Public / Future Production",
        "key": "public",
        "value": SECONDARY_API_BASE,
        "description": "Use this after the public deployment becomes the preferred default.",
    },
]

API_DOC_CURRENT_GROUPS = [
    {
        "title": "Health And Discovery",
        "description": "Lightweight service metadata and discovery routes.",
        "routes": [
            {
                "method": "GET",
                "path": "/api/health",
                "note": "Health and environment status.",
            },
            {
                "method": "GET",
                "path": "/api/manifest",
                "note": "Implemented endpoint groups, base URLs, and roadmap summary.",
            },
        ],
    },
    {
        "title": "Job Monitoring And Export",
        "description": "Current job-state, summary, and archive retrieval.",
        "routes": [
            {
                "method": "GET",
                "path": "/api/job_log/<job_id>",
                "note": "Returns current in-memory job log/status for active jobs.",
            },
            {
                "method": "GET",
                "path": "/api/job_summary/<job_id>",
                "note": "Returns a lightweight per-job summary.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/download",
                "note": "Downloads a ZIP bundle for the job, preferring a curated public results bundle when present.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/export",
                "note": "Exports the job index as CSV.",
            },
        ],
    },
    {
        "title": "Programmatic Job API",
        "description": "Single-job submission, status retrieval, result manifest access, file listing, and bundle download.",
        "routes": [
            {
                "method": "POST",
                "path": "/api/jobs",
                "note": "Submit a single Warhead Hunter job using the current compatible input model.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>",
                "note": "Read job metadata and status from durable job metadata plus live in-memory state when available.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/results",
                "note": "Return a lightweight result manifest for job-derived outputs.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/files",
                "note": "List safe job files with download URLs.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/files/<filename>",
                "note": "Download one safe file from the job directory.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/bundle",
                "note": "Download a ZIP bundle of safe result files for the job, preferring a curated public results bundle when present.",
            },
        ],
    },
    {
        "title": "Structure And Visualization Assets",
        "description": "Routes that serve SVG, PDB, protein-only PDB, SDF, and related structure assets.",
        "routes": [
            {
                "method": "GET",
                "path": "/api/svg/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
            {
                "method": "GET",
                "path": "/api/svg-plain/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
            {
                "method": "GET",
                "path": "/api/pdb/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
            {
                "method": "GET",
                "path": "/api/protein/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
            {
                "method": "GET",
                "path": "/api/sdf/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
        ],
    },
    {
        "title": "Ligand And Result Helpers",
        "description": "Routes that help the browser resolve ligand properties, chains, and mapped atom data.",
        "routes": [
            {
                "method": "GET",
                "path": "/api/ligand_props/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
            {
                "method": "GET",
                "path": "/api/ligand_chain/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
            {
                "method": "GET",
                "path": "/api/sasa_overlay/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
            {
                "method": "GET",
                "path": "/api/sasa_atommap/...",
                "note": "Implemented route family — exact path arguments should be confirmed from code.",
            },
        ],
    },
    {
        "title": "SASA Blueprint Endpoints",
        "description": "Programmatic atom-level solvent-exposure retrieval for job outputs.",
        "routes": [
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/sasa/available",
                "note": "Lists available chain and residue combinations for a PDB.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/sasa/atoms",
                "note": "Returns atom-level SASA payloads for one ligand occurrence.",
            },
            {
                "method": "POST",
                "path": "/api/jobs/<job_id>/sasa/bulk",
                "note": "Bulk atom-level SASA lookup for multiple requests.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/sasa/residue_for_ligand",
                "note": "Resolves residue ID from PDB, chain, and ligand code.",
            },
        ],
    },
    {
        "title": "Curated Example Jobs",
        "description": "Read-only curated examples for exploring completed Warhead Hunter outputs and prepared ligand-bound structures.",
        "routes": [
            {
                "method": "GET",
                "path": "/api/examples",
                "note": "Lists curated completed example jobs and availability metadata.",
            },
            {
                "method": "GET",
                "path": "/api/examples/<job_id>",
                "note": "Returns metadata for one curated example job.",
            },
            {
                "method": "GET",
                "path": "/api/examples/<job_id>/files",
                "note": "Lists safe downloadable files for one curated example job.",
            },
            {
                "method": "GET",
                "path": "/api/examples/<job_id>/files/<filename>",
                "note": "Downloads one safe file from a curated example job.",
            },
            {
                "method": "GET",
                "path": "/api/examples/<job_id>/bundle",
                "note": "Downloads a ZIP bundle of safe result files for a curated example job, preferring a curated public results bundle when present.",
            },
        ],
    },
    {
        "title": "Indexed Jobs",
        "description": "Read-only job index aligned with the Past Jobs Browser.",
        "routes": [
            {
                "method": "GET",
                "path": "/api/indexed-jobs",
                "note": "Lists indexed jobs with optional filters such as protein, query, available, and limit.",
            },
        ],
    },
]

API_DOC_PLANNED_ENDPOINTS = [
    "POST /api/jobs",
    "GET /api/jobs/{job_id}",
    "GET /api/jobs/{job_id}/results",
    "GET /api/jobs/{job_id}/files",
    "GET /api/jobs/{job_id}/files/{filename}",
    "GET /api/jobs/{job_id}/bundle",
    "POST /api/batches",
    "GET /api/batches/{batch_id}",
    "GET /api/batches/{batch_id}/results",
]

COMPANION_TOOL_LINKS = [
    {"name": "PROTAC Builder", "url": "https://protacbuilder.com"},
    {"name": "E3 Ligandalyzer", "url": "https://e3ligandalyzer.com"},
    {"name": "V-LiSEMOD", "url": "https://vlisemod.com"},
]

CURATED_EXAMPLE_CONFIG = [
    {
        "job_id": "b281996d",
        "label": "CRBN / cereblon",
        "protein": "CRBN",
        "use_case": "E3 ligase recruiter / PROTAC-oriented example",
    },
    {
        "job_id": "032917e1",
        "label": "EGFR",
        "protein": "EGFR",
        "use_case": "kinase target example",
    },
    {
        "job_id": "d750cfea",
        "label": "HIV-1 Protease",
        "protein": "Protease",
        "use_case": "viral enzyme / ligand-bound structure example",
    },
    {
        "job_id": "d6706e03",
        "label": "DYRK1A",
        "protein": "DYRK1A",
        "use_case": "kinase inhibitor structure example",
    },
]

DEFAULT_CURATED_EXAMPLE_JOB_ID = CURATED_EXAMPLE_CONFIG[0]["job_id"]

SAFE_RESULT_SUFFIXES = {
    ".pdb", ".sdf", ".svg", ".csv", ".tsv", ".html", ".htm", ".json", ".log", ".txt"
}
SAFE_SKIP_NAMES = {".ds_store"}
SAFE_SKIP_DIRS = {".git", "__pycache__"}

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
    return {"current_year": datetime.now().year}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_json(payload: Dict[str, Any]):
    resp = jsonify(payload)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _api_error(code: str, message: str, status: int = 400, details: Optional[Dict[str, Any]] = None):
    resp = _api_json({
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    })
    return resp, status


def _job_metadata_path(job_id: str) -> Path:
    return job_root(job_id) / "job_metadata.json"


def _read_job_metadata(job_id: str) -> Optional[Dict[str, Any]]:
    fp = _job_metadata_path(job_id)
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_job_metadata_local(job_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    fp = _job_metadata_path(job_id)
    fp.parent.mkdir(parents=True, exist_ok=True)
    data = _read_job_metadata(job_id) or {}
    data.update(patch or {})
    data["job_id"] = job_id
    data["updated_at"] = _utc_now_iso()
    data.setdefault("outputs", {})
    data.setdefault("error", None)
    fp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return data


def _fetch_fasta_for_pdb(pdb_id: str) -> Optional[str]:
    pdb = str(pdb_id or "").strip().upper()
    if len(pdb) != 4:
        return None

    sources = [
        f"https://files.rcsb.org/download/{pdb}.fasta",
        f"https://www.ebi.ac.uk/pdbe/api/pdb/entry/molecules/{pdb.lower()}",
    ]

    for url in sources:
        try:
            resp = requests.get(url, timeout=10, verify=False)
            if resp.status_code != 200:
                continue
            text = resp.text or ""
            if not text:
                continue

            if url.endswith(".fasta"):
                if "<html" in text.lower() or "<!doctype" in text.lower():
                    continue
                return text.strip()

            # PDBe fallback JSON -> concatenate polymer sequences into one FASTA-like blob
            data = resp.json()
            entries = data.get(pdb.lower()) or data.get(pdb.upper()) or []
            seqs = []
            for entity in entries:
                seq = str(entity.get("sequence", "") or "").strip()
                if seq:
                    entity_id = entity.get("entity_id", "?")
                    seqs.append(f">{pdb}_{entity_id}\n{seq}")
            if seqs:
                return "\n".join(seqs)
        except Exception:
            continue
    return None


def _build_api_job_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    pdb_id = str(payload.get("pdb_id") or "").strip().upper()
    ligand = str(payload.get("ligand") or "").strip().upper()
    target_name = str(payload.get("target_name") or "").strip()
    search_query = str(payload.get("search_query") or "").strip()
    fasta_seq = str(payload.get("fasta_seq") or "").strip()
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}

    if target_name or search_query or fasta_seq:
        if not target_name:
            return {"ok": False, "error": ("MISSING_REQUIRED_FIELD", "Field 'target_name' is required when using direct pipeline-compatible submission.", {"field": "target_name"})}
        if not search_query:
            return {"ok": False, "error": ("MISSING_REQUIRED_FIELD", "Field 'search_query' is required when using direct pipeline-compatible submission.", {"field": "search_query"})}
        if not fasta_seq:
            return {"ok": False, "error": ("MISSING_REQUIRED_FIELD", "Field 'fasta_seq' is required for API submission because the current pipeline expects sequence-backed filtering.", {"field": "fasta_seq"})}

        return {
            "ok": True,
            "target_name": target_name,
            "search_query": search_query,
            "fasta_seq": fasta_seq,
            "request_payload": {
                "target_name": target_name,
                "search_query": search_query,
                "fasta_seq": fasta_seq,
                "pdb_id": pdb_id,
                "ligand": ligand,
                "options": options,
                "submission_mode": "pipeline-compatible",
            },
        }

    if not pdb_id:
        return {"ok": False, "error": ("MISSING_REQUIRED_FIELD", "Provide either 'pdb_id' or the direct pipeline-compatible fields 'target_name', 'search_query', and 'fasta_seq'.", {"required_any_of": ["pdb_id", "target_name/search_query/fasta_seq"]})}

    fasta_from_pdb = _fetch_fasta_for_pdb(pdb_id)
    if not fasta_from_pdb:
        return {"ok": False, "error": ("PIPELINE_START_FAILED", f"Could not resolve FASTA for pdb_id '{pdb_id}'. Provide 'target_name', 'search_query', and 'fasta_seq' explicitly for API submission.", {"pdb_id": pdb_id})}

    derived_target = target_name or pdb_id
    derived_query = search_query or pdb_id

    return {
        "ok": True,
        "target_name": derived_target,
        "search_query": derived_query,
        "fasta_seq": fasta_from_pdb,
        "request_payload": {
            "pdb_id": pdb_id,
            "ligand": ligand,
            "options": options,
            "target_name": derived_target,
            "search_query": derived_query,
            "fasta_seq": fasta_from_pdb,
            "submission_mode": "pdb-id-compatible",
            "notes": [
                "The current pipeline is target/query/FASTA-oriented.",
                "For API compatibility, search_query is derived from pdb_id and FASTA is resolved before launch.",
                "Ligand is preserved in request metadata but is not yet a first-class pipeline selector.",
            ],
        },
    }


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
#        ROUTES (API DOCS)
# -----------------------------
@app.get("/api-docs")
@app.get("/api")
def api_docs():
    return render_template(
        "api_docs.html",
        api_version=API_VERSION,
        environment=APP_ENVIRONMENT,
        api_base_options=API_BASE_OPTIONS,
        api_doc_current_groups=API_DOC_CURRENT_GROUPS,
        api_doc_planned_endpoints=API_DOC_PLANNED_ENDPOINTS,
        companion_tool_links=COMPANION_TOOL_LINKS,
        primary_api_base=PRIMARY_API_BASE,
        secondary_api_base=SECONDARY_API_BASE,
        curated_examples=get_curated_examples(),
        preferred_example_job_id=DEFAULT_CURATED_EXAMPLE_JOB_ID,
        batch_preview_payload={
            "jobs": [
                {
                    "pdb_id": "4EIY",
                    "ligand": "ABC",
                    "options": {
                        "run_sasa": True,
                        "generate_svg": True,
                        "generate_viewer": True,
                    },
                }
            ]
        },
    )


@app.get("/api/health")
def api_health():
    return _api_json({
        "ok": True,
        "service": "warhead-hunter",
        "status": "healthy",
        "api_version": API_VERSION,
        "environment": APP_ENVIRONMENT,
        "time": _utc_now_iso(),
    })


@app.get("/api/manifest")
def api_manifest():
    return _api_json({
        "ok": True,
        "service": "warhead-hunter",
        "api_version": API_VERSION,
        "environment": APP_ENVIRONMENT,
        "time": _utc_now_iso(),
        "base_urls": {
            "primary": PRIMARY_API_BASE,
            "secondary": SECONDARY_API_BASE,
        },
        "current_implemented_endpoint_groups": [
            {
                "title": group["title"],
                "description": group["description"],
                "routes": group["routes"],
            }
            for group in API_DOC_CURRENT_GROUPS
        ],
        "future_planned_endpoint_groups": {
            "planned_endpoints": API_DOC_PLANNED_ENDPOINTS,
            "warning": "Batch endpoints and structured job-submission endpoints are planned but not yet implemented.",
        },
        "implemented_groups": {
            "job_api": {
                "status": "implemented",
                "description": "Single-job submission, status retrieval, result manifest access, file listing, and bundle download.",
                "endpoints": [
                    "POST /api/jobs",
                    "GET /api/jobs/<job_id>",
                    "GET /api/jobs/<job_id>/results",
                    "GET /api/jobs/<job_id>/files",
                    "GET /api/jobs/<job_id>/files/<filename>",
                    "GET /api/jobs/<job_id>/bundle",
                ],
            },
            "example_jobs": {
                "status": "implemented",
                "description": "Read-only curated examples for exploring completed Warhead Hunter outputs.",
                "endpoints": [
                    "GET /api/examples",
                    "GET /api/examples/<job_id>",
                    "GET /api/examples/<job_id>/files",
                    "GET /api/examples/<job_id>/files/<filename>",
                    "GET /api/examples/<job_id>/bundle",
                ],
            },
            "indexed_jobs": {
                "status": "implemented",
                "description": "Read-only indexed job listing aligned with the Past Jobs Browser.",
                "endpoints": [
                    "GET /api/indexed-jobs",
                ],
            },
        },
        "companion_tool_links": COMPANION_TOOL_LINKS,
        "warning": "Use the internal/Tailscale base first for current development. Public production may become the preferred default later.",
    })


@app.post("/api/jobs")
def api_submit_job():
    payload = request.get_json(silent=True)
    if payload is None:
        return _api_error("INVALID_JSON", "Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _api_error("INVALID_JSON", "Top-level JSON body must be an object.", 400)

    unsupported_keys = [k for k in ("structure_path", "structure_file", "input_path", "upload_path") if k in payload]
    if unsupported_keys:
        return _api_error(
            "MISSING_REQUIRED_FIELD",
            "Direct structure-path submission is not supported by the current pipeline. Use 'pdb_id' or provide 'target_name', 'search_query', and 'fasta_seq'.",
            400,
            {"unsupported_fields": unsupported_keys},
        )

    built = _build_api_job_request(payload)
    if not built.get("ok"):
        code, message, details = built["error"]
        status = 400 if code in {"INVALID_JSON", "MISSING_REQUIRED_FIELD"} else 422
        return _api_error(code, message, status, details)

    try:
        job_id = start_job(
            built["target_name"],
            built["search_query"],
            built["fasta_seq"],
            source="api",
            request_payload=built["request_payload"],
        )
    except Exception as e:
        return _api_error(
            "PIPELINE_START_FAILED",
            "The pipeline could not be started.",
            500,
            {"message": str(e)},
        )

    meta = get_job_api_metadata(job_id) or {}
    meta["source"] = "api"
    _write_job_metadata_local(job_id, meta)

    return _api_json({
        "ok": True,
        "job_id": job_id,
        "status": meta.get("status", "queued"),
        "status_url": f"/api/jobs/{job_id}",
        "results_url": f"/api/jobs/{job_id}/results",
        "files_url": f"/api/jobs/{job_id}/files",
        "bundle_url": f"/api/jobs/{job_id}/bundle",
        "notes": built["request_payload"].get("notes", []),
    })


@app.get("/api/jobs/<job_id>")
def api_job_status(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    meta = get_job_api_metadata(job_id)
    if meta is None:
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    _write_job_metadata_local(job_id, meta)
    return _api_json({
        "ok": True,
        "job": meta,
    })


@app.get("/api/jobs/<job_id>/results")
def api_job_results(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    meta = get_job_api_metadata(job_id)
    if meta is None:
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    manifest = build_job_results_manifest(job_id)
    status_value = str(meta.get("status") or "unknown").lower()
    if not manifest["files"] and status_value in {"queued", "running", "pending", "unknown"}:
        resp = _api_json({
            "ok": True,
            "job_id": job_id,
            "status": meta.get("status", "unknown"),
            "message": "Results are not ready yet.",
        })
        return resp, 202

    meta["outputs"]["results_manifest_available"] = True
    _write_job_metadata_local(job_id, meta)
    return _api_json({
        "ok": True,
        "job_id": job_id,
        "status": meta.get("status", "unknown"),
        "results": manifest,
    })


@app.get("/api/jobs/<job_id>/files")
def api_job_files(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    kind = (request.args.get("kind") or "all").strip().lower()
    if kind not in {"all", "pdb", "sdf", "svg", "csv", "html", "other"}:
        return _api_error("INVALID_PATH", f"Unsupported kind filter: {kind}", 400, {"kind": kind})

    files = list_safe_job_files(job_id, kind=kind, namespace="jobs")
    return _api_json({
        "ok": True,
        "job_id": job_id,
        "files": files,
    })


@app.get("/api/jobs/<job_id>/files/<path:filename>")
def api_job_file_download(job_id, filename):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    rel = str(filename or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return _api_error("INVALID_PATH", "Requested file path is invalid.", 400)

    fp = (base / rel).resolve()
    if not _is_safe_job_file(base, fp):
        return _api_error("FILE_NOT_FOUND", "Requested file was not found.", 404)
    if not fp.exists():
        return _api_error("FILE_NOT_FOUND", "Requested file was not found.", 404)

    return send_file(fp, as_attachment=True, download_name=fp.name)


@app.get("/api/jobs/<job_id>/bundle")
def api_job_bundle(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    public_bundle = get_preferred_public_bundle(job_id)
    if public_bundle:
        return send_file(
            public_bundle,
            mimetype="application/zip",
            as_attachment=True,
            download_name=public_bundle.name,
        )

    mem = create_safe_job_zip(job_id, mode="example")
    if mem is None:
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{job_id}_warhead_hunter_results.zip",
    )


@app.post("/api/batches")
def api_batches_stub():
    return _api_error(
        "NOT_IMPLEMENTED",
        "Batch API is planned but not implemented yet. Use POST /api/jobs for single-job submission.",
        501,
    )


@app.get("/api/examples")
def api_examples():
    return _api_json({
        "ok": True,
        "service": "warhead-hunter",
        "examples": get_curated_examples(),
    })


@app.get("/api/examples/<job_id>")
def api_example_detail(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_json({
            "ok": False,
            "error": f"Curated example not found: {job_id}",
        }), 404

    entry = build_curated_example_entry(item)
    if entry["available"]:
        entry["files_count"] = len(list_safe_job_files(job_id, kind="all"))
    return _api_json({
        "ok": True,
        "service": "warhead-hunter",
        "example": entry,
    })


@app.get("/api/examples/<job_id>/files")
def api_example_files(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_json({"ok": False, "error": f"Curated example not found: {job_id}"}), 404

    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_json({"ok": False, "error": f"Curated example job is not available on this deployment: {job_id}"}), 404

    kind = (request.args.get("kind") or "all").strip().lower()
    if kind not in {"all", "pdb", "sdf", "svg", "csv", "html", "other"}:
        return _api_json({"ok": False, "error": f"Unsupported kind filter: {kind}"}), 400

    files = list_safe_job_files(job_id, kind=kind)
    return _api_json({
        "ok": True,
        "service": "warhead-hunter",
        "job_id": job_id,
        "kind": kind,
        "count": len(files),
        "files": files,
    })


@app.get("/api/examples/<job_id>/files/<path:filename>")
def api_example_file_download(job_id, filename):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_json({"ok": False, "error": f"Curated example not found: {job_id}"}), 404

    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_json({"ok": False, "error": f"Curated example job is not available on this deployment: {job_id}"}), 404

    rel = str(filename or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return _api_json({"ok": False, "error": "Invalid filename"}), 400

    fp = (base / rel).resolve()
    if not _is_safe_job_file(base, fp):
        return _api_json({"ok": False, "error": "File not found or not allowed"}), 404
    if not fp.exists():
        return _api_json({"ok": False, "error": "File not found"}), 404

    return send_file(fp, as_attachment=True, download_name=fp.name)


@app.get("/api/examples/<job_id>/bundle")
def api_example_bundle(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_json({"ok": False, "error": f"Curated example not found: {job_id}"}), 404

    public_bundle = get_preferred_public_bundle(job_id)
    if public_bundle:
        return send_file(
            public_bundle,
            mimetype="application/zip",
            as_attachment=True,
            download_name=public_bundle.name,
        )

    mem = create_safe_job_zip(job_id, mode="example")
    if mem is None:
        return _api_json({"ok": False, "error": f"Curated example job is not available on this deployment: {job_id}"}), 404

    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{job_id}_warhead_hunter_example_results.zip",
    )


@app.get("/api/indexed-jobs")
def api_indexed_jobs():
    protein_filter = (request.args.get("protein") or "").strip().lower()
    query_filter = (request.args.get("query") or "").strip().lower()
    available_filter = (request.args.get("available") or "").strip().lower()
    limit_raw = (request.args.get("limit") or "50").strip()

    try:
        limit = max(1, min(int(limit_raw), 500))
    except Exception:
        limit = 50

    jobs = load_indexed_jobs()

    if protein_filter:
        jobs = [j for j in jobs if protein_filter in (j.get("protein", "").lower())]
    if query_filter:
        jobs = [j for j in jobs if query_filter in (j.get("search_query", "").lower())]
    if available_filter in {"true", "false"}:
        want = available_filter == "true"
        jobs = [j for j in jobs if bool(j.get("has_results")) == want]

    jobs = jobs[:limit]
    job_summaries = [
        {
            "job_id": j.get("job_id", ""),
            "protein": j.get("protein", ""),
            "search_query": j.get("search_query", ""),
            "fasta_len": j.get("fasta_len", 0),
            "has_results": bool(j.get("has_results")),
            "mtime": j.get("mtime", ""),
        }
        for j in jobs
    ]
    return _api_json({
        "ok": True,
        "count": len(job_summaries),
        "jobs": job_summaries,
    })


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


def get_jobs_root() -> Path:
    return Path(app.config["JOBS_DIR"])


def safe_job_dir(job_id: str) -> Optional[Path]:
    if not _safe_job_id(job_id):
        return None
    p = (get_jobs_root() / job_id).resolve()
    try:
        root = get_jobs_root().resolve()
        if not str(p).startswith(str(root)):
            return None
    except Exception:
        return None
    return p


def _fasta_length(fasta: str) -> int:
    if not fasta:
        return 0
    seq = "\n".join([ln for ln in fasta.splitlines() if not ln.strip().startswith(">")])
    seq = re.sub(r"[^A-Za-z]", "", seq)
    return len(seq)


def _job_meta_from_dir(job_dir: Path) -> Dict[str, Any]:
    meta = _read_protein_data_csv(job_dir) or {}
    fasta = (meta.get("fasta") or "").strip()

    try:
        mtime = datetime.fromtimestamp(job_dir.stat().st_mtime)
        mtime_s = mtime.strftime("%Y-%m-%d %H:%M")
    except Exception:
        mtime_s = ""

    return {
        "job_id": job_dir.name,
        "protein": (meta.get("protein") or "").strip(),
        "search_query": (meta.get("search_query") or "").strip(),
        "fasta": fasta,
        "fasta_len": _fasta_length(fasta),
        "has_results": _job_has_results(job_dir),
        "mtime": mtime_s,
    }


def load_indexed_jobs() -> List[Dict[str, Any]]:
    jobs_root = get_jobs_root()
    jobs: List[Dict[str, Any]] = []
    if not jobs_root.exists():
        return jobs

    for d in sorted(jobs_root.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        jid = d.name
        if not _safe_job_id(jid):
            continue
        jobs.append(_job_meta_from_dir(d))

    jobs.sort(key=lambda x: x.get("mtime", ""), reverse=True)
    return jobs


def classify_file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdb":
        return "pdb"
    if suffix == ".sdf":
        return "sdf"
    if suffix == ".svg":
        return "svg"
    if suffix in {".csv", ".tsv"}:
        return "csv"
    if suffix in {".html", ".htm"}:
        return "html"
    return "other"


def _is_safe_job_file(base: Path, fp: Path) -> bool:
    try:
        base_r = base.resolve()
        fp_r = fp.resolve()
    except Exception:
        return False

    if not str(fp_r).startswith(str(base_r)):
        return False
    if not fp_r.is_file():
        return False

    rel_parts = fp_r.relative_to(base_r).parts
    if not rel_parts:
        return False
    if any(part.startswith(".") for part in rel_parts):
        return False
    if any(part in SAFE_SKIP_DIRS for part in rel_parts):
        return False
    if fp_r.name.lower() in SAFE_SKIP_NAMES:
        return False
    if fp_r.suffix.lower() not in SAFE_RESULT_SUFFIXES:
        return False
    return True


def build_file_download_url(job_id: str, relative_path: str, namespace: str = "examples") -> str:
    rel = quote(relative_path.replace("\\", "/").lstrip("/"), safe="/")
    if namespace == "examples":
        return f"/api/examples/{job_id}/files/{rel}"
    return f"/api/jobs/{job_id}/files/{rel}"


def list_safe_job_files(job_id: str, kind: Optional[str] = None, namespace: str = "examples") -> List[Dict[str, Any]]:
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return []

    kind_norm = (kind or "all").strip().lower()
    files: List[Dict[str, Any]] = []

    for fp in base.rglob("*"):
        if not _is_safe_job_file(base, fp):
            continue

        file_kind = classify_file_kind(fp)
        if kind_norm not in {"", "all"} and file_kind != kind_norm:
            continue

        rel = fp.relative_to(base).as_posix()
        try:
            size_bytes = fp.stat().st_size
            modified_at = datetime.fromtimestamp(fp.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            size_bytes = 0
            modified_at = ""

        files.append({
            "name": fp.name,
            "relative_path": rel,
            "kind": file_kind,
            "size_bytes": size_bytes,
            "modified_at": modified_at,
            "download_url": build_file_download_url(job_id, rel, namespace=namespace),
        })

    files.sort(key=lambda x: (x["kind"], x["relative_path"]))
    return files


def create_safe_job_zip(job_id: str, mode: str = "example") -> Optional[io.BytesIO]:
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return None

    if mode == "example":
        safe_files = list_safe_job_files(job_id, kind="all")
        rel_paths = [item["relative_path"] for item in safe_files]
    else:
        rel_paths = []
        for fp in base.rglob("*"):
            if fp.is_file():
                rel_paths.append(fp.relative_to(base).as_posix())

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in rel_paths:
            fp = (base / rel).resolve()
            if mode == "example" and not _is_safe_job_file(base, fp):
                continue
            if not fp.exists() or not fp.is_file():
                continue
            z.write(fp, arcname=str(Path(job_id) / rel))

    mem.seek(0)
    return mem


def get_preferred_public_bundle(job_id: str) -> Optional[Path]:
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return None

    candidate = (base / "bundles" / f"{job_id}_warhead_hunter_public_results.zip").resolve()
    try:
        if not str(candidate).startswith(str(base.resolve())):
            return None
    except Exception:
        return None

    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def build_job_results_manifest(job_id: str) -> Dict[str, Any]:
    files = list_safe_job_files(job_id, kind="all", namespace="jobs")
    counts: Dict[str, int] = {}
    for item in files:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    meta = _read_job_metadata(job_id) or {}
    public_bundle = get_preferred_public_bundle(job_id)
    job_manifest = job_root(job_id) / "job_result_manifest.json"
    cleanup_report = job_root(job_id) / "cleanup_report.md"
    summary = {
        "file_count": len(files),
        "counts_by_kind": counts,
        "has_target_results_dir": bool((job_root(job_id) / "TARGET_RESULTS").exists()),
        "has_results_display_csv": bool((target_results_dir(job_id) / "Results_Display.csv").exists() or (job_root(job_id) / "Results_Display.csv").exists()),
        "has_resolved_sasa_summary": bool(_first_existing([
            job_root(job_id) / "Resolved_SASA_Summary.csv",
            job_root(job_id) / "Resolved_SASA_Summary.tsv",
            target_results_dir(job_id) / "Resolved_SASA_Summary.csv",
            target_results_dir(job_id) / "Resolved_SASA_Summary.tsv",
        ])),
        "status_from_metadata": meta.get("status", ""),
        "has_curated_public_bundle": bool(public_bundle),
        "public_bundle_path": public_bundle.relative_to(job_root(job_id)).as_posix() if public_bundle else "",
        "has_job_result_manifest": bool(job_manifest.exists()),
        "has_cleanup_report": bool(cleanup_report.exists()),
    }
    return {
        "summary": summary,
        "files": files,
    }


def get_job_api_metadata(job_id: str) -> Optional[Dict[str, Any]]:
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return None

    data = _read_job_metadata(job_id) or {}
    live = JOB_STORE.get(job_id, {})
    protein_meta = _read_protein_data_csv(base) or {}

    if not data:
        data = {
            "job_id": job_id,
            "status": live.get("status") or ("completed" if _job_has_results(base) else "unknown"),
            "created_at": live.get("created_at") or "",
            "updated_at": _utc_now_iso(),
            "source": "web",
            "request": {
                "target_name": (protein_meta.get("protein") or "").strip(),
                "search_query": (protein_meta.get("search_query") or "").strip(),
                "fasta_seq": (protein_meta.get("fasta") or "").strip(),
            },
            "outputs": {},
            "error": None,
        }

    if live:
        data["status"] = live.get("status", data.get("status", "unknown"))
        data["created_at"] = data.get("created_at") or live.get("created_at", "")
        data["started_at"] = live.get("started_at", data.get("started_at", ""))
        data["finished_at"] = live.get("finished_at", data.get("finished_at", ""))
        data["current_step"] = live.get("current_step", data.get("current_step", ""))
        data["step_started_at"] = live.get("step_started_at", data.get("step_started_at", ""))

    public_bundle = get_preferred_public_bundle(job_id)
    outputs = dict(data.get("outputs") or {})
    outputs.update({
        "job_dir": str(base),
        "has_results": _job_has_results(base),
        "results_url": f"/api/jobs/{job_id}/results",
        "files_url": f"/api/jobs/{job_id}/files",
        "bundle_url": f"/api/jobs/{job_id}/bundle",
        "legacy_download_url": f"/api/jobs/{job_id}/download",
        "public_bundle_path": public_bundle.relative_to(base).as_posix() if public_bundle else outputs.get("public_bundle_path", ""),
    })
    data["outputs"] = outputs
    data["updated_at"] = _utc_now_iso()
    return data


def get_curated_example_by_id(job_id: str) -> Optional[Dict[str, Any]]:
    for item in CURATED_EXAMPLE_CONFIG:
        if item["job_id"] == job_id:
            return dict(item)
    return None


def build_curated_example_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    job_id = item["job_id"]
    base = safe_job_dir(job_id)
    available = bool(base and base.exists())
    meta = _job_meta_from_dir(base) if available else {}

    return {
        "job_id": job_id,
        "label": item.get("label", job_id),
        "protein": (meta.get("protein") or item.get("protein") or "").strip(),
        "search_query": (meta.get("search_query") or "").strip(),
        "use_case": item.get("use_case", ""),
        "available": available,
        "has_results": bool(meta.get("has_results")) if available else False,
        "job_url": f"/api/examples/{job_id}",
        "files_url": f"/api/examples/{job_id}/files",
        "bundle_url": f"/api/examples/{job_id}/bundle",
        "browser_url": f"/open_job/{job_id}" if available else "",
        "api_curl": f'curl -s "$BASE/api/examples/{job_id}" | python -m json.tool',
    }


def get_curated_examples() -> List[Dict[str, Any]]:
    return [build_curated_example_entry(item) for item in CURATED_EXAMPLE_CONFIG]



def ligand_sdf_dir(job_id: str) -> Optional[Path]:
    return _first_existing([
        target_results_dir(job_id) / "LIGAND_SDF",
        job_root(job_id) / "TARGET_RESULTS" / "LIGAND_SDF",
        job_root(job_id) / "LIGAND_SDF",
    ])



@app.route("/browse")
def browse():
    return render_template("browse.html", jobs=load_indexed_jobs())


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

@app.get("/api/jobs/<job_id>/download")
def api_download_job(job_id):
    if not _safe_job_id(job_id):
        abort(400, "Invalid job_id")

    base = job_root(job_id)
    if not base.exists():
        abort(404, "Job not found")

    public_bundle = get_preferred_public_bundle(job_id)
    if public_bundle:
        return send_file(
            public_bundle,
            mimetype="application/zip",
            as_attachment=True,
            download_name=public_bundle.name,
        )

    mem = create_safe_job_zip(job_id, mode="job")
    if mem is None:
        abort(404, "Job not found")
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
    rows = []
    for meta in load_indexed_jobs():
        rows.append({
            "job_id": meta.get("job_id", ""),
            "protein": (meta.get("protein") or "").strip(),
            "search_query": (meta.get("search_query") or "").strip(),
            "has_results": "yes" if meta.get("has_results") else "no"
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
