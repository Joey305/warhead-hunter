#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api/randy_archive_client.py

Small authenticated client used by Warhead Hunter to read old job artifacts
from the RANDY backup receiver.

Required Heroku config vars:
  RANDY_ARCHIVE_BASE_URL=https://randy.rove-vernier.ts.net/backup
  RANDY_ARCHIVE_TOKEN=<same token used by RANDY>

Fallback token env vars:
  WARHEAD_HANDOFF_TOKEN
  PROTAC_BACKUP_TOKEN
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote

import pandas as pd
import requests
from flask import Response


def _base_url() -> str:
    return os.environ.get("RANDY_ARCHIVE_BASE_URL", "").strip().rstrip("/")


def _token() -> str:
    return (
        os.environ.get("RANDY_ARCHIVE_TOKEN", "").strip()
        or os.environ.get("WARHEAD_HANDOFF_TOKEN", "").strip()
        or os.environ.get("PROTAC_BACKUP_TOKEN", "").strip()
    )


def archive_enabled() -> bool:
    return bool(_base_url() and _token())


def _headers() -> Dict[str, str]:
    token = _token()
    headers = {"User-Agent": "warhead-hunter-randy-archive-client/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _job_url(job_id: str) -> str:
    return f"{_base_url()}/hunter-job/{quote(str(job_id).strip(), safe='')}"


def _file_url(job_id: str, relative_path: str) -> str:
    rel = str(relative_path or "").strip().lstrip("/")
    return f"{_job_url(job_id)}/file/{quote(rel, safe='/')}"


@lru_cache(maxsize=512)
def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    if not archive_enabled():
        return None

    try:
        resp = requests.get(_job_url(job_id), headers=_headers(), timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            return None
        return payload
    except Exception:
        return None


def job_exists(job_id: str) -> bool:
    return bool(get_job(job_id))


def _norm(value: Any, upper: bool = False, lower: bool = False) -> str:
    s = str(value or "").strip()
    if upper:
        return s.upper()
    if lower:
        return s.lower()
    return s


def _candidate_files(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    files = payload.get("files")
    if isinstance(files, list):
        return [f for f in files if isinstance(f, dict)]
    return []


def _candidate_options(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    options = payload.get("options")
    if isinstance(options, list):
        return [o for o in options if isinstance(o, dict)]
    return []


def _option_to_asset(option: Dict[str, Any], kind: str, plain: Optional[bool]) -> Optional[Dict[str, Any]]:
    if kind == "pdb":
        name = option.get("pdb_file")
    elif kind == "sdf":
        name = option.get("sdf")
    elif kind == "svg":
        name = option.get("svg_plain") if plain else option.get("svg_exposed")
    else:
        name = None

    if not name:
        return None

    return {
        "name": Path(str(name)).name,
        "filename": Path(str(name)).name,
        "relative_path": str(name).lstrip("/"),
        "source": "randy_options",
        "option": option,
    }


def find_asset(
    job_id: str,
    *,
    pdb: str = "",
    chain: str = "",
    ligand: str = "",
    resid: str = "",
    kind: str,
    plain: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """
    Find one archived asset from RANDY's job index.

    kind:
      pdb | sdf | svg
    plain:
      only meaningful for kind='svg'
    """
    payload = get_job(job_id)
    if not payload:
        return None

    pdb_l = _norm(pdb, lower=True)
    chain_u = _norm(chain, upper=True)
    ligand_u = _norm(ligand, upper=True)
    resid_s = _norm(resid)

    # Prefer explicit option metadata, when available.
    for opt in _candidate_options(payload):
        if pdb_l and _norm(opt.get("pdb"), lower=True) != pdb_l:
            continue
        if chain_u and _norm(opt.get("chain"), upper=True) != chain_u:
            continue
        if ligand_u and _norm(opt.get("ligand"), upper=True) != ligand_u:
            continue
        if resid_s and kind != "pdb" and _norm(opt.get("resid")) not in {"", resid_s}:
            continue

        asset = _option_to_asset(opt, kind, plain)
        if asset:
            return asset

    # Fallback to a generic files list if RANDY returns one.
    suffix = {"pdb": ".pdb", "sdf": ".sdf", "svg": ".svg"}.get(kind)
    if not suffix:
        return None

    for item in _candidate_files(payload):
        rel = str(item.get("relative_path") or item.get("filename") or item.get("name") or "")
        name = Path(rel).name
        low = name.lower()

        if not low.endswith(suffix):
            continue
        if pdb_l and pdb_l not in low:
            continue
        if chain_u and f"_{chain_u.lower()}_" not in low:
            continue
        if ligand_u and ligand_u.lower() not in low:
            continue
        if resid_s and kind != "pdb" and resid_s.lower() not in low:
            continue
        if kind == "svg" and plain is True and "_plain.svg" not in low:
            continue
        if kind == "svg" and plain is False and "_exposed.svg" not in low:
            continue

        return {
            **item,
            "relative_path": rel.lstrip("/"),
            "source": "randy_files",
        }

    return None


def find_table(job_id: str, names: Iterable[str]) -> Optional[Dict[str, Any]]:
    payload = get_job(job_id)
    if not payload:
        return None

    wanted = {Path(str(n)).name.lower() for n in names}

    for item in _candidate_files(payload):
        rel = str(item.get("relative_path") or item.get("filename") or item.get("name") or "")
        if Path(rel).name.lower() in wanted:
            return {**item, "relative_path": rel.lstrip("/")}

    # If RANDY only supports filename search, return the first requested filename.
    for name in names:
        clean = Path(str(name)).name
        if clean:
            return {"relative_path": clean, "filename": clean, "source": "randy_filename_guess"}

    return None


def get_file_bytes(job_id: str, relative_path: str, timeout: int = 30) -> Optional[tuple[bytes, str]]:
    if not archive_enabled() or not relative_path:
        return None

    try:
        resp = requests.get(_file_url(job_id, relative_path), headers=_headers(), timeout=timeout)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/octet-stream")
    except Exception:
        return None


def proxy_file_response(job_id: str, relative_path: str, mimetype: str = "application/octet-stream") -> Optional[Response]:
    got = get_file_bytes(job_id, relative_path)
    if not got:
        return None

    content, content_type = got
    return Response(
        content,
        mimetype=mimetype or content_type or "application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Warhead-Handoff-Source": "RANDY_ARCHIVE",
        },
    )


def get_table_dataframe(job_id: str, names: Iterable[str]) -> Optional[pd.DataFrame]:
    asset = find_table(job_id, names)
    if not asset:
        return None

    got = get_file_bytes(job_id, asset.get("relative_path", ""))
    if not got:
        return None

    content, _content_type = got
    try:
        return pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
    except Exception:
        return None
