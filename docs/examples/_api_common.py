#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests


DEFAULT_BASES = [
    "http://cartman.rove-vernier.ts.net",
    "https://warheadhunter.com",
]


def choose_base() -> str:
    env_base = (os.getenv("WARHEAD_API_BASE") or "").strip().rstrip("/")
    if env_base:
        return env_base

    for base in DEFAULT_BASES:
        try:
            response = requests.get(f"{base}/api/health", timeout=5)
            if response.ok:
                return base.rstrip("/")
        except requests.RequestException:
            pass

    raise RuntimeError("No Warhead Hunter API base is reachable. Set WARHEAD_API_BASE explicitly.")


def get_json(url: str, **kwargs: Any) -> Dict[str, Any]:
    response = requests.get(url, timeout=kwargs.pop("timeout", 30), **kwargs)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(json.dumps(payload, indent=2))
    return payload


def post_json(url: str, payload: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    response = requests.post(url, json=payload, timeout=kwargs.pop("timeout", 60), **kwargs)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(json.dumps(data, indent=2))
    return data


def download_file(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=300, stream=True) as response:
        response.raise_for_status()
        with output_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    handle.write(chunk)
    return output_path


def print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2))


def require_job_ids(argv: Iterable[str], fallback: List[str]) -> List[str]:
    values = [str(item).strip() for item in argv if str(item).strip()]
    return values or fallback


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)
