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


TABLE_CANDIDATES = {
    "Results_Display.csv": [
        "Results_Display.csv",
        "TARGET_RESULTS/Results_Display.csv",
        "job_files/Results_Display.csv",
        "job_files/TARGET_RESULTS/Results_Display.csv",
    ],
    "Resolved_SASA_Summary.csv": [
        "Resolved_SASA_Summary.csv",
        "TARGET_RESULTS/Resolved_SASA_Summary.csv",
        "job_files/Resolved_SASA_Summary.csv",
        "job_files/TARGET_RESULTS/Resolved_SASA_Summary.csv",
    ],
    "Resolved_SASA_Summary.tsv": [
        "Resolved_SASA_Summary.tsv",
        "TARGET_RESULTS/Resolved_SASA_Summary.tsv",
        "job_files/Resolved_SASA_Summary.tsv",
        "job_files/TARGET_RESULTS/Resolved_SASA_Summary.tsv",
    ],
    "Warhead_SASA_atoms.csv": [
        "Warhead_SASA_atoms.csv",
        "TARGET_RESULTS/Warhead_SASA_atoms.csv",
        "job_files/Warhead_SASA_atoms.csv",
        "job_files/TARGET_RESULTS/Warhead_SASA_atoms.csv",
    ],
    "Ligand_3D_Atoms_with_SASA.csv": [
        "Ligand_3D_Atoms_with_SASA.csv",
        "TARGET_RESULTS/Ligand_3D_Atoms_with_SASA.csv",
        "job_files/Ligand_3D_Atoms_with_SASA.csv",
        "job_files/TARGET_RESULTS/Ligand_3D_Atoms_with_SASA.csv",
    ],
    "3DSASAmapped.csv": [
        "3DSASAmapped.csv",
        "TARGET_RESULTS/3DSASAmapped.csv",
        "job_files/3DSASAmapped.csv",
        "job_files/TARGET_RESULTS/3DSASAmapped.csv",
    ],
    "Ligand_Metadata.csv": [
        "Ligand_Metadata.csv",
        "TARGET_RESULTS/Ligand_Metadata.csv",
        "job_files/Ligand_Metadata.csv",
        "job_files/TARGET_RESULTS/Ligand_Metadata.csv",
    ],
}

_LAST_TABLE_DIAGNOSTIC: Dict[str, Any] = {}


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


def get_job_index(job_id: str) -> Optional[Dict[str, Any]]:
    return get_job(job_id)


def last_table_diagnostic() -> Dict[str, Any]:
    return dict(_LAST_TABLE_DIAGNOSTIC)


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
        rel = option.get("pdb_path") or name
    elif kind == "sdf":
        name = option.get("sdf")
        rel = option.get("sdf_path") or name
    elif kind == "svg":
        name = option.get("svg_plain") if plain else option.get("svg_exposed")
        rel = (option.get("svg_plain_path") if plain else option.get("svg_exposed_path")) or name
    else:
        name = None
        rel = None

    if not name and not rel:
        return None

    return {
        "name": Path(str(name or rel)).name,
        "filename": Path(str(name or rel)).name,
        "relative_path": str(rel or name).lstrip("/"),
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
        _LAST_TABLE_DIAGNOSTIC.clear()
        _LAST_TABLE_DIAGNOSTIC.update({"job_id": job_id, "status": "job_not_found_or_archive_disabled"})
        return None

    wanted_names = [Path(str(n)).name for n in names if str(n or "").strip()]
    wanted = {n.lower() for n in wanted_names}
    attempted: list[str] = []

    indexed = payload.get("available_tables")
    if isinstance(indexed, dict):
        for name in wanted_names:
            rel = indexed.get(name)
            if isinstance(rel, str) and rel.strip():
                attempted.append(rel.strip())
                return {
                    "relative_path": rel.strip().lstrip("/"),
                    "filename": Path(rel).name,
                    "source": "randy_available_tables",
                }

    tables = payload.get("tables")
    if isinstance(tables, dict):
        for name in wanted_names:
            info = tables.get(name)
            if isinstance(info, dict):
                rel = str(info.get("relative_path") or "").strip()
                if rel:
                    attempted.append(rel)
                    return {**info, "relative_path": rel.lstrip("/"), "source": "randy_tables_index"}

    for item in _candidate_files(payload):
        rel = str(item.get("relative_path") or item.get("filename") or item.get("name") or "")
        if Path(rel).name.lower() in wanted:
            attempted.append(rel)
            return {**item, "relative_path": rel.lstrip("/")}

    for name in wanted_names:
        for rel in TABLE_CANDIDATES.get(name, [name]):
            attempted.append(rel)
            return {"relative_path": rel, "filename": Path(rel).name, "source": "randy_candidate_guess", "attempted_paths": attempted}

    return None


def get_file_bytes(job_id: str, relative_path: str, timeout: int = 30) -> Optional[tuple[bytes, str]]:
    if not archive_enabled() or not relative_path:
        return None

    try:
        resp = requests.get(_file_url(job_id, relative_path), headers=_headers(), timeout=timeout)
        if resp.status_code in {400, 401, 403, 404}:
            try:
                detail = resp.json()
            except Exception:
                detail = {"status_code": resp.status_code, "text": resp.text[:300]}
            _LAST_TABLE_DIAGNOSTIC.update({
                "last_file_status": resp.status_code,
                "last_file_path": relative_path,
                "last_file_error": detail,
            })
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
    _LAST_TABLE_DIAGNOSTIC.clear()
    if not archive_enabled():
        _LAST_TABLE_DIAGNOSTIC.update({"job_id": job_id, "status": "auth_or_config_missing"})
        return None

    payload = get_job(job_id)
    if not payload:
        _LAST_TABLE_DIAGNOSTIC.update({"job_id": job_id, "status": "job_not_found"})
        return None

    attempted: list[dict[str, Any]] = []
    assets: list[Dict[str, Any]] = []
    first = find_table(job_id, names)
    if first:
        assets.append(first)
    for name in [Path(str(n)).name for n in names if str(n or "").strip()]:
        for rel in TABLE_CANDIDATES.get(name, [name]):
            if not any(str(a.get("relative_path") or "") == rel for a in assets):
                assets.append({"relative_path": rel, "filename": Path(rel).name, "source": "randy_candidate"})

    if not assets:
        _LAST_TABLE_DIAGNOSTIC.update({"job_id": job_id, "status": "no_table_candidates"})
        return None

    for asset in assets:
        rel = str(asset.get("relative_path") or "").strip()
        if not rel:
            continue
        got = get_file_bytes(job_id, rel)
        attempted.append({"relative_path": rel, "source": asset.get("source", ""), "downloaded": bool(got)})
        if not got:
            continue
        content, _content_type = got
        try:
            sep = "\t" if rel.lower().endswith(".tsv") else ","
            df = pd.read_csv(io.BytesIO(content), sep=sep, dtype=str).fillna("")
        except Exception as exc:
            attempted[-1]["parse_error"] = str(exc)
            continue
        if df.empty:
            attempted[-1]["empty"] = True
            continue
        _LAST_TABLE_DIAGNOSTIC.update({
            "job_id": job_id,
            "status": "ok",
            "table_path": rel,
            "table_source": asset.get("source", ""),
            "attempted_paths": attempted,
        })
        return df

    _LAST_TABLE_DIAGNOSTIC.update({
        "job_id": job_id,
        "status": "no_readable_table",
        "attempted_paths": attempted,
    })
    return None
