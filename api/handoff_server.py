#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import requests
from flask import Blueprint, current_app, abort, jsonify, request

hand_bp = Blueprint("handoff", __name__, url_prefix="/api/handoff")

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
# Heroku-friendly mode: POST resolved job artifacts to RANDY through HTTPS Funnel.
# Required on Heroku:
#   WARHEAD_HANDOFF_STORAGE_URL=https://randy.rove-vernier.ts.net/backup/hunter-job-files
#   WARHEAD_HANDOFF_TOKEN=<rotated token>
# Optional:
#   WARHEAD_HANDOFF_TIMEOUT_SECONDS=20
#
# Legacy SSH/SCP mode remains available for local/CARTMAN runs if no storage URL
# is configured or if WARHEAD_HANDOFF_MODE=ssh is set explicitly.

HANDOFF_MODE = os.getenv("WARHEAD_HANDOFF_MODE", "auto").strip().lower() or "auto"
STORAGE_URL = os.getenv("WARHEAD_HANDOFF_STORAGE_URL", "").strip()
HANDOFF_TOKEN = os.getenv("WARHEAD_HANDOFF_TOKEN", "").strip()
HANDOFF_TIMEOUT_SECONDS = float(os.getenv("WARHEAD_HANDOFF_TIMEOUT_SECONDS", "20") or 20)

REMOTE_HOST = os.getenv("WARHEAD_HANDOFF_REMOTE_HOST", "randy").strip() or "randy"
REMOTE_BASE = Path(
    os.getenv(
        "WARHEAD_HANDOFF_REMOTE_BASE",
        "/home/jxs794/PROTAC_BUILDER/warhead_hunter/hunter_jobs",
    ).strip()
)
REMOTE_SSH_OPTS = shlex.split(os.getenv("WARHEAD_HANDOFF_SSH_OPTS", ""))


def _effective_mode() -> str:
    if HANDOFF_MODE in {"https", "http"}:
        return "https"
    if HANDOFF_MODE == "ssh":
        return "ssh"
    if STORAGE_URL:
        return "https"
    return "ssh"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def safe_job_id(job_id: str) -> str:
    if not job_id or "/" in job_id or ".." in job_id or "\\" in job_id:
        abort(400, "Invalid job_id")
    return job_id


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cmd: List[str]) -> str:
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"Command failed: {' '.join(cmd)}")
    return (r.stdout or "").strip()


def ssh(cmd: str) -> str:
    return run(["ssh", *REMOTE_SSH_OPTS, REMOTE_HOST, cmd])


def scp(src: Path, dst_remote_path: str) -> None:
    run(["scp", *REMOTE_SSH_OPTS, str(src), f"{REMOTE_HOST}:{dst_remote_path}"])


def _pick_first(matches: List[Path], what: str) -> Path:
    if not matches:
        abort(404, f"{what} not found")
    return sorted(matches, key=lambda p: str(p))[0]


def find_pdb(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str) -> Path:
    base = jobs_dir / job_id / "TARGET_RESULTS" / "WAR_PDB"
    if not base.exists():
        abort(404, f"WAR_PDB directory not found: {base}")
    fname = f"{pdb}_{chain}_{warhead}.pdb"
    matches = list(base.rglob(fname))
    if not matches:
        abort(404, f"Source PDB not found under WAR_PDB: {fname}")
    return _pick_first(matches, "PDB")


def find_sdf(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str, resid: Optional[str]) -> Path:
    roots = [
        jobs_dir / job_id / "MCS_Output" / "MCS_SDF",
        jobs_dir / job_id / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF",
        jobs_dir / job_id / "LIGAND_SDF",
        jobs_dir / job_id / "TARGET_RESULTS" / "LIGAND_SDF",
    ]
    existing_roots = [root for root in roots if root.exists()]
    if not existing_roots:
        abort(404, f"No SDF directory found for job {job_id}")

    patterns = []
    if resid:
        patterns.append(f"{pdb}_{chain}_{warhead}_{resid}.sdf")
    patterns.append(f"{pdb}_{chain}_{warhead}_*.sdf")
    patterns.append(f"{pdb}_{chain}_{warhead}.sdf")

    matches: List[Path] = []
    for root in existing_roots:
        for pattern in patterns:
            matches.extend(list(root.glob(pattern)))
    if not matches:
        abort(404, f"Source SDF not found for {pdb}_{chain}_{warhead} resid={resid or ''}")
    return _pick_first(matches, "SDF")


def find_svgs(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str, resid: Optional[str]) -> List[Path]:
    roots = [
        jobs_dir / job_id / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG",
        jobs_dir / job_id / "MCS_Output" / "MCS_SVG",
        jobs_dir / job_id / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVGS",
        jobs_dir / job_id / "MCS_Output" / "MCS_SVGS",
    ]
    existing_roots = [root for root in roots if root.exists()]
    if not existing_roots:
        abort(404, f"No MCS SVG directory found for job {job_id}")

    patterns = []
    if resid:
        patterns.append(f"{pdb}_{chain}_{warhead}_{resid}_*.svg")
    patterns.append(f"{pdb}_{chain}_{warhead}_*_*.svg")
    patterns.append(f"{pdb}_{chain}_{warhead}*.svg")

    matches: List[Path] = []
    for root in existing_roots:
        for pattern in patterns:
            matches.extend(list(root.glob(pattern)))
    matches = sorted(set(matches), key=lambda p: str(p))
    if not matches:
        abort(404, f"No SVGs found for {pdb}_{chain}_{warhead} resid={resid or ''}")
    return matches


def ensure_remote_dir(remote_dir: Path) -> None:
    ssh(f"mkdir -p {shlex.quote(str(remote_dir))}")


def _build_manifest(
    *,
    job_id: str,
    pdb: str,
    chain: str,
    warhead: str,
    resid: Optional[str],
    src_pdb: Path,
    src_sdf: Path,
    src_svgs: List[Path],
) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "pdb": pdb,
        "chain": chain,
        "warhead": warhead,
        "resid": resid,
        "created_at_utc": _utc_now_iso(),
        "files": {
            "pdb": src_pdb.name,
            "sdf": src_sdf.name,
            "svg": [p.name for p in src_svgs],
        },
        "source_paths": {
            "pdb": str(src_pdb),
            "sdf": str(src_sdf),
            "svg": [str(p) for p in src_svgs],
        },
        "source": "protac-builder-button",
        "handoff_mode": _effective_mode(),
    }


def _materialize_via_https(manifest: Dict[str, Any], files_to_send: List[Tuple[str, Path]]) -> Dict[str, Any]:
    if not STORAGE_URL:
        raise RuntimeError("WARHEAD_HANDOFF_STORAGE_URL is not configured")
    if not HANDOFF_TOKEN:
        raise RuntimeError("WARHEAD_HANDOFF_TOKEN is not configured")

    form = {
        "job_id": manifest["job_id"],
        "pdb": manifest["pdb"],
        "chain": manifest["chain"],
        "warhead": manifest["warhead"],
        "resid": manifest.get("resid") or "",
        "source": manifest.get("source") or "protac-builder-button",
        "manifest_json": json.dumps(manifest, sort_keys=True),
    }

    handles = []
    multipart = []
    try:
        for field_label, path in files_to_send:
            handle = path.open("rb")
            handles.append(handle)
            multipart.append(("files", (path.name, handle, "application/octet-stream")))

        resp = requests.post(
            STORAGE_URL,
            headers={"Authorization": f"Bearer {HANDOFF_TOKEN}"},
            data=form,
            files=multipart,
            timeout=HANDOFF_TIMEOUT_SECONDS,
        )
        try:
            payload = resp.json()
        except Exception:
            payload = {"ok": False, "raw_response": resp.text[:1000]}
        if resp.status_code >= 400 or not payload.get("ok"):
            raise RuntimeError(f"RANDY handoff upload failed: HTTP {resp.status_code} {payload}")
        return payload
    finally:
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass


def _materialize_via_ssh(manifest: Dict[str, Any], files_to_send: List[Tuple[str, Path]]) -> Dict[str, Any]:
    job_id = manifest["job_id"]
    remote_job_dir = REMOTE_BASE / job_id
    ensure_remote_dir(remote_job_dir)

    for _label, path in files_to_send:
        scp(path, str(remote_job_dir / path.name))

    tmp_manifest = Path("/tmp") / f"hunter_manifest_{job_id}_{manifest['pdb']}_{manifest['chain']}_{manifest['warhead']}.json"
    tmp_manifest.write_text(json.dumps({**manifest, "remote_host": REMOTE_HOST, "remote_base": str(REMOTE_BASE), "remote_dir": str(remote_job_dir)}, indent=2), encoding="utf-8")
    try:
        scp(tmp_manifest, str(remote_job_dir / "manifest.json"))
    finally:
        tmp_manifest.unlink(missing_ok=True)

    return {
        "ok": True,
        "mode": "ssh",
        "remote_dir": str(remote_job_dir),
        "files": manifest["files"],
    }


@hand_bp.get("/config")
def handoff_config():
    mode = _effective_mode()
    return jsonify({
        "ok": True,
        "mode": mode,
        "storage_url_configured": bool(STORAGE_URL),
        "storage_url": STORAGE_URL if STORAGE_URL else "",
        "token_configured": bool(HANDOFF_TOKEN),
        "timeout_seconds": HANDOFF_TIMEOUT_SECONDS,
        "legacy_remote_host": REMOTE_HOST,
        "legacy_remote_base": str(REMOTE_BASE),
        "ssh_opts_enabled": bool(REMOTE_SSH_OPTS),
    })


# -----------------------------------------------------------------------------
# MAIN ENDPOINT
# -----------------------------------------------------------------------------
@hand_bp.route("/materialize/<job_id>/<pdb>/<chain>/<warhead>", methods=["POST"])
def materialize_hunter_job(job_id, pdb, chain, warhead):
    """
    Resolve PDB/SDF/SVG artifacts from a Warhead Hunter job directory and store
    them persistently on RANDY.

    Heroku/default path:
      POST multipart files to WARHEAD_HANDOFF_STORAGE_URL over HTTPS Funnel.

    Legacy/local fallback:
      SSH/SCP to WARHEAD_HANDOFF_REMOTE_HOST when WARHEAD_HANDOFF_MODE=ssh or no
      storage URL is configured.
    """
    try:
        job_id = safe_job_id(job_id)
        pdb = _safe_text(pdb).lower()
        chain = _safe_text(chain).upper()
        warhead = _safe_text(warhead).upper()
        resid = _safe_text(request.args.get("resid")) or None

        jobs_dir = Path(current_app.config.get("JOBS_DIR", "jobs")).resolve()

        src_pdb = find_pdb(jobs_dir, job_id, pdb, chain, warhead)
        src_sdf = find_sdf(jobs_dir, job_id, pdb, chain, warhead, resid=resid)
        src_svgs = find_svgs(jobs_dir, job_id, pdb, chain, warhead, resid=resid)

        manifest = _build_manifest(
            job_id=job_id,
            pdb=pdb,
            chain=chain,
            warhead=warhead,
            resid=resid,
            src_pdb=src_pdb,
            src_sdf=src_sdf,
            src_svgs=src_svgs,
        )
        files_to_send: List[Tuple[str, Path]] = [("pdb", src_pdb), ("sdf", src_sdf)] + [("svg", p) for p in src_svgs]

        if _effective_mode() == "https":
            uploaded = _materialize_via_https(manifest, files_to_send)
            return jsonify({
                "ok": True,
                "mode": "https",
                "storage_url": STORAGE_URL,
                "remote_dir": uploaded.get("remote_dir", ""),
                "files": manifest["files"],
                "saved_files": uploaded.get("saved_files", []),
                "resid": resid,
            })

        uploaded = _materialize_via_ssh(manifest, files_to_send)
        return jsonify({
            "ok": True,
            "mode": "ssh",
            "remote_dir": uploaded.get("remote_dir", ""),
            "files": manifest["files"],
            "resid": resid,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        abort(500, str(e))
