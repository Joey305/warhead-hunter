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
import uuid
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
import job_state as disk_jobs
from api.sasa_api import bp as sasa_bp
from routes import bp as routes_bp
from api.handoff_server import hand_bp
from api.sdf_resolver import resolve_sdf_path

try:
    from api.randy_archive_client import (
        archive_enabled as randy_archive_enabled,
        find_asset as randy_find_asset,
        get_table_dataframe as randy_get_table_dataframe,
        proxy_file_response as randy_proxy_file_response,
        job_exists as randy_job_exists,
    )
except Exception:
    def randy_archive_enabled() -> bool:
        return False

    def randy_find_asset(*args, **kwargs):
        return None

    def randy_get_table_dataframe(*args, **kwargs):
        return None

    def randy_proxy_file_response(*args, **kwargs):
        return None

    def randy_job_exists(*args, **kwargs) -> bool:
        return False
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
BATCHES_DIR = JOBS_DIR / "_batches"
BATCHES_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = str(APP_ROOT / "uploads")
app.config["JOBS_DIR"] = str(JOBS_DIR)

# API docs / manifest config
PRIMARY_API_BASE = os.getenv("WARHEAD_API_PRIMARY_BASE", "http://cartman.rove-vernier.ts.net").rstrip("/")
SECONDARY_API_BASE = os.getenv("WARHEAD_API_SECONDARY_BASE", "https://warheadhunter.com").rstrip("/")
PUBLIC_SITE_BASE = os.getenv("WARHEAD_PUBLIC_SITE_BASE", "https://warheadhunter.com").rstrip("/")
PROTAC_BUILDER_BASE = os.getenv(
    "PROTAC_BUILDER_BASE",
    "https://protacbuilder.com/copy/COPYindex",
).rstrip("/")
API_VERSION = "0.1"
APP_ENVIRONMENT = (
    os.getenv("WARHEAD_ENVIRONMENT")
    or os.getenv("FLASK_ENV")
    or "development"
)

app.config["PRIMARY_API_BASE"] = PRIMARY_API_BASE
app.config["SECONDARY_API_BASE"] = SECONDARY_API_BASE
app.config["PUBLIC_SITE_BASE"] = PUBLIC_SITE_BASE
app.config["PROTAC_BUILDER_BASE"] = PROTAC_BUILDER_BASE
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
                "note": "Health, deployment metadata, and filesystem readiness status.",
            },
            {
                "method": "GET",
                "path": "/api/manifest",
                "note": "Implemented endpoint groups, base URLs, docs URLs, and companion links.",
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
                "note": "Submit a single Warhead Hunter job using target_name, search_query, and optional fasta_seq.",
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
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/war-pdbs",
                "note": "List cleaned ligand-bound PDB files under TARGET_RESULTS/WAR_PDB for one job.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/war-pdbs.zip",
                "note": "Download only the cleaned ligand-bound WAR_PDB files for one job.",
            },
            {
                "method": "GET",
                "path": "/api/jobs/<job_id>/artifacts",
                "note": "List classified job artifacts with kind and folder filters for automation workflows.",
            },
        ],
    },
    {
        "title": "Batch Submission",
        "description": "Launch multiple jobs sequentially with lightweight batch metadata and aggregated status endpoints.",
        "routes": [
            {
                "method": "POST",
                "path": "/api/batches",
                "note": "Submit multiple jobs using the same input model as POST /api/jobs.",
            },
            {
                "method": "GET",
                "path": "/api/batches/<batch_id>",
                "note": "Read batch metadata and live/computed status for each submitted job.",
            },
            {
                "method": "GET",
                "path": "/api/batches/<batch_id>/results",
                "note": "Return per-job result manifest summaries for a batch.",
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
                "note": "Returns metadata for one curated example job. Alias: /api/examples/<job_id>/metadata",
            },
            {
                "method": "GET",
                "path": "/api/examples/<job_id>/metadata",
                "note": "Backward-compatible metadata alias for one curated example job.",
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
            {
                "method": "GET",
                "path": "/api/examples/<job_id>/war-pdbs",
                "note": "List cleaned ligand-bound PDB files for one curated example job.",
            },
            {
                "method": "GET",
                "path": "/api/examples/<job_id>/war-pdbs.zip",
                "note": "Download only the cleaned ligand-bound WAR_PDB files for one curated example job.",
            },
            {
                "method": "GET",
                "path": "/api/examples/<job_id>/artifacts",
                "note": "List classified artifact files for one curated example job.",
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
                "note": "Lists indexed jobs with optional filters such as protein, target, query, availability, limit, offset, and sort.",
            },
        ],
    },
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
    return {
        "current_year": datetime.now().year,
        "site_base": app.config["PUBLIC_SITE_BASE"],
        "protac_builder_base": app.config["PROTAC_BUILDER_BASE"],
        "api_version": API_VERSION,
        "app_environment": APP_ENVIRONMENT,
    }



def compute_results_ready(job_id: str) -> Dict[str, Any]:
    df = load_results_display(job_id)
    has_rows = bool(df is not None and not df.empty)

    results_csv = _first_existing([
        target_results_dir(job_id) / "Results_Display.csv",
        job_root(job_id) / "Results_Display.csv",
    ])

    return {
        "has_results": has_rows,
        "results_ready": has_rows,
        "results_row_count": int(len(df.index)) if df is not None else 0,
        "results_csv": str(results_csv) if results_csv else "",
        "browser_results_url": f"/results/{job_id}",
        "results_url": f"/api/jobs/{job_id}/results",
        "files_url": f"/api/jobs/{job_id}/files",
        "bundle_url": f"/api/jobs/{job_id}/bundle",
    }




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


def _normalize_job_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload or {})

    alias_groups = {
        "target_name": ["target_name", "protein", "target"],
        "search_query": ["search_query", "query"],
        "fasta_seq": ["fasta_seq", "fasta", "sequence"],
    }

    for canonical, aliases in alias_groups.items():
        value = normalized.get(canonical)
        if value in (None, ""):
            for alias in aliases:
                if normalized.get(alias) not in (None, ""):
                    normalized[canonical] = normalized.get(alias)
                    break

    return normalized


def _batch_metadata_path(batch_id: str) -> Path:
    return BATCHES_DIR / f"{batch_id}.json"


def _read_batch_metadata(batch_id: str) -> Optional[Dict[str, Any]]:
    fp = _batch_metadata_path(batch_id)
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_batch_metadata(batch_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    fp = _batch_metadata_path(batch_id)
    payload = dict(data or {})
    payload["batch_id"] = batch_id
    payload["updated_at"] = _utc_now_iso()
    fp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _public_url(path: str) -> str:
    base = app.config["PUBLIC_SITE_BASE"].rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _job_metadata_path(job_id: str) -> Path:
    return job_root(job_id) / "job_metadata.json"


def _read_job_metadata(job_id: str) -> Optional[Dict[str, Any]]:
    return disk_jobs.load_job_metadata(job_id, JOBS_DIR)


def _write_job_metadata_local(job_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    return disk_jobs.write_job_metadata(job_id, patch or {}, JOBS_DIR)


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
    payload = _normalize_job_payload(payload)
    pdb_id = str(payload.get("pdb_id") or "").strip().upper()
    ligand = str(payload.get("ligand") or "").strip().upper()
    target_name = str(payload.get("target_name") or "").strip()
    search_query = str(payload.get("search_query") or "").strip()
    fasta_seq = str(payload.get("fasta_seq") or "").strip()
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}

    if target_name or search_query or fasta_seq:
        if not target_name:
            return {"ok": False, "error": ("MISSING_FIELD", "Field 'target_name' is required. Alias keys 'protein' and 'target' are also accepted.", {"field": "target_name"})}
        if not search_query:
            return {"ok": False, "error": ("MISSING_FIELD", "Field 'search_query' is required. Alias key 'query' is also accepted.", {"field": "search_query"})}

        notes: List[str] = []
        if not fasta_seq:
            notes.append("No FASTA sequence was provided. The current pipeline accepts this, but sequence-backed filtering may be less specific.")

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
                "notes": notes,
            },
        }

    if not pdb_id:
        return {"ok": False, "error": ("MISSING_FIELD", "Fields 'target_name' and 'search_query' are required. Alias keys 'protein', 'target', and 'query' are accepted.", {"required_fields": ["target_name", "search_query"]})}

    fasta_from_pdb = fasta_seq or _fetch_fasta_for_pdb(pdb_id) or ""
    derived_target = target_name or pdb_id
    derived_query = search_query or pdb_id

    notes = [
        "The current pipeline is target/query-oriented.",
        "This request was normalized from pdb_id compatibility mode.",
    ]
    if not fasta_from_pdb:
        notes.append("No FASTA sequence could be resolved for this pdb_id. The job will still be submitted with an empty fasta_seq.")

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
            "notes": notes,
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
      1) local jobs/<job>/TARGET_RESULTS/Results_Display.csv
      2) local jobs/<job>/Results_Display.csv
      3) RANDY archived job files

    This is the first backend fallback needed by the results gallery.
    If Heroku lost its local job folder after a restart, the gallery can still
    build cards from RANDY's archived Results_Display.csv.
    """
    fp = _first_existing([
        target_results_dir(job_id) / "Results_Display.csv",
        job_root(job_id) / "Results_Display.csv",
    ])

    df = None
    if fp:
        try:
            df = pd.read_csv(fp, dtype=str).fillna("")
        except Exception:
            df = None

    if df is None or df.empty:
        df = randy_get_table_dataframe(job_id, ["Results_Display.csv"])

    if df is None or df.empty:
        return None

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

    return df.fillna("")


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
    fp, _diag = resolve_sdf_path(job_root(job_id), pdb, chain, warhead)
    return fp


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
        companion_tool_links=COMPANION_TOOL_LINKS,
        primary_api_base=PRIMARY_API_BASE,
        secondary_api_base=SECONDARY_API_BASE,
        curated_examples=get_curated_examples(),
        preferred_example_job_id=DEFAULT_CURATED_EXAMPLE_JOB_ID,
        batch_preview_payload={
            "jobs": [
                {
                    "target_name": "OGA",
                    "search_query": "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",
                    "fasta_seq": ">FASTA_SEQUENCE",
                }
            ],
            "delay_seconds": 2,
        },
    )


@app.get("/api/health")
def api_health():
    return _api_json({
        "ok": True,
        "service": "Warhead Hunter API",
        "status": "healthy",
        "version": API_VERSION,
        "api_version": API_VERSION,
        "environment": APP_ENVIRONMENT,
        "timestamp": _utc_now_iso(),
        "time": _utc_now_iso(),
        "jobs_dir_exists": JOBS_DIR.exists(),
        "pipeline_assets_exists": (APP_ROOT / "pipeline_assets").exists(),
    })


@app.get("/api/manifest")
def api_manifest():
    return _api_json({
        "ok": True,
        "service": "Warhead Hunter API",
        "version": API_VERSION,
        "api_version": API_VERSION,
        "environment": APP_ENVIRONMENT,
        "timestamp": _utc_now_iso(),
        "base_urls": {
            "primary": PRIMARY_API_BASE,
            "secondary": SECONDARY_API_BASE,
        },
        "active_endpoint_groups": [
            {
                "title": group["title"],
                "description": group["description"],
                "routes": group["routes"],
            }
            for group in API_DOC_CURRENT_GROUPS
        ],
        "docs_urls": {
            "api_docs": "/api-docs",
            "browser": "/browse",
            "examples": "/examples",
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
    result = _submit_job_from_payload(payload)
    if not result.get("ok"):
        return _api_error(
            result["error"]["code"],
            result["error"]["message"],
            result.get("status_code", 400),
            result["error"].get("details") or {},
        )
    return _api_json(result)


@app.get("/api/jobs/<job_id>")
def api_job_status(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    meta = get_job_api_metadata(job_id)
    if meta is None:
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    _write_job_metadata_local(job_id, {
        "status": meta.get("status"),
        "current_step": meta.get("current_step"),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "request": meta.get("request", {}),
        "outputs": meta.get("outputs", {}),
        "error": meta.get("error"),
    })
    disk_state = compute_results_ready(job_id)
    meta.update(disk_state)

    return _api_json({"ok": True, **meta})


@app.get("/api/jobs/<job_id>/results")
def api_job_results(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    meta = get_job_api_metadata(job_id)
    if meta is None:
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    manifest = build_job_results_manifest(job_id)
    if not manifest["has_results"] and str(meta.get("status") or "").lower() not in {"completed", "failed"}:
        return _api_error("RESULTS_NOT_READY", "Results are not ready yet.", 202, {"job_id": job_id})

    _write_job_metadata_local(job_id, {
        "outputs": {
            **(meta.get("outputs") or {}),
            "results_manifest_available": True,
        }
    })
    return _api_json(manifest)


@app.get("/api/jobs/<job_id>/files")
def api_job_files(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)

    kind = (request.args.get("kind") or "all").strip().lower()
    if kind not in API_FILE_KINDS:
        return _api_error("INVALID_PATH", f"Unsupported kind filter: {kind}", 400, {"kind": kind})
    try:
        folder = _safe_folder_arg(request.args.get("folder") or "")
    except ValueError:
        return _api_error("INVALID_PATH", "Requested folder path is invalid.", 400)
    limit = _parse_limit(request.args.get("limit") or "1000", default=1000)

    files = list_safe_job_files(job_id, kind=kind, namespace="jobs", folder=folder, limit=limit)
    return _api_json({
        "ok": True,
        "job_id": job_id,
        "kind": kind,
        "folder": folder,
        "count": len(files),
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


@app.get("/api/jobs/<job_id>/war-pdbs")
def api_job_war_pdbs(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)
    files = _war_pdb_files(job_id, namespace="jobs")
    return _api_json({
        "ok": True,
        "job_id": job_id,
        "count": len(files),
        "files": files,
        "zip_url": f"/api/jobs/{job_id}/war-pdbs.zip",
    })


@app.get("/api/jobs/<job_id>/war-pdbs.zip")
def api_job_war_pdbs_zip(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)
    mem = _create_kind_zip(job_id, kind="war_pdb", namespace="jobs", root_name=f"{job_id}_WAR_PDB")
    if mem is None:
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{job_id}_WAR_PDB.zip",
    )


@app.get("/api/jobs/<job_id>/artifacts")
def api_job_artifacts(job_id):
    if not _safe_job_id(job_id):
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", "No job was found for this job_id.", 404)
    kind = (request.args.get("kind") or "all").strip().lower()
    if kind not in API_FILE_KINDS:
        return _api_error("INVALID_PATH", f"Unsupported kind filter: {kind}", 400, {"kind": kind})
    try:
        folder = _safe_folder_arg(request.args.get("folder") or "")
    except ValueError:
        return _api_error("INVALID_PATH", "Requested folder path is invalid.", 400)
    limit = _parse_limit(request.args.get("limit") or "1000", default=1000)
    return _api_json(_list_artifacts_response(job_id, "jobs", kind, folder, limit))


@app.post("/api/batches")
def api_batches():
    payload = request.get_json(silent=True)
    if payload is None:
        return _api_error("INVALID_JSON", "Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _api_error("INVALID_JSON", "Top-level JSON body must be an object.", 400)

    jobs_payload = payload.get("jobs")
    if not isinstance(jobs_payload, list) or not jobs_payload:
        return _api_error("MISSING_FIELD", "Field 'jobs' must be a non-empty JSON array.", 400, {"field": "jobs"})

    delay_seconds = payload.get("delay_seconds", 0)
    try:
        delay_seconds = max(0, min(float(delay_seconds), 30.0))
    except Exception:
        delay_seconds = 0.0

    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    batch_jobs = []
    for idx, job_payload in enumerate(jobs_payload, start=1):
        if not isinstance(job_payload, dict):
            return _api_error("INVALID_JSON", "Each item inside 'jobs' must be a JSON object.", 400, {"index": idx - 1})
        result = _submit_job_from_payload(job_payload)
        if not result.get("ok"):
            return _api_error(
                result["error"]["code"],
                f"Batch job {idx} failed validation or submission: {result['error']['message']}",
                result.get("status_code", 400),
                {"index": idx - 1, **(result["error"].get("details") or {})},
            )
        batch_jobs.append({
            "target_name": (_normalize_job_payload(job_payload).get("target_name") or ""),
            "search_query": (_normalize_job_payload(job_payload).get("search_query") or ""),
            "job_id": result["job_id"],
            "status_url": result["status_url"],
            "monitor_url": result["monitor_url"],
            "results_url": result["results_url"],
            "browser_results_url": result["browser_results_url"],
        })
        if delay_seconds and idx < len(jobs_payload):
            time.sleep(delay_seconds)

    batch_meta = _write_batch_metadata(batch_id, {
        "created_at": _utc_now_iso(),
        "count": len(batch_jobs),
        "delay_seconds": delay_seconds,
        "jobs": batch_jobs,
    })
    return _api_json({
        "ok": True,
        "batch_id": batch_id,
        "count": len(batch_jobs),
        "jobs": batch_jobs,
        "batch_status_url": f"/api/batches/{batch_id}",
        "batch_results_url": f"/api/batches/{batch_id}/results",
    })


@app.get("/api/batches/<batch_id>")
def api_batch_status(batch_id):
    batch_meta = _read_batch_metadata(batch_id)
    if not batch_meta:
        return _api_error("JOB_NOT_FOUND", "No batch was found for this batch_id.", 404)
    jobs = _collect_batch_status(batch_meta)
    return _api_json({
        "ok": True,
        "batch_id": batch_id,
        "created_at": batch_meta.get("created_at", ""),
        "updated_at": batch_meta.get("updated_at", ""),
        "count": batch_meta.get("count", len(jobs)),
        "delay_seconds": batch_meta.get("delay_seconds", 0),
        "jobs": jobs,
    })


@app.get("/api/batches/<batch_id>/results")
def api_batch_results(batch_id):
    batch_meta = _read_batch_metadata(batch_id)
    if not batch_meta:
        return _api_error("JOB_NOT_FOUND", "No batch was found for this batch_id.", 404)
    payload = []
    for item in batch_meta.get("jobs", []):
        job_id = item.get("job_id", "")
        meta = get_job_api_metadata(job_id)
        manifest = build_job_results_manifest(job_id) if meta else None
        payload.append({
            "job_id": job_id,
            "target_name": item.get("target_name", ""),
            "status": (meta or {}).get("status", "unknown"),
            "has_results": bool((meta or {}).get("has_results")),
            "results": manifest,
        })
    return _api_json({
        "ok": True,
        "batch_id": batch_id,
        "count": len(payload),
        "jobs": payload,
    })


@app.get("/api/examples")
def api_examples():
    return _api_json({
        "ok": True,
        "service": "Warhead Hunter API",
        "examples": get_curated_examples(),
    })


@app.get("/api/examples/<job_id>/metadata")
@app.get("/api/examples/<job_id>")
def api_example_detail(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_error("JOB_NOT_FOUND", f"Curated example not found: {job_id}", 404)

    entry = build_curated_example_entry(item)
    if entry["available"]:
        entry["files_count"] = len(list_safe_job_files(job_id, kind="all"))
    return _api_json({
        "ok": True,
        "service": "Warhead Hunter API",
        **entry,
    })


@app.get("/api/examples/<job_id>/files")
def api_example_files(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_error("JOB_NOT_FOUND", f"Curated example not found: {job_id}", 404)

    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", f"Curated example job is not available on this deployment: {job_id}", 404)

    kind = (request.args.get("kind") or "all").strip().lower()
    if kind not in API_FILE_KINDS:
        return _api_error("INVALID_PATH", f"Unsupported kind filter: {kind}", 400, {"kind": kind})
    try:
        folder = _safe_folder_arg(request.args.get("folder") or "")
    except ValueError:
        return _api_error("INVALID_PATH", "Requested folder path is invalid.", 400)
    limit = _parse_limit(request.args.get("limit") or "1000", default=1000)

    files = list_safe_job_files(job_id, kind=kind, namespace="examples", folder=folder, limit=limit)
    return _api_json({
        "ok": True,
        "service": "Warhead Hunter API",
        "job_id": job_id,
        "kind": kind,
        "folder": folder,
        "count": len(files),
        "files": files,
    })


@app.get("/api/examples/<job_id>/files/<path:filename>")
def api_example_file_download(job_id, filename):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_error("JOB_NOT_FOUND", f"Curated example not found: {job_id}", 404)

    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", f"Curated example job is not available on this deployment: {job_id}", 404)

    rel = str(filename or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return _api_error("INVALID_PATH", "Invalid filename", 400)

    fp = (base / rel).resolve()
    if not _is_safe_job_file(base, fp):
        return _api_error("FILE_NOT_FOUND", "File not found or not allowed", 404)
    if not fp.exists():
        return _api_error("FILE_NOT_FOUND", "File not found", 404)

    return send_file(fp, as_attachment=True, download_name=fp.name)


@app.get("/api/examples/<job_id>/bundle")
def api_example_bundle(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_error("JOB_NOT_FOUND", f"Curated example not found: {job_id}", 404)

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
        return _api_error("JOB_NOT_FOUND", f"Curated example job is not available on this deployment: {job_id}", 404)

    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{job_id}_warhead_hunter_example_results.zip",
    )


@app.get("/api/examples/<job_id>/war-pdbs")
def api_example_war_pdbs(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_error("JOB_NOT_FOUND", f"Curated example not found: {job_id}", 404)
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", f"Curated example job is not available on this deployment: {job_id}", 404)
    files = _war_pdb_files(job_id, namespace="examples")
    return _api_json({
        "ok": True,
        "job_id": job_id,
        "count": len(files),
        "files": files,
        "zip_url": f"/api/examples/{job_id}/war-pdbs.zip",
    })


@app.get("/api/examples/<job_id>/war-pdbs.zip")
def api_example_war_pdbs_zip(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_error("JOB_NOT_FOUND", f"Curated example not found: {job_id}", 404)
    mem = _create_kind_zip(job_id, kind="war_pdb", namespace="examples", root_name=f"{job_id}_WAR_PDB")
    if mem is None:
        return _api_error("JOB_NOT_FOUND", f"Curated example job is not available on this deployment: {job_id}", 404)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{job_id}_WAR_PDB.zip",
    )


@app.get("/api/examples/<job_id>/artifacts")
def api_example_artifacts(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return _api_error("JOB_NOT_FOUND", f"Curated example not found: {job_id}", 404)
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return _api_error("JOB_NOT_FOUND", f"Curated example job is not available on this deployment: {job_id}", 404)
    kind = (request.args.get("kind") or "all").strip().lower()
    if kind not in API_FILE_KINDS:
        return _api_error("INVALID_PATH", f"Unsupported kind filter: {kind}", 400, {"kind": kind})
    try:
        folder = _safe_folder_arg(request.args.get("folder") or "")
    except ValueError:
        return _api_error("INVALID_PATH", "Requested folder path is invalid.", 400)
    limit = _parse_limit(request.args.get("limit") or "1000", default=1000)
    return _api_json(_list_artifacts_response(job_id, "examples", kind, folder, limit))


@app.get("/api/indexed-jobs")
def api_indexed_jobs():
    protein_filter = (request.args.get("protein") or request.args.get("target") or "").strip().lower()
    query_filter = (request.args.get("query") or "").strip().lower()
    available_filter = (request.args.get("available") or "").strip().lower()
    limit = _parse_limit(request.args.get("limit") or "50", default=50, maximum=5000)
    try:
        offset = max(0, int((request.args.get("offset") or "0").strip() or 0))
    except Exception:
        offset = 0
    sort_key = (request.args.get("sort") or "created_desc").strip().lower()

    jobs = load_indexed_jobs()

    if protein_filter:
        jobs = [j for j in jobs if protein_filter in (j.get("protein", "").lower()) or protein_filter in (j.get("target_name", "").lower())]
    if query_filter:
        jobs = [j for j in jobs if query_filter in (j.get("search_query", "").lower())]
    if available_filter in {"true", "false"}:
        want = available_filter == "true"
        jobs = [j for j in jobs if bool(j.get("has_results")) == want]

    if sort_key == "created_asc":
        jobs.sort(key=lambda j: j.get("created_at", ""))
    elif sort_key == "protein":
        jobs.sort(key=lambda j: (j.get("protein", "").lower(), j.get("job_id", "")))
    elif sort_key == "job_id":
        jobs.sort(key=lambda j: j.get("job_id", ""))
    else:
        jobs.sort(key=lambda j: j.get("created_at", "") or j.get("modified_at", ""), reverse=True)

    total_count = len(jobs)
    jobs = jobs[offset:offset + limit]
    job_summaries = [
        {
            "job_id": j.get("job_id", ""),
            "protein": j.get("protein", ""),
            "target_name": j.get("target_name", ""),
            "search_query": j.get("search_query", ""),
            "fasta_len": j.get("fasta_len", 0),
            "has_results": bool(j.get("has_results")),
            "available": bool(j.get("available")),
            "created_at": j.get("created_at", ""),
            "modified_at": j.get("modified_at", ""),
            "monitor_url": f"/monitor/{j.get('job_id', '')}",
            "browser_results_url": f"/results/{j.get('job_id', '')}",
            "api_status_url": f"/api/jobs/{j.get('job_id', '')}",
            "files_url": f"/api/jobs/{j.get('job_id', '')}/files",
            "bundle_url": f"/api/jobs/{j.get('job_id', '')}/bundle",
            "war_pdbs_url": f"/api/jobs/{j.get('job_id', '')}/war-pdbs",
        }
        for j in jobs
    ]
    return _api_json({
        "ok": True,
        "count": total_count,
        "returned": len(job_summaries),
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


@app.route("/how-to-use")
def how_to_use():
    return render_template("how_to_use.html")


@app.route("/science")
def science():
    return render_template("science.html")


@app.route("/use-cases")
def use_cases():
    return render_template("use_cases.html")


@app.route("/examples")
def examples_page():
    examples = get_curated_examples()
    available_count = len([example for example in examples if example.get("available")])
    return render_template(
        "examples.html",
        curated_examples=examples,
        available_examples=available_count,
        total_examples=len(examples),
    )


@app.route("/examples/<job_id>")
def example_detail_page(job_id):
    item = get_curated_example_by_id(job_id)
    if not item:
        return render_template(
            "example_detail.html",
            example=None,
            requested_job_id=job_id,
        ), 404

    return render_template(
        "example_detail.html",
        example=build_curated_example_entry(item, include_preview=True),
        requested_job_id=job_id,
    )


@app.route("/faq")
def faq():
    return render_template("faq.html")


@app.route("/docs")
def docs_page():
    return render_template("docs.html")


@app.route("/publications")
def publications():
    return render_template("publications.html")


@app.route("/ecosystem")
def ecosystem():
    return render_template("ecosystem.html")

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
    return disk_jobs.results_ready_from_disk(job_path.name, JOBS_DIR)


def get_jobs_root() -> Path:
    return Path(app.config["JOBS_DIR"])


def safe_job_dir(job_id: str) -> Optional[Path]:
    try:
        return disk_jobs.job_dir_for(job_id, get_jobs_root())
    except Exception:
        return None


def _fasta_length(fasta: str) -> int:
    if not fasta:
        return 0
    seq = "\n".join([ln for ln in fasta.splitlines() if not ln.strip().startswith(">")])
    seq = re.sub(r"[^A-Za-z]", "", seq)
    return len(seq)


def _job_meta_from_dir(job_dir: Path) -> Dict[str, Any]:
    protein_meta = _read_protein_data_csv(job_dir) or {}
    disk_meta = _read_job_metadata(job_dir.name) or {}
    hydrated = disk_jobs.hydrate_job_from_disk(job_dir.name, get_jobs_root(), JOB_STORE.get(job_dir.name)) or {}
    request_meta = disk_meta.get("request") or {}
    fasta = (
        str(request_meta.get("fasta_seq") or "").strip()
        or str(protein_meta.get("fasta") or "").strip()
    )

    try:
        stat = job_dir.stat()
        created_s = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
        modified_s = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        created_s = ""
        modified_s = ""

    target_name = (
        str(request_meta.get("target_name") or "").strip()
        or str(protein_meta.get("protein") or "").strip()
    )
    search_query = (
        str(request_meta.get("search_query") or "").strip()
        or str(protein_meta.get("search_query") or "").strip()
    )

    return {
        "job_id": job_dir.name,
        "protein": target_name,
        "target_name": target_name,
        "search_query": search_query,
        "fasta": fasta,
        "fasta_len": _fasta_length(fasta),
        "has_results": bool(hydrated.get("results_ready")),
        "available": bool(hydrated.get("results_ready")),
        "status": str(hydrated.get("status") or disk_meta.get("status") or "unknown").lower(),
        "created_at": str(disk_meta.get("created_at") or created_s),
        "modified_at": modified_s,
        "mtime": modified_s,
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
        if jid.startswith("_"):
            continue
        if not _safe_job_id(jid):
            continue
        jobs.append(_job_meta_from_dir(d))

    jobs.sort(key=lambda x: x.get("modified_at", ""), reverse=True)
    return jobs


def classify_file_kind(path: Path) -> str:
    rel = path.as_posix().lower()
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name == "job_result_manifest.json":
        return "manifest"
    if rel.startswith("war_pdb/") or "/war_pdb/" in rel:
        return "war_pdb" if suffix == ".pdb" else "other"
    if suffix == ".pdb":
        return "pdb"
    if suffix == ".sdf":
        return "sdf"
    if suffix == ".svg":
        return "svg"
    if suffix == ".tsv":
        return "table"
    if suffix == ".csv":
        if any(token in name for token in [
            "results_display", "summary", "metadata", "index", "table",
            "atoms", "chainrename", "chain_similarity", "filtered_data",
            "cifdata", "ligand_mcs_map"
        ]) or any(part.lower() in {"target_table"} for part in path.parts):
            return "table"
        return "csv"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".json":
        return "manifest"
    if suffix in {".log", ".txt"}:
        return "log"
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
    if any(part.endswith(".app") for part in rel_parts):
        return False
    if fp_r.name.lower() in SAFE_SKIP_NAMES:
        return False
    if fp_r.suffix.lower() not in SAFE_RESULT_SUFFIXES:
        return False
    if fp_r.name.endswith(".py"):
        return False
    return True


def build_file_download_url(job_id: str, relative_path: str, namespace: str = "examples") -> str:
    rel = quote(relative_path.replace("\\", "/").lstrip("/"), safe="/")
    if namespace == "examples":
        return f"/api/examples/{job_id}/files/{rel}"
    return f"/api/jobs/{job_id}/files/{rel}"


def _filter_file_item(item: Dict[str, Any], kind: str, folder: str) -> bool:
    if kind not in {"", "all"}:
        if kind == "table":
            if item["kind"] != "table":
                return False
        elif item["kind"] != kind:
            return False

    if folder:
        wanted = folder.replace("\\", "/").strip("/").lower()
        if not item["relative_path"].lower().startswith(wanted):
            return False

    return True


def list_safe_job_files(
    job_id: str,
    kind: Optional[str] = None,
    namespace: str = "examples",
    folder: str = "",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return []

    kind_norm = (kind or "all").strip().lower()
    folder_norm = folder.replace("\\", "/").strip("/")
    files: List[Dict[str, Any]] = []

    for fp in base.rglob("*"):
        if not _is_safe_job_file(base, fp):
            continue

        rel = fp.relative_to(base).as_posix()
        try:
            stat = fp.stat()
            size_bytes = stat.st_size
            modified_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            size_bytes = 0
            modified_at = ""

        file_kind = classify_file_kind(Path(rel))
        item = {
            "name": fp.name,
            "filename": fp.name,
            "relative_path": rel,
            "kind": file_kind,
            "size_bytes": size_bytes,
            "modified_at": modified_at,
            "download_url": build_file_download_url(job_id, rel, namespace=namespace),
        }

        if not _filter_file_item(item, kind_norm, folder_norm):
            continue

        files.append(item)

    files.sort(key=lambda x: (x["kind"], x["relative_path"]))
    if isinstance(limit, int) and limit > 0:
        files = files[:limit]
    return files


def create_safe_job_zip(
    job_id: str,
    mode: str = "example",
    kind: str = "all",
    folder: str = "",
    root_name: Optional[str] = None,
) -> Optional[io.BytesIO]:
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return None

    if mode == "job":
        rel_paths = []
        for fp in base.rglob("*"):
            if fp.is_file():
                rel_paths.append(fp.relative_to(base).as_posix())
    else:
        safe_files = list_safe_job_files(job_id, kind=kind, namespace="jobs" if mode == "job_api" else "examples", folder=folder)
        rel_paths = [item["relative_path"] for item in safe_files]

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in rel_paths:
            fp = (base / rel).resolve()
            if mode != "job" and not _is_safe_job_file(base, fp):
                continue
            if not fp.exists() or not fp.is_file():
                continue
            zip_root = root_name or job_id
            z.write(fp, arcname=str(Path(zip_root) / rel))

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
    base = safe_job_dir(job_id)
    files = list_safe_job_files(job_id, kind="all", namespace="jobs")
    counts = {
        "war_pdb": len([f for f in files if f["kind"] == "war_pdb"]),
        "pdb": len([f for f in files if f["kind"] == "pdb"]),
        "sdf": len([f for f in files if f["kind"] == "sdf"]),
        "svg": len([f for f in files if f["kind"] == "svg"]),
        "csv": len([f for f in files if f["kind"] == "csv"]),
        "table": len([f for f in files if f["kind"] == "table"]),
        "html": len([f for f in files if f["kind"] == "html"]),
        "manifest": len([f for f in files if f["kind"] == "manifest"]),
        "tsv": len([f for f in files if f["relative_path"].lower().endswith(".tsv")]),
    }

    def _find_rel(candidates: List[Path]) -> str:
        fp = _first_existing(candidates)
        if not fp or not base:
            return ""
        try:
            return fp.relative_to(base).as_posix()
        except Exception:
            return ""

    return {
        "ok": True,
        "job_id": job_id,
        "has_results": _job_has_results(job_root(job_id)),
        "target_results_dir": "TARGET_RESULTS",
        "summary_files": [
            item for item in files
            if item["relative_path"].endswith("Results_Display.csv")
            or "summary" in item["relative_path"].lower()
        ],
        "display_files": [
            item for item in files
            if item["kind"] in {"html", "svg"}
        ],
        "counts": counts,
        "key_outputs": {
            "results_display": _find_rel([
                target_results_dir(job_id) / "Results_Display.csv",
                job_root(job_id) / "Results_Display.csv",
            ]),
            "resolved_sasa_summary": _find_rel([
                target_results_dir(job_id) / "Resolved_SASA_Summary.csv",
                target_results_dir(job_id) / "Resolved_SASA_Summary.tsv",
                job_root(job_id) / "Resolved_SASA_Summary.csv",
                job_root(job_id) / "Resolved_SASA_Summary.tsv",
            ]),
            "ligand_metadata": _find_rel([
                target_results_dir(job_id) / "Ligand_Metadata.csv",
                job_root(job_id) / "Ligand_Metadata.csv",
            ]),
            "war_pdb_dir": (
                "TARGET_RESULTS/WAR_PDB" if (target_results_dir(job_id) / "WAR_PDB").exists()
                else ("WAR_PDB" if (job_root(job_id) / "WAR_PDB").exists() else "")
            ),
            "mcs_output_dir": (
                "TARGET_RESULTS/MCS_Output" if (target_results_dir(job_id) / "MCS_Output").exists()
                else ("MCS_Output" if (job_root(job_id) / "MCS_Output").exists() else "")
            ),
        },
        "urls": {
            "files": f"/api/jobs/{job_id}/files",
            "bundle": f"/api/jobs/{job_id}/bundle",
            "browser_results": f"/results/{job_id}",
        },
        "files": files,
    }


def get_job_api_metadata(job_id: str) -> Optional[Dict[str, Any]]:
    base = safe_job_dir(job_id)
    if not base or not base.exists():
        return None

    hydrated = disk_jobs.hydrate_job_from_disk(job_id, get_jobs_root(), JOB_STORE.get(job_id)) or {}
    data = _read_job_metadata(job_id) or {}
    live = JOB_STORE.get(job_id, {})
    protein_meta = _read_protein_data_csv(base) or {}
    request_meta = dict(data.get("request") or {})
    target_name = (
        str(request_meta.get("target_name") or "").strip()
        or str(protein_meta.get("protein") or "").strip()
    )
    search_query = (
        str(request_meta.get("search_query") or "").strip()
        or str(protein_meta.get("search_query") or "").strip()
    )

    if not data:
        data = {
            "job_id": job_id,
            "status": live.get("status") or ("completed" if _job_has_results(base) else "unknown"),
            "created_at": live.get("created_at") or "",
            "updated_at": _utc_now_iso(),
            "source": "web",
            "request": {
                "target_name": target_name,
                "search_query": search_query,
                "fasta_seq": (protein_meta.get("fasta") or "").strip(),
            },
            "outputs": {},
            "error": None,
        }

    if live:
        data["created_at"] = data.get("created_at") or live.get("created_at", "")
        data["started_at"] = data.get("started_at") or live.get("started_at", "")
        data["finished_at"] = data.get("finished_at") or live.get("finished_at", "")
        data["current_step"] = data.get("current_step") or live.get("current_step", "")
        data["step_started_at"] = data.get("step_started_at") or live.get("step_started_at", "")

    public_bundle = get_preferred_public_bundle(job_id)
    outputs = dict(data.get("outputs") or {})
    files = list_safe_job_files(job_id, kind="all", namespace="jobs")
    artifact_counts = {
        "war_pdb_count": len([f for f in files if f["kind"] == "war_pdb"]),
        "sdf_count": len([f for f in files if f["kind"] == "sdf"]),
        "svg_count": len([f for f in files if f["kind"] == "svg"]),
        "csv_count": len([f for f in files if f["kind"] == "csv"]),
        "table_count": len([f for f in files if f["kind"] == "table"]),
    }
    outputs.update({
        "job_dir": str(base),
        "has_results": bool(hydrated.get("results_ready")),
        "results_url": f"/api/jobs/{job_id}/results",
        "files_url": f"/api/jobs/{job_id}/files",
        "bundle_url": f"/api/jobs/{job_id}/bundle",
        "war_pdbs_url": f"/api/jobs/{job_id}/war-pdbs",
        "legacy_download_url": f"/api/jobs/{job_id}/download",
        "public_bundle_path": public_bundle.relative_to(base).as_posix() if public_bundle else outputs.get("public_bundle_path", ""),
    })
    data["outputs"] = outputs
    data["updated_at"] = _utc_now_iso()
    return {
        "job_id": job_id,
        "status": str(hydrated.get("status") or data.get("status") or ("completed" if _job_has_results(base) else "unknown")).lower(),
        "target_name": target_name,
        "search_query": search_query,
        "created_at": hydrated.get("created_at", data.get("created_at", "")),
        "started_at": hydrated.get("started_at", data.get("started_at", "")),
        "finished_at": hydrated.get("finished_at", data.get("finished_at", "")),
        "current_step": hydrated.get("current_step", data.get("current_step", "")),
        "source": data.get("source", "web"),
        "request": data.get("request", {}),
        "outputs": outputs,
        "error": hydrated.get("error", data.get("error")),
        "monitor_url": f"/monitor/{job_id}",
        "results_url": f"/api/jobs/{job_id}/results",
        "files_url": f"/api/jobs/{job_id}/files",
        "bundle_url": f"/api/jobs/{job_id}/bundle",
        "browser_results_url": f"/results/{job_id}",
        "has_results": bool(hydrated.get("results_ready")),
        "results_ready": bool(hydrated.get("results_ready")),
        "available_artifacts": artifact_counts,
    }


def get_curated_example_by_id(job_id: str) -> Optional[Dict[str, Any]]:
    for item in CURATED_EXAMPLE_CONFIG:
        if item["job_id"] == job_id:
            return dict(item)
    return None


def _preferred_existing_path(candidates: List[Path]) -> Optional[Path]:
    return _first_existing(candidates)


def _preferred_example_paths(job_id: str) -> Dict[str, Optional[Path]]:
    return {
        "results_display": _preferred_existing_path([
            target_results_dir(job_id) / "Results_Display.csv",
            job_root(job_id) / "Results_Display.csv",
        ]),
        "resolved_sasa_summary": _preferred_existing_path([
            target_results_dir(job_id) / "Resolved_SASA_Summary.csv",
            target_results_dir(job_id) / "Resolved_SASA_Summary.tsv",
            job_root(job_id) / "Resolved_SASA_Summary.csv",
            job_root(job_id) / "Resolved_SASA_Summary.tsv",
        ]),
        "ligand_metadata": _preferred_existing_path([
            target_results_dir(job_id) / "Ligand_Metadata.csv",
            job_root(job_id) / "Ligand_Metadata.csv",
        ]),
        "war_pdb_dir": _preferred_existing_path([
            target_results_dir(job_id) / "WAR_PDB",
            job_root(job_id) / "WAR_PDB",
        ]),
        "mcs_sdf_dir": _preferred_existing_path([
            target_results_dir(job_id) / "MCS_Output" / "MCS_SDF",
            job_root(job_id) / "MCS_Output" / "MCS_SDF",
        ]),
        "mcs_svg_dir": _preferred_existing_path([
            target_results_dir(job_id) / "MCS_Output" / "MCS_SVG",
            job_root(job_id) / "MCS_Output" / "MCS_SVG",
        ]),
        "target_results_dir": _preferred_existing_path([
            target_results_dir(job_id),
            job_root(job_id) / "TARGET_RESULTS",
        ]),
        "public_bundle": get_preferred_public_bundle(job_id),
    }


def _count_dir_files(path: Optional[Path], suffixes: tuple[str, ...]) -> int:
    if not path or not path.exists() or not path.is_dir():
        return 0
    wanted = tuple(s.lower() for s in suffixes)
    return len([fp for fp in path.rglob("*") if fp.is_file() and fp.suffix.lower() in wanted])


def _count_results_rows(job_id: str) -> int:
    df = load_results_display(job_id)
    if df is None or df.empty:
        return 0
    return len(df.index)


def _example_pose_preview(job_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    df = load_results_display(job_id)
    if df is None or df.empty:
        return []

    if "pdb_id" not in df.columns and "pdb" in df.columns:
        df["pdb_id"] = df["pdb"]
    for col in ("Chain", "Warhead", "SMILES", "Target"):
        if col not in df.columns:
            df[col] = ""
    if "%Exposed" not in df.columns:
        df["%Exposed"] = "0"

    preview = df.copy()
    preview["Target"] = preview["Target"].astype(str).fillna("").str.strip()
    preview["pdb_id"] = preview["pdb_id"].astype(str).fillna("").str.lower()
    preview["Chain"] = preview["Chain"].astype(str).fillna("").str.upper()
    preview["Warhead"] = preview["Warhead"].astype(str).fillna("").str.upper()
    preview["%Exposed_num"] = pd.to_numeric(preview["%Exposed"], errors="coerce").fillna(0.0)
    preview = preview.sort_values("%Exposed_num", ascending=False).head(limit)

    rows: List[Dict[str, Any]] = []
    for row in preview.to_dict(orient="records"):
        rows.append({
            "target": str(row.get("Target") or "").strip(),
            "pdb_id": str(row.get("pdb_id") or "").strip(),
            "chain": str(row.get("Chain") or "").strip(),
            "warhead": str(row.get("Warhead") or "").strip(),
            "smiles": str(row.get("SMILES") or "").strip(),
            "exposed_percent": round(float(row.get("%Exposed_num") or 0.0), 1),
        })
    return rows


def _example_status_reason(available: bool, has_results: bool, counts: Dict[str, Any], paths: Dict[str, Optional[Path]]) -> str:
    if not available:
        return "This curated example is configured, but its job artifacts are not present on this deployment."
    if not has_results:
        return "The job folder is present, but the result display artifacts are not ready for browsing yet."

    missing = []
    if not paths.get("results_display"):
        missing.append("Results_Display.csv")
    if not paths.get("resolved_sasa_summary"):
        missing.append("Resolved SASA summary")
    if counts.get("war_pdb_count", 0) == 0:
        missing.append("WAR_PDB structures")
    if counts.get("sdf_count", 0) == 0:
        missing.append("SDF structures")
    if counts.get("svg_count", 0) == 0:
        missing.append("SVG ligand maps")

    if missing:
        return "Partial example availability. Missing or empty artifacts: " + ", ".join(missing) + "."
    return "Ready for read-only browsing, downloads, and API inspection."


def build_curated_example_entry(item: Dict[str, Any], include_preview: bool = False) -> Dict[str, Any]:
    job_id = item["job_id"]
    base = safe_job_dir(job_id)
    available = bool(base and base.exists())
    meta = _job_meta_from_dir(base) if available else {}
    paths = _preferred_example_paths(job_id) if available else {}
    has_results = bool(meta.get("has_results")) if available else False
    counts = {
        "result_rows": _count_results_rows(job_id) if available else 0,
        "war_pdb_count": _count_dir_files(paths.get("war_pdb_dir"), (".pdb",)) if available else 0,
        "sdf_count": _count_dir_files(paths.get("mcs_sdf_dir"), (".sdf",)) if available else 0,
        "svg_count": _count_dir_files(paths.get("mcs_svg_dir"), (".svg",)) if available else 0,
        "table_count": 0,
        "csv_count": 0,
        "bundle_available": bool(paths.get("public_bundle")) if available else False,
    }
    if available:
        safe_files = list_safe_job_files(job_id, kind="all", namespace="examples")
        counts["table_count"] = len([f for f in safe_files if f["kind"] == "table"])
        counts["csv_count"] = len([f for f in safe_files if f["kind"] == "csv"])
        counts["downloadable_file_count"] = len(safe_files)
    else:
        counts["downloadable_file_count"] = 0

    status_reason = _example_status_reason(available, has_results, counts, paths)
    partial = available and has_results and (
        counts["war_pdb_count"] == 0
        or counts["sdf_count"] == 0
        or counts["svg_count"] == 0
        or not paths.get("results_display")
    )
    status = "unavailable"
    if available and has_results and not partial:
        status = "available"
    elif available:
        status = "partial"

    links = {
        "page": f"/examples/{job_id}",
        "results": f"/results/{job_id}" if has_results else "",
        "metadata": f"/api/examples/{job_id}/metadata",
        "metadata_legacy": f"/api/examples/{job_id}",
        "files": f"/api/examples/{job_id}/files",
        "bundle": f"/api/examples/{job_id}/bundle",
        "war_pdbs": f"/api/examples/{job_id}/war-pdbs",
        "war_pdbs_zip": f"/api/examples/{job_id}/war-pdbs.zip",
        "artifacts": f"/api/examples/{job_id}/artifacts",
        "api_docs": "/api-docs",
    }
    key_outputs = {
        "results_display": paths.get("results_display").name if available and paths.get("results_display") else "",
        "resolved_sasa_summary": paths.get("resolved_sasa_summary").name if available and paths.get("resolved_sasa_summary") else "",
        "ligand_metadata": paths.get("ligand_metadata").name if available and paths.get("ligand_metadata") else "",
        "war_pdb_dir": "TARGET_RESULTS/WAR_PDB" if available and paths.get("war_pdb_dir") and "TARGET_RESULTS" in paths["war_pdb_dir"].as_posix() else ("WAR_PDB" if available and paths.get("war_pdb_dir") else ""),
        "mcs_sdf_dir": "TARGET_RESULTS/MCS_Output/MCS_SDF" if available and paths.get("mcs_sdf_dir") and "TARGET_RESULTS" in paths["mcs_sdf_dir"].as_posix() else ("MCS_Output/MCS_SDF" if available and paths.get("mcs_sdf_dir") else ""),
        "mcs_svg_dir": "TARGET_RESULTS/MCS_Output/MCS_SVG" if available and paths.get("mcs_svg_dir") and "TARGET_RESULTS" in paths["mcs_svg_dir"].as_posix() else ("MCS_Output/MCS_SVG" if available and paths.get("mcs_svg_dir") else ""),
    }

    entry = {
        "job_id": job_id,
        "label": item.get("label", job_id),
        "protein": (meta.get("protein") or item.get("protein") or "").strip(),
        "search_query": (meta.get("search_query") or "").strip(),
        "use_case": item.get("use_case", ""),
        "available": available,
        "has_results": has_results,
        "status": status,
        "status_reason": status_reason,
        "counts": counts,
        "key_outputs": key_outputs,
        "links": links,
        "urls": {
            "browser": links["results"],
            "metadata": links["metadata"],
            "files": links["files"],
            "bundle": links["bundle"],
            "war_pdbs": links["war_pdbs"],
            "war_pdbs_zip": links["war_pdbs_zip"],
            "artifacts": links["artifacts"],
            "page": links["page"],
        },
        "job_url": links["metadata"],
        "files_url": links["files"],
        "bundle_url": links["bundle"],
        "browser_url": links["results"],
        "page_url": links["page"],
        "api_curl": f'curl -s "$BASE/api/examples/{job_id}" | python -m json.tool',
        "what_you_can_do": [
            "Inspect read-only example outputs before launching a new job.",
            "Download prepared WAR_PDB, SDF, SVG, CSV, and ZIP bundle artifacts when available.",
            "Open the normal results gallery to browse ligand-exposure poses in the app.",
            "Use the metadata and files endpoints as stable examples for automation scripts.",
        ],
    }
    if include_preview:
        entry["preview_rows"] = _example_pose_preview(job_id)
    return entry


def get_curated_examples() -> List[Dict[str, Any]]:
    return [build_curated_example_entry(item) for item in CURATED_EXAMPLE_CONFIG]


API_FILE_KINDS = {"all", "war_pdb", "pdb", "sdf", "svg", "csv", "table", "html", "manifest", "log", "other"}


def _parse_limit(value: str, default: int = 500, maximum: int = 2000) -> int:
    try:
        parsed = int(str(value or default).strip())
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def _safe_folder_arg(folder: str) -> str:
    folder = str(folder or "").replace("\\", "/").strip().strip("/")
    if not folder:
        return ""
    parts = [part for part in folder.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Invalid folder")
    return "/".join(parts)


def _list_artifacts_response(job_id: str, namespace: str, kind: str, folder: str, limit: int) -> Dict[str, Any]:
    files = list_safe_job_files(job_id, kind=kind, namespace=namespace, folder=folder, limit=limit)
    return {
        "ok": True,
        "job_id": job_id,
        "kind": kind,
        "folder": folder,
        "count": len(files),
        "files": files,
    }


def _war_pdb_files(job_id: str, namespace: str = "jobs") -> List[Dict[str, Any]]:
    return list_safe_job_files(
        job_id,
        kind="war_pdb",
        namespace=namespace,
    )


def _create_kind_zip(job_id: str, kind: str, namespace: str, root_name: str) -> Optional[io.BytesIO]:
    return create_safe_job_zip(
        job_id,
        mode="job_api" if namespace == "jobs" else "example",
        kind=kind,
        folder="",
        root_name=root_name,
    )


def _submit_job_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    unsupported_keys = [k for k in ("structure_path", "structure_file", "input_path", "upload_path") if k in payload]
    if unsupported_keys:
        return {
            "ok": False,
            "error": {
                "code": "UNSUPPORTED_INPUT",
                "message": "Direct structure-path submission is not supported by the current pipeline. Use target_name/search_query/fasta_seq or pdb_id compatibility mode.",
                "details": {"unsupported_fields": unsupported_keys},
            },
            "status_code": 400,
        }

    built = _build_api_job_request(payload)
    if not built.get("ok"):
        code, message, details = built["error"]
        return {
            "ok": False,
            "error": {"code": code, "message": message, "details": details},
            "status_code": 400 if code in {"INVALID_JSON", "MISSING_FIELD", "UNSUPPORTED_INPUT"} else 422,
        }

    try:
        job_id = start_job(
            built["target_name"],
            built["search_query"],
            built["fasta_seq"],
            source="api",
            request_payload=built["request_payload"],
        )
    except Exception as e:
        return {
            "ok": False,
            "error": {
                "code": "PIPELINE_START_FAILED",
                "message": "The pipeline could not be started.",
                "details": {"message": str(e)},
            },
            "status_code": 500,
        }

    meta = get_job_api_metadata(job_id) or {}
    _write_job_metadata_local(job_id, {
        "source": "api",
        "request": built["request_payload"],
    })
    return {
        "ok": True,
        "job_id": job_id,
        "status": "running" if meta.get("status", "running") in {"queued", "pending"} else meta.get("status", "running"),
        "status_url": f"/api/jobs/{job_id}",
        "monitor_url": f"/monitor/{job_id}",
        "results_url": f"/api/jobs/{job_id}/results",
        "files_url": f"/api/jobs/{job_id}/files",
        "bundle_url": f"/api/jobs/{job_id}/bundle",
        "browser_results_url": f"/results/{job_id}",
        "notes": built["request_payload"].get("notes", []),
    }


def _collect_batch_status(batch_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    jobs = []
    for item in batch_data.get("jobs", []):
        job_id = str(item.get("job_id") or "").strip()
        if not job_id:
            continue
        meta = get_job_api_metadata(job_id)
        jobs.append({
            **item,
            "status": (meta or {}).get("status", "unknown"),
            "has_results": bool((meta or {}).get("has_results")),
            "browser_results_url": (meta or {}).get("browser_results_url", f"/results/{job_id}"),
        })
    return jobs



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

    job = disk_jobs.hydrate_job_from_disk(job_id, get_jobs_root(), JOB_STORE.get(job_id))
    if job is None:
        abort(404, "Job not found on disk.")

    if job.get("results_ready"):
        return redirect(f"/results/{job_id}")

    return redirect(url_for("job_monitor", job_id=job_id))

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


@app.route("/robots.txt")
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {_public_url('/sitemap.xml')}",
        "",
    ])
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    urls = [
        "/",
        "/hunter",
        "/scout",
        "/how-to-use",
        "/science",
        "/use-cases",
        "/examples",
        "/faq",
        "/docs",
        "/api-docs",
        "/about",
        "/publications",
        "/ecosystem",
    ]
    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in urls:
        xml.append("  <url>")
        xml.append(f"    <loc>{_public_url(path)}</loc>")
        xml.append("  </url>")
    xml.append("</urlset>")
    return Response("\n".join(xml), mimetype="application/xml")


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
    if not _safe_job_id(job_id):
        return "Job not found", 404
    job = disk_jobs.hydrate_job_from_disk(job_id, get_jobs_root(), JOB_STORE.get(job_id))
    if job is None:
        return "Job not found", 404
    return render_template("monitor.html", job=job, job_id=job_id)


@app.route("/api/job_log/<job_id>")
def job_log(job_id):
    if not _safe_job_id(job_id):
        return jsonify({"ok": False, "error": "Job not found", "job_id": job_id}), 404

    job = disk_jobs.hydrate_job_from_disk(job_id, get_jobs_root(), JOB_STORE.get(job_id))
    if job is None:
        return jsonify({"ok": False, "error": "Job not found", "job_id": job_id}), 404

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "status": job.get("status", "unknown"),
        "target": job.get("target", ""),
        "current_step": job.get("current_step", ""),
        "results_ready": bool(job.get("results_ready")),
        "log": job.get("log", []),
        "error": job.get("error"),
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
SVG_CANVAS_BG = "#020607"


def _serve_themed_svg(fp: Path):
    try:
        svg = fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return send_file(fp)

    # RDKit emits a full-canvas background rect near the top of the SVG. Keep
    # molecule/highlight colors untouched and theme only that canvas rect.
    bg_rect = re.compile(
        r"(<rect\b(?=[^>]*\bwidth=['\"](?:100%|[0-9.]+)['\"])(?=[^>]*\bheight=['\"](?:100%|[0-9.]+)['\"])[^>]*\b(?:fill\s*:\s*(?:#fff(?:fff)?|white)|fill=['\"](?:#fff(?:fff)?|white)['\"])[^>]*>)",
        re.IGNORECASE,
    )

    def replace_rect(match):
        rect = match.group(1)
        if re.search(r"fill\s*:\s*(?:#fff(?:fff)?|white)", rect, flags=re.IGNORECASE):
            rect = re.sub(r"fill\s*:\s*(?:#fff(?:fff)?|white)", f"fill:{SVG_CANVAS_BG}", rect, flags=re.IGNORECASE)
        else:
            rect = re.sub(r"fill=['\"](?:#fff(?:fff)?|white)['\"]", f"fill='{SVG_CANVAS_BG}'", rect, flags=re.IGNORECASE)
        return rect

    themed, count = bg_rect.subn(replace_rect, svg, count=1)
    if count == 0:
        themed = re.sub(
            r"(<svg\b[^>]*>)",
            rf"\1<rect width='100%' height='100%' x='0' y='0' fill='{SVG_CANVAS_BG}'/>",
            svg,
            count=1,
            flags=re.IGNORECASE,
        )

    return Response(themed, mimetype="image/svg+xml")


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
                return _serve_themed_svg(fp)

        # 2) fallback: wildcard any residue
        hits = sorted(mcs_dir.glob(f"{pdb}_{chain}_{warhead}_*_exposed.svg"))
        if hits:
            return _serve_themed_svg(hits[0])

    # --- Legacy fallback (old LIGAND_SVGS behavior) ---
    base_dir = ligand_svgs_dir(job_id)
    if base_dir:
        fname = f"{pdb}_{chain}_{warhead}_exposed.svg"
        fp = base_dir / fname
        if fp.exists():
            return _serve_themed_svg(fp)

    # RANDY fallback for archived exposed SVGs.
    asset = randy_find_asset(
        job_id,
        pdb=pdb,
        chain=chain,
        ligand=warhead,
        resid=(request.args.get("resid") or ""),
        kind="svg",
        plain=False,
    )
    if asset:
        proxied = randy_proxy_file_response(
            job_id,
            asset.get("relative_path", ""),
            mimetype="image/svg+xml",
        )
        if proxied:
            return proxied

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
                return _serve_themed_svg(fp)

        # 2) fallback: wildcard any residue
        hits = sorted(mcs_dir.glob(f"{pdb}_{chain}_{warhead}_*_plain.svg"))
        if hits:
            return _serve_themed_svg(hits[0])

    # --- Legacy fallback (old LIGAND_SVGS behavior) ---
    base_dir = ligand_svgs_dir(job_id)
    if base_dir:
        fname = f"{pdb}_{chain}_{warhead}_plain.svg"
        fp = base_dir / fname
        if fp.exists():
            return _serve_themed_svg(fp)

        legacy = base_dir / f"{pdb}_{chain}_{warhead}.svg"
        if legacy.exists():
            return _serve_themed_svg(legacy)

    # RANDY fallback for archived plain SVGs.
    asset = randy_find_asset(
        job_id,
        pdb=pdb,
        chain=chain,
        ligand=warhead,
        resid=(request.args.get("resid") or ""),
        kind="svg",
        plain=True,
    )
    if asset:
        proxied = randy_proxy_file_response(
            job_id,
            asset.get("relative_path", ""),
            mimetype="image/svg+xml",
        )
        if proxied:
            return proxied

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
    if fp and fp.exists():
        return send_file(
            fp,
            mimetype="chemical/x-pdb",
            as_attachment=False,
            download_name=f"{pdb}_{chain}_{warhead}.pdb"
        )

    # RANDY fallback for old jobs no longer present on Heroku local disk.
    asset = randy_find_asset(job_id, pdb=pdb, chain=chain, ligand=warhead, kind="pdb")
    if asset:
        proxied = randy_proxy_file_response(
            job_id,
            asset.get("relative_path", ""),
            mimetype="chemical/x-pdb",
        )
        if proxied:
            return proxied

    abort(404, description="PDB not found")


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
    pdb_id = (request.args.get("pdb_id") or request.args.get("pdb") or "").strip().lower()
    chain = (request.args.get("chain") or "A").strip().upper()
    resid = (request.args.get("resid") or request.args.get("residue_id") or "").strip()

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

    def metadata_has_descriptors(d: Dict[str, Any]) -> bool:
        required = [
            "QED",
            "MW",
            "LogP",
            "TPSA",
            "HBA",
            "HBD",
            "Rotatable_Bonds",
            "Ring_Count",
            "Aromatic_Rings",
        ]
        for key in required:
            value = d.get(key)
            if value not in (None, "", "None"):
                return True
        return False

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

    def coerce_props(d: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(d)
        bool_keys = {"Lipinski_Pass", "Veber_Pass", "Ghose_Pass", "Muegge_Pass", "Egan_Pass"}
        int_keys = {"HBA", "HBD", "Rotatable_Bonds", "Ring_Count", "Aromatic_Rings"}
        float_keys = {"QED", "MW", "LogP", "TPSA"}

        for key in bool_keys:
            value = out.get(key)
            if isinstance(value, str):
                low = value.strip().lower()
                if low in {"true", "1"}:
                    out[key] = True
                elif low in {"false", "0"}:
                    out[key] = False

        for key in int_keys:
            value = out.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    out[key] = int(float(value))
                except Exception:
                    pass

        for key in float_keys:
            value = out.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    out[key] = float(value)
                except Exception:
                    pass
        return out

    def resolve_smiles_from_job_context() -> str:
        if smiles:
            return smiles

        candidates = [
            target_results_dir(job_id) / "Resolved_SASA_Summary.csv",
            job_root(job_id) / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
            job_root(job_id) / "Resolved_SASA_Summary.csv",
            target_results_dir(job_id) / "Results_Display.csv",
            job_root(job_id) / "TARGET_RESULTS" / "Results_Display.csv",
            job_root(job_id) / "Results_Display.csv",
        ]

        for path in candidates:
            if not path or not path.exists():
                continue
            try:
                df = pd.read_csv(path, dtype=str).fillna("")
            except Exception:
                continue
            if df.empty:
                continue

            ligand_cols = [c for c in df.columns if c.lower() in ("ligand_resolved", "warhead", "ligand", "resname", "ligand_code")]
            smiles_cols = [c for c in df.columns if c.lower() in ("smiles", "canonical_smiles", "parent_smiles")]
            if not ligand_cols or not smiles_cols:
                continue

            mask = pd.Series(True, index=df.index)
            ligand_mask = pd.Series(False, index=df.index)
            for col in ligand_cols:
                ligand_mask = ligand_mask | (df[col].astype(str).str.upper().str.strip() == ligand_code_u)
            mask = mask & ligand_mask

            if pdb_id:
                pdb_cols = [c for c in df.columns if c.lower() in ("pdb_id", "pdb")]
                if pdb_cols:
                    pdb_mask = pd.Series(False, index=df.index)
                    for col in pdb_cols:
                        pdb_mask = pdb_mask | (df[col].astype(str).str.lower().str.strip() == pdb_id)
                    mask = mask & pdb_mask

            if chain:
                chain_cols = [c for c in df.columns if c.lower() == "chain"]
                if chain_cols:
                    chain_mask = pd.Series(False, index=df.index)
                    for col in chain_cols:
                        chain_mask = chain_mask | (df[col].astype(str).str.upper().str.strip() == chain)
                    mask = mask & chain_mask

            if resid:
                resid_norm = str(resid).strip()
                resid_cols = [c for c in df.columns if c.lower() in ("residue_id", "resid")]
                if resid_cols:
                    resid_mask = pd.Series(False, index=df.index)
                    for col in resid_cols:
                        vals = df[col].astype(str).str.strip()
                        resid_mask = resid_mask | (vals == resid_norm) | (vals == f"{resid_norm}.0")
                    mask = mask & resid_mask

            subset = df[mask]
            if subset.empty:
                continue

            row = subset.iloc[0].to_dict()
            for col in smiles_cols:
                value = str(row.get(col) or "").strip()
                if value:
                    return value
        return ""

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
                d = coerce_props(d)
                if metadata_has_descriptors(d):
                    return jsonify(d)

    # ---------- 2) Fallback: compute from SMILES ----------
    resolved_smiles = resolve_smiles_from_job_context()
    if resolved_smiles:
        d = compute_from_smiles(resolved_smiles)
        if d:
            d["Ligand"] = ligand_code_u
            if pdb_id:
                d["pdb_id"] = pdb_id
            if chain:
                d["Chain"] = chain
            if resid:
                d["Residue_ID"] = resid
            return jsonify(d)

    return jsonify({
        "ok": False,
        "error": "Ligand properties unavailable",
        "job_id": job_id,
        "ligand_code": ligand_code_u,
        "pdb_id": pdb_id or None,
        "chain": chain or None,
        "resid": resid or None,
        "metadata_found": bool(meta and meta.exists()),
    })




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
    if not _safe_job_id(job_id):
        return jsonify({"ok": False, "error": "Invalid job_id"}), 400

    pdb_n = str(pdb).lower().strip()
    ligand_n = str(ligand).upper().strip()
    chain_n = (chain or infer_chain_from_results(job_id, pdb_n, ligand_n) or "A").upper().strip()

    resid = (request.args.get("resid") or request.args.get("residue_id") or "").strip()
    fp, diag = resolve_sdf_path(job_root(job_id), pdb_n, chain_n, ligand_n, resid)
    if fp:
        if diag.get("ambiguous"):
            app.logger.warning(
                "Ambiguous SDF match for %s/%s/%s/%s: %s",
                job_id, pdb_n, chain_n, ligand_n, diag.get("matching_candidates")
            )
        return send_file(fp, mimetype="chemical/x-mdl-sdfile")

    # RANDY fallback for archived jobs.
    asset = randy_find_asset(
        job_id,
        pdb=pdb_n,
        chain=chain_n,
        ligand=ligand_n,
        resid=resid,
        kind="sdf",
    )
    if asset:
        proxied = randy_proxy_file_response(
            job_id,
            asset.get("relative_path", ""),
            mimetype="chemical/x-mdl-sdfile",
        )
        if proxied:
            return proxied

    return jsonify({
        "ok": False,
        "error": "SDF not found",
        **diag,
    }), 404



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
    if fp:
        df = pd.read_csv(fp, dtype=str).fillna("")
    else:
        df = randy_get_table_dataframe(
            job_id,
            ["Warhead_SASA_atoms.csv", "Ligand_3D_Atoms_with_SASA.csv"]
        )
        if df is None or df.empty:
            return jsonify([]), 200
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

    rows = []
    if csv_path:
        with csv_path.open("r", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        df = randy_get_table_dataframe(
            job_id,
            ["3DSASAmapped.csv", "Ligand_3D_Atoms_with_SASA.csv", "3DSASA_MASTER.csv"]
        )
        if df is None or df.empty:
            return jsonify([]), 200
        rows = df.fillna("").to_dict(orient="records")

    out = []
    for row in rows:
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
