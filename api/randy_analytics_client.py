"""Small authenticated client for Warhead Hunter analytics events on RANDY."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests


def _base_url() -> str:
    for raw in (os.getenv("RANDY_ANALYTICS_BASE_URL", ""), os.getenv("RANDY_BACKUP_BASE_URL", ""), os.getenv("RANDY_ARCHIVE_BASE_URL", "")):
        value = str(raw or "").strip().rstrip("/")
        if value:
            return value if value.endswith("/backup") else f"{value}/backup"
    return ""


def _token() -> str:
    return (
        os.getenv("RANDY_ANALYTICS_TOKEN", "").strip()
        or os.getenv("RANDY_BACKUP_TOKEN", "").strip()
        or os.getenv("RANDY_ARCHIVE_TOKEN", "").strip()
        or os.getenv("PROTAC_BACKUP_TOKEN", "").strip()
    )


def analytics_enabled() -> bool:
    return bool(_base_url() and _token())


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "User-Agent": "warhead-hunter-analytics/1.0"}


def emit_event(payload: Dict[str, Any]) -> bool:
    """Best-effort telemetry: analytics must never fail a scientific job."""
    if not analytics_enabled():
        return False
    try:
        response = requests.post(f"{_base_url()}/hunter-analytics-event", json=payload, headers=_headers(), timeout=8)
        return bool(response.ok and response.json().get("ok"))
    except Exception:
        return False


def get_overview(days: int = 30) -> Optional[Dict[str, Any]]:
    if not analytics_enabled():
        return None
    try:
        response = requests.get(
            f"{_base_url()}/analytics/hunter/overview",
            params={"days": max(1, min(int(days), 3650))}, headers=_headers(), timeout=12,
        )
        payload = response.json()
        return payload if response.ok and payload.get("ok") else None
    except Exception:
        return None
