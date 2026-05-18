#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional, List

from flask import Blueprint, current_app, abort, jsonify, request

hand_bp = Blueprint("handoff", __name__, url_prefix="/api/handoff")

# -----------------------------------------------------------------------------
# CONFIG (single source of truth)
# -----------------------------------------------------------------------------
KYLE_HOST = "kyle"
KYLE_BASE = Path("/home/jxs794/VLISEMOD/static/hunter_jobs")

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def safe_job_id(job_id: str) -> str:
    if not job_id or "/" in job_id or ".." in job_id or "\\" in job_id:
        abort(400, "Invalid job_id")
    return job_id


def run(cmd: List[str]) -> str:
    """
    Run a subprocess, fail loudly and clearly.
    """
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"Command failed: {' '.join(cmd)}")
    return (r.stdout or "").strip()


def ssh(cmd: str) -> str:
    """
    Run a command on KYLE via SSH.
    """
    return run(["ssh", KYLE_HOST, cmd])


def scp(src: Path, dst_remote_path: str) -> None:
    """
    SCP a file to KYLE.
    dst_remote_path should be an absolute path on KYLE.
    """
    run(["scp", str(src), f"{KYLE_HOST}:{dst_remote_path}"])


def _pick_first(matches: List[Path], what: str) -> Path:
    if not matches:
        abort(404, f"{what} not found")
    # deterministic: stable sort
    matches = sorted(matches, key=lambda p: str(p))
    return matches[0]


def find_pdb(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str) -> Path:
    """
    PDBs live here (per your tree):
      jobs/<job_id>/TARGET_RESULTS/WAR_PDB/<TARGET>/<pdb>_<chain>_<warhead>.pdb
    We don't assume TARGET; we rglob().
    """
    base = jobs_dir / job_id / "TARGET_RESULTS" / "WAR_PDB"
    if not base.exists():
        abort(404, f"WAR_PDB directory not found: {base}")

    fname = f"{pdb}_{chain}_{warhead}.pdb"
    matches = list(base.rglob(fname))
    if not matches:
        abort(404, f"Source PDB not found (expected under WAR_PDB): {fname}")
    return _pick_first(matches, "PDB")


def find_sdf(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str, resid: Optional[str]) -> Path:
    """
    SDFs live here (per your tree):
      jobs/<job_id>/MCS_Output/MCS_SDF/<pdb>_<chain>_<warhead>_<resid>.sdf

    If resid is missing, we fall back to the first match:
      <pdb>_<chain>_<warhead>_*.sdf
    """
    base = jobs_dir / job_id / "MCS_Output" / "MCS_SDF"
    if not base.exists():
        abort(404, f"MCS_SDF directory not found: {base}")

    if resid:
        fname = f"{pdb}_{chain}_{warhead}_{resid}.sdf"
        matches = list(base.glob(fname))
        if not matches:
            abort(404, f"Source SDF not found: {fname}")
        return matches[0]

    # fallback: any resid
    pattern = f"{pdb}_{chain}_{warhead}_*.sdf"
    matches = list(base.glob(pattern))
    if not matches:
        abort(404, f"Source SDF not found (pattern): {pattern}")
    return _pick_first(matches, "SDF")



def find_svgs(
    jobs_dir: Path,
    job_id: str,
    pdb: str,
    chain: str,
    warhead: str,
    resid: Optional[str]
) -> List[Path]:
    """
    SVGs live here:
      jobs/<job_id>/TARGET_RESULTS/MCS_Output/MCS_SVG/

    Expected:
      <pdb>_<chain>_<warhead>_<resid>_{plain|exposed}.svg

    If resid missing → fallback to any matching resid.
    """
    base = jobs_dir / job_id / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG"
    if not base.exists():
        abort(404, f"MCS_SVG directory not found: {base}")

    if resid:
        pattern = f"{pdb}_{chain}_{warhead}_{resid}_*.svg"
    else:
        pattern = f"{pdb}_{chain}_{warhead}_*_*.svg"

    matches = sorted(base.glob(pattern))
    if not matches:
        abort(404, f"No SVGs found (pattern): {pattern}")

    return matches




def ensure_remote_dir(remote_dir: Path) -> None:
    """
    Ensure the hunter job folder exists on KYLE.
    """
    # Quote path safely enough for your use case (no spaces expected)
    ssh(f"mkdir -p {remote_dir}")


# -----------------------------------------------------------------------------
# MAIN ENDPOINT
# -----------------------------------------------------------------------------
@hand_bp.route("/materialize/<job_id>/<pdb>/<chain>/<warhead>", methods=["POST"])
def materialize_hunter_job(job_id, pdb, chain, warhead):
    """
    Copies the job-specific PDB + SDF from CARTMAN job outputs to:
      kyle:/home/jxs794/VLISEMOD/static/hunter_jobs/<job_id>/

    Notes:
      - Does NOT depend on SMILES.
      - Uses filesystem search, matching your actual output layout.
      - Optional resid can be passed as query param ?resid=1101
    """
    try:
        job_id = safe_job_id(job_id)

        pdb = str(pdb).lower().strip()
        chain = str(chain).upper().strip()
        warhead = str(warhead).upper().strip()

        resid = (request.args.get("resid") or "").strip() or None

        jobs_dir = Path(current_app.config.get("JOBS_DIR", "jobs")).resolve()

        # ---------------------------------------------------------------------
        # 1) Resolve SOURCE files on CARTMAN (REAL locations)
        # ---------------------------------------------------------------------
        src_pdb = find_pdb(jobs_dir, job_id, pdb, chain, warhead)
        src_sdf = find_sdf(jobs_dir, job_id, pdb, chain, warhead, resid=resid)

        pdb_name = src_pdb.name
        sdf_name = src_sdf.name

        # ---------------------------------------------------------------------
        # 1b) Resolve SVGs (optional but expected)
        # ---------------------------------------------------------------------
        src_svgs = find_svgs(jobs_dir, job_id, pdb, chain, warhead, resid)
        svg_names = [p.name for p in src_svgs]


        # ---------------------------------------------------------------------
        # 2) Ensure DESTINATION directory exists on KYLE
        # ---------------------------------------------------------------------
        remote_job_dir = (KYLE_BASE / job_id)
        ensure_remote_dir(remote_job_dir)

        # ---------------------------------------------------------------------
        # 3) SCP files to KYLE
        # ---------------------------------------------------------------------
        scp(src_pdb, str(remote_job_dir / pdb_name))
        scp(src_sdf, str(remote_job_dir / sdf_name))

        # ---------------------------------------------------------------------
        # 3b) SCP SVGs
        # ---------------------------------------------------------------------
        for svg in src_svgs:
            scp(svg, str(remote_job_dir / svg.name))


        
        


        # ---------------------------------------------------------------------
        # 4) Write + SCP manifest
        # ---------------------------------------------------------------------
        manifest = {
            "job_id": job_id,
            "pdb": pdb,
            "chain": chain,
            "warhead": warhead,
            "resid": resid,
            "files": {
                "pdb": pdb_name,
                "sdf": sdf_name,
                "svg": svg_names
            },
            "source_paths": {
                "pdb": str(src_pdb),
                "sdf": str(src_sdf),
                "svg": [str(p) for p in src_svgs]
            },
            "remote_dir": str(remote_job_dir),
            "source": "protac-builder-button"
        }

        tmp_manifest = Path("/tmp") / f"hunter_manifest_{job_id}_{pdb}_{chain}_{warhead}.json"
        with open(tmp_manifest, "w") as f:
            json.dump(manifest, f, indent=2)

        scp(tmp_manifest, str(remote_job_dir / "manifest.json"))
        tmp_manifest.unlink(missing_ok=True)

        return jsonify({
            "ok": True,
            "remote_dir": str(remote_job_dir),
            "files": manifest["files"],
            "resid": resid
        })


    except Exception as e:
        import traceback
        traceback.print_exc()
        abort(500, str(e))
