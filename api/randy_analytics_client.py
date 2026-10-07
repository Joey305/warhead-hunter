"""Privacy-safe, best-effort client for the dedicated RANDY Hunter receiver."""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

import requests

ALLOWED_EVENT_TYPES = {
    "workflow_started", "results_viewed", "export_generated", "companion_handoff",
    "analysis_submitted", "analysis_started", "analysis_completed", "analysis_failed",
}
ALLOWED_FEATURES = {
    "hunter_launch", "rcsb_scout", "manual_upload", "api_docs", "job_monitor", "results_gallery",
    "candidate_structure", "candidate_2d_map", "archive_browse", "example_detail", "hunter_job",
    "sdf_download", "pdb_download", "job_bundle_download", "war_pdb_bundle_download", "job_index_csv",
    "api_artifact_download", "builder_from_results_gallery",
}
SAFE_FIELDS = {"event_id", "event_type", "feature", "occurred_at_utc", "visitor_id", "session_id", "route", "referrer_host", "device_type", "country_code", "country_name", "latitude", "longitude", "handoff_id", "runtime_seconds", "failure_stage", "structure_count", "pose_count", "unique_ligand_count", "total_ligand_atoms", "exposed_atom_count", "mean_percent_exposed", "high_exposure_pose_count", "archive_verified"}


def _base_url() -> str:
    for raw in (os.getenv("RANDY_ANALYTICS_BASE_URL", ""), os.getenv("RANDY_BACKUP_BASE_URL", ""), os.getenv("RANDY_ARCHIVE_BASE_URL", "")):
        value = str(raw or "").strip().rstrip("/")
        if value:
            return value if value.endswith("/backup") else f"{value}/backup"
    return ""


def _token() -> str:
    return os.getenv("RANDY_ANALYTICS_TOKEN", "").strip() or os.getenv("RANDY_BACKUP_TOKEN", "").strip() or os.getenv("RANDY_ARCHIVE_TOKEN", "").strip() or os.getenv("PROTAC_BACKUP_TOKEN", "").strip()


def analytics_enabled() -> bool:
    return bool(_base_url() and _token())


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "User-Agent": "warhead-hunter-analytics/2.0"}


def _sanitize(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("event_type") not in ALLOWED_EVENT_TYPES or payload.get("feature") not in ALLOWED_FEATURES:
        return None
    return {key: payload[key] for key in SAFE_FIELDS if key in payload}


def _post(payload: Dict[str, Any]) -> None:
    try:
        requests.post(f"{_base_url()}/warhead-hunter/analytics/events", json=payload, headers=_headers(), timeout=(0.5, 2.0))
    except Exception:
        pass


def emit_event(payload: Dict[str, Any]) -> bool:
    """Queue an already-sanitized primitive event without blocking product work."""
    safe = _sanitize(payload)
    if not safe or not analytics_enabled():
        return False
    threading.Thread(target=_post, args=(safe,), daemon=True, name="randy-hunter-analytics").start()
    return True


def get_overview(days: int = 30) -> Optional[Dict[str, Any]]:
    if not analytics_enabled():
        return None
    try:
        response = requests.get(f"{_base_url()}/warhead-hunter/analytics/rollup", params={"days": max(1, min(int(days), 3650))}, headers=_headers(), timeout=5)
        payload = response.json()
        return payload if response.ok and payload.get("ok") else None
    except Exception:
        return None
