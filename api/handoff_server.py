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
# Heroku-friendly path:
#   1) Try to resolve PDB/SDF/SVG files from the local dyno job folder.
#   2) Upload those resolved files to RANDY via HTTPS Funnel.
#   3) If the local dyno job folder is missing/incomplete, ask RANDY to
#      materialize the requested handoff from a previously backed-up full job ZIP.
#
# Required on Heroku:
#   WARHEAD_HANDOFF_STORAGE_URL=https://randy.rove-vernier.ts.net/backup/hunter-job-files
#   WARHEAD_HANDOFF_TOKEN=<rotated token>
# Optional:
#   WARHEAD_HANDOFF_MATERIALIZE_URL=https://randy.rove-vernier.ts.net/backup/hunter-job-materialize
#   WARHEAD_HANDOFF_TIMEOUT_SECONDS=20
#   WARHEAD_HANDOFF_MODE=https

HANDOFF_MODE = os.getenv("WARHEAD_HANDOFF_MODE", "auto").strip().lower() or "auto"
STORAGE_URL = os.getenv("WARHEAD_HANDOFF_STORAGE_URL", "").strip()
HANDOFF_TOKEN = os.getenv("WARHEAD_HANDOFF_TOKEN", "").strip()
HANDOFF_TIMEOUT_SECONDS = float(os.getenv("WARHEAD_HANDOFF_TIMEOUT_SECONDS", "20") or 20)

# If not provided explicitly, infer from /backup/hunter-job-files.
_DEFAULT_MATERIALIZE_URL = ""
if STORAGE_URL.endswith("/backup/hunter-job-files"):
    _DEFAULT_MATERIALIZE_URL = STORAGE_URL[: -len("/backup/hunter-job-files")] + "/backup/hunter-job-materialize"
MATERIALIZE_URL = os.getenv("WARHEAD_HANDOFF_MATERIALIZE_URL", _DEFAULT_MATERIALIZE_URL).strip()

# Legacy SSH/SCP mode remains available for local/CARTMAN runs.
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
    matches = sorted({p.resolve() for p in matches if p.exists()}, key=lambda p: str(p))
    if not matches:
        raise FileNotFoundError(f"{what} not found")
    return matches[0]


def _existing_roots(jobs_dir: Path, job_id: str, rel_roots: List[str]) -> List[Path]:
    roots = []
    for rel in rel_roots:
        root = jobs_dir / job_id / rel
        if root.exists():
            roots.append(root)
    return roots


def find_pdb(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str) -> Path:
    roots = _existing_roots(jobs_dir, job_id, [
        "TARGET_RESULTS/WAR_PDB",
        "WAR_PDB",
    ])
    if not roots:
        raise FileNotFoundError(f"WAR_PDB directory not found for job {job_id}; checked TARGET_RESULTS/WAR_PDB and WAR_PDB")
    fname = f"{pdb}_{chain}_{warhead}.pdb"
    matches: List[Path] = []
    for root in roots:
        matches.extend(root.rglob(fname))
    return _pick_first(matches, f"PDB {fname}")


def find_sdf(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str, resid: Optional[str]) -> Path:
    roots = _existing_roots(jobs_dir, job_id, [
        "MCS_Output/MCS_SDF",
        "TARGET_RESULTS/MCS_Output/MCS_SDF",
        "LIGAND_SDF",
        "TARGET_RESULTS/LIGAND_SDF",
    ])
    if not roots:
        raise FileNotFoundError(f"No SDF directory found for job {job_id}")

    patterns: List[str] = []
    if resid:
        patterns.append(f"{pdb}_{chain}_{warhead}_{resid}.sdf")
    patterns.extend([
        f"{pdb}_{chain}_{warhead}_*.sdf",
        f"{pdb}_{chain}_{warhead}.sdf",
    ])

    matches: List[Path] = []
    for root in roots:
        for pattern in patterns:
            matches.extend(root.glob(pattern))
    return _pick_first(matches, f"SDF for {pdb}_{chain}_{warhead} resid={resid or ''}")


def find_svgs(jobs_dir: Path, job_id: str, pdb: str, chain: str, warhead: str, resid: Optional[str]) -> List[Path]:
    roots = _existing_roots(jobs_dir, job_id, [
        "TARGET_RESULTS/MCS_Output/MCS_SVG",
        "MCS_Output/MCS_SVG",
        "TARGET_RESULTS/MCS_Output/MCS_SVGS",
        "MCS_Output/MCS_SVGS",
    ])
    if not roots:
        raise FileNotFoundError(f"No MCS SVG directory found for job {job_id}")

    patterns: List[str] = []
    if resid:
        patterns.append(f"{pdb}_{chain}_{warhead}_{resid}_*.svg")
    patterns.extend([
        f"{pdb}_{chain}_{warhead}_*_*.svg",
        f"{pdb}_{chain}_{warhead}*.svg",
    ])

    matches: List[Path] = []
    for root in roots:
        for pattern in patterns:
            matches.extend(root.glob(pattern))
    matches = sorted({p.resolve() for p in matches if p.exists()}, key=lambda p: str(p))
    if not matches:
        raise FileNotFoundError(f"No SVGs found for {pdb}_{chain}_{warhead} resid={resid or ''}")
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
        for _field_label, path in files_to_send:
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


def _materialize_stored_job_via_https(*, job_id: str, pdb: str, chain: str, warhead: str, resid: Optional[str], local_error: str) -> Dict[str, Any]:
    """Ask RANDY to locate/copy the selected artifacts from its backed-up full job archive."""
    if not MATERIALIZE_URL:
        raise RuntimeError(f"Local handoff lookup failed and WARHEAD_HANDOFF_MATERIALIZE_URL is not configured. Local error: {local_error}")
    if not HANDOFF_TOKEN:
        raise RuntimeError("WARHEAD_HANDOFF_TOKEN is not configured")

    payload = {
        "job_id": job_id,
        "pdb": pdb,
        "chain": chain,
        "warhead": warhead,
        "resid": resid or "",
        "source": "protac-builder-button",
        "local_lookup_error": local_error,
    }
    resp = requests.post(
        MATERIALIZE_URL,
        headers={"Authorization": f"Bearer {HANDOFF_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=HANDOFF_TIMEOUT_SECONDS,
    )
    try:
        data = resp.json()
    except Exception:
        data = {"ok": False, "raw_response": resp.text[:1000]}
    if resp.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(f"RANDY stored-job materialize failed after local lookup failed: local={local_error}; HTTP {resp.status_code} {data}")
    return data


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

    return {"ok": True, "mode": "ssh", "remote_dir": str(remote_job_dir), "files": manifest["files"]}


@hand_bp.get("/config")
def handoff_config():
    mode = _effective_mode()
    return jsonify({
        "ok": True,
        "mode": mode,
        "storage_url_configured": bool(STORAGE_URL),
        "storage_url": STORAGE_URL if STORAGE_URL else "",
        "materialize_url_configured": bool(MATERIALIZE_URL),
        "materialize_url": MATERIALIZE_URL if MATERIALIZE_URL else "",
        "token_configured": bool(HANDOFF_TOKEN),
        "timeout_seconds": HANDOFF_TIMEOUT_SECONDS,
        "legacy_remote_host": REMOTE_HOST,
        "legacy_remote_base": str(REMOTE_BASE),
        "ssh_opts_enabled": bool(REMOTE_SSH_OPTS),
        "fallback": "If local Heroku job files are gone, this route asks RANDY to materialize from the backed-up full job archive.",
    })


# -----------------------------------------------------------------------------
# MAIN ENDPOINT
# -----------------------------------------------------------------------------
@hand_bp.route("/materialize/<job_id>/<pdb>/<chain>/<warhead>", methods=["POST"])
def materialize_hunter_job(job_id, pdb, chain, warhead):
    try:
        job_id = safe_job_id(job_id)
        pdb = _safe_text(pdb).lower()
        chain = _safe_text(chain).upper()
        warhead = _safe_text(warhead).upper()
        resid = _safe_text(request.args.get("resid")) or None
        jobs_dir = Path(current_app.config.get("JOBS_DIR", "jobs")).resolve()

        try:
            src_pdb = find_pdb(jobs_dir, job_id, pdb, chain, warhead)
            src_sdf = find_sdf(jobs_dir, job_id, pdb, chain, warhead, resid=resid)
            src_svgs = find_svgs(jobs_dir, job_id, pdb, chain, warhead, resid=resid)
        except Exception as local_error:
            if _effective_mode() == "https":
                materialized = _materialize_stored_job_via_https(
                    job_id=job_id,
                    pdb=pdb,
                    chain=chain,
                    warhead=warhead,
                    resid=resid,
                    local_error=str(local_error),
                )
                return jsonify({
                    "ok": True,
                    "mode": "https-stored-job-fallback",
                    "remote_dir": materialized.get("remote_dir", ""),
                    "files": materialized.get("files", {}),
                    "saved_files": materialized.get("saved_files", []),
                    "event_id": materialized.get("event_id"),
                    "resid": resid,
                    "local_lookup_error": str(local_error),
                })
            raise

        manifest = _build_manifest(job_id=job_id, pdb=pdb, chain=chain, warhead=warhead, resid=resid, src_pdb=src_pdb, src_sdf=src_sdf, src_svgs=src_svgs)
        files_to_send: List[Tuple[str, Path]] = [("pdb", src_pdb), ("sdf", src_sdf)] + [("svg", p) for p in src_svgs]

        if _effective_mode() == "https":
            uploaded = _materialize_via_https(manifest, files_to_send)
            return jsonify({
                "ok": True,
                "mode": "https-local-upload",
                "storage_url": STORAGE_URL,
                "remote_dir": uploaded.get("remote_dir", ""),
                "files": manifest["files"],
                "saved_files": uploaded.get("saved_files", []),
                "event_id": uploaded.get("event_id"),
                "resid": resid,
            })

        uploaded = _materialize_via_ssh(manifest, files_to_send)
        return jsonify({"ok": True, "mode": "ssh", "remote_dir": uploaded.get("remote_dir", ""), "files": manifest["files"], "resid": resid})

    except Exception as e:
        import traceback
        traceback.print_exc()
        abort(500, str(e))
