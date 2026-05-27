# app/api/sasa_api.py
from __future__ import annotations
import os
import csv
import math
import time
import hashlib
from dataclasses import dataclass
from typing import Dict, Tuple, List, Any, Optional

from flask import Blueprint, request, jsonify, current_app, abort, make_response

bp = Blueprint("sasa_api", __name__)

# ----------------------------
# In-memory store per job_id
# ----------------------------

@dataclass
class SasaKeyData:
    atoms: List[dict]
    minv: float
    maxv: float
    p95: float

@dataclass
class JobSasaStore:
    path: str
    mtime: float
    size: int
    etag: str
    by_key: Dict[Tuple[str, str, str], SasaKeyData]           # (pdb, chain, resid)
    available_by_pdb: Dict[str, List[Tuple[str, str]]]        # pdb -> [(chain,resid), ...]
    resid_by_ligand: Dict[Tuple[str, str, str], str]          # (pdb, chain, ligand) -> resid



_JOB_CACHE: Dict[str, JobSasaStore] = {}

def _job_csv_path(job_id: str) -> str:
    job_root = current_app.config.get("JOBS_DIR")
    if not job_root:
        raise RuntimeError("Set app.config['JOBS_DIR'] to your jobs directory.")

    candidates = [
        os.path.join(job_root, job_id, "TARGET_RESULTS", "Warhead_SASA_atoms.csv"),
        os.path.join(job_root, job_id, "Warhead_SASA_atoms.csv"),
        os.path.join(job_root, job_id, "TARGET_RESULTS", "Ligand_3D_Atoms_with_SASA.csv"),
        os.path.join(job_root, job_id, "Ligand_3D_Atoms_with_SASA.csv"),
        os.path.join(job_root, job_id, "MCS_Output", "Ligand_MCS_SASA_ALL_ATOMS.csv"),
        os.path.join(job_root, job_id, "MCS_OUTPUT", "Ligand_MCS_SASA_ALL_ATOMS.csv"),
    ]
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", newline="") as f:
                reader = csv.DictReader(f)
                if any(True for _ in reader):
                    return p
        except Exception:
            return p
    # default (helps your 404 message show the likely path)
    return candidates[0]


def _field(row: Dict[str, str], *names: str) -> str:
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row.get(name) or ""
        v = lower.get(name.lower())
        if v is not None:
            return v or ""
    return ""


def _pick_field(fieldnames: List[str], *names: str) -> Optional[str]:
    lowered = {f.lower(): f for f in fieldnames}
    for name in names:
        if name in fieldnames:
            return name
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None

def _norm_resid(v: str) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    # normalize "9001.0" -> "9001"
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return s



def _calc_p95(values: List[float]) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    idx = int(math.floor(0.95 * (len(vs) - 1)))
    return float(vs[idx])



def _build_store(csv_path: str) -> JobSasaStore:
    st = os.stat(csv_path)
    mtime, size = st.st_mtime, st.st_size
    etag_src = f"{csv_path}|{mtime}|{size}".encode("utf-8")
    etag = hashlib.sha1(etag_src).hexdigest()

    by_key: Dict[Tuple[str, str, str], List[dict]] = {}
    avail: Dict[str, set] = {}
    exposures_for_key: Dict[Tuple[str, str, str], List[float]] = {}
    resid_by_ligand: Dict[Tuple[str, str, str], str] = {}  # (pdb, chain, ligand) -> resid

    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        required_any = {
            "pdb_id": _pick_field(fieldnames, "pdb_id", "pdb"),
            "Chain": _pick_field(fieldnames, "Chain", "chain"),
            "Residue_ID": _pick_field(fieldnames, "Residue_ID", "residue_id", "resid"),
            "x": _pick_field(fieldnames, "x"),
            "y": _pick_field(fieldnames, "y"),
            "z": _pick_field(fieldnames, "z"),
            "Exposure_A2": _pick_field(fieldnames, "Exposure_A2", "exposure", "exposure_a2"),
        }
        missing = [name for name, value in required_any.items() if not value]
        if missing:
            raise RuntimeError(f"SASA CSV missing columns: {sorted(missing)}")

        for row in reader:
            pdb   = _field(row, "pdb_id", "pdb").strip().lower()
            chain = _field(row, "Chain", "chain").strip().upper()
            resid = _norm_resid(_field(row, "Residue_ID", "residue_id", "resid"))
            lig   = _field(row, "Ligand", "Warhead", "ligand", "warhead").strip().upper()

            if not pdb or not chain or not resid:
                continue

            # Map ligand -> resid (first one wins)
            if lig and (pdb, chain, lig) not in resid_by_ligand:
                resid_by_ligand[(pdb, chain, lig)] = resid

            key = (pdb, chain, resid)

            # exposure
            try:
                exposure = float(_field(row, "Exposure_A2", "exposure", "exposure_a2") or 0.0)
            except Exception:
                exposure = 0.0

            # rdkit atom index
            rdkit_idx = None
            try:
                atom_index_raw = _field(row, "AtomIndex", "atom_index", "rdkit_atom_index")
                if atom_index_raw not in (None, "", "nan", "NaN"):
                    rdkit_idx = int(float(atom_index_raw))
            except Exception:
                rdkit_idx = None

            # atom_id
            atom_id = None
            try:
                atom_id_raw = _field(row, "atom_id", "serial", "pdb_serial")
                if atom_id_raw not in (None, "", "nan", "NaN"):
                    atom_id = int(float(atom_id_raw))
            except Exception:
                atom_id = None

            # coords
            try:
                x = float(_field(row, "x")); y = float(_field(row, "y")); z = float(_field(row, "z"))
            except Exception:
                continue  # cannot plot without coords

            atom_name = _field(row, "atom_name", "exact_atom", "AtomName").strip()
            element = _field(row, "AtomSymbol", "element", "atom_symbol").strip()
            if not element and atom_name:
                element = "".join(ch for ch in atom_name if ch.isalpha())[:2].strip().title()

            atom = {
                "atom_name": atom_name,
                "element": element,
                "atom_id": atom_id,
                "x": x, "y": y, "z": z,
                "exposure": exposure,        # frontend-friendly
                "exposure_a2": exposure,     # backcompat
                "rdkit_atom_index": rdkit_idx
            }

            by_key.setdefault(key, []).append(atom)
            exposures_for_key.setdefault(key, []).append(exposure)
            avail.setdefault(pdb, set()).add((chain, resid))

    # ✅ RESTORE final_by_key (THIS FIXES YOUR 500)
    final_by_key: Dict[Tuple[str, str, str], SasaKeyData] = {}
    for key, atoms in by_key.items():
        exps = exposures_for_key.get(key, [])
        minv = float(min(exps)) if exps else 0.0
        maxv = float(max(exps)) if exps else 0.0
        p95  = _calc_p95(exps)
        final_by_key[key] = SasaKeyData(atoms=atoms, minv=minv, maxv=maxv, p95=p95)

    available_by_pdb = {pdb: sorted(list(items)) for pdb, items in avail.items()}

    return JobSasaStore(
        path=csv_path,
        mtime=mtime,
        size=size,
        etag=etag,
        by_key=final_by_key,
        available_by_pdb=available_by_pdb,
        resid_by_ligand=resid_by_ligand,
    )



def _get_store(job_id: str) -> JobSasaStore:
    csv_path = _job_csv_path(job_id)
    if not os.path.exists(csv_path):
        abort(404, description=f"SASA file not found for job {job_id}: {csv_path}")

    st = os.stat(csv_path)
    cached = _JOB_CACHE.get(job_id)

    # Reload if missing or changed
    if (cached is None) or (cached.path != csv_path) or (cached.mtime != st.st_mtime) or (cached.size != st.st_size):
        _JOB_CACHE[job_id] = _build_store(csv_path)

    return _JOB_CACHE[job_id]


def _maybe_304(store: JobSasaStore):
    inm = request.headers.get("If-None-Match", "")
    if inm and inm.strip('"') == store.etag:
        resp = make_response("", 304)
        resp.headers["ETag"] = f"\"{store.etag}\""
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp
    return None


# ----------------------------
# Routes
# ----------------------------

@bp.get("/api/jobs/<job_id>/sasa/available")
def sasa_available(job_id: str):
    store = _get_store(job_id)
    maybe = _maybe_304(store)
    if maybe:
        return maybe

    pdb_id = (request.args.get("pdb_id") or "").strip().lower()
    if not pdb_id:
        return jsonify({"ok": False, "error": "Missing pdb_id"}), 400

    avail = store.available_by_pdb.get(pdb_id, [])
    resp = jsonify({
        "ok": True,
        "pdb_id": pdb_id,
        "available": [{"chain": c, "residue_id": r} for (c, r) in avail],
    })
    resp.headers["ETag"] = f"\"{store.etag}\""
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.get("/api/jobs/<job_id>/sasa/atoms")
def sasa_atoms(job_id: str):
    store = _get_store(job_id)
    maybe = _maybe_304(store)
    if maybe:
        return maybe

    pdb_id = (request.args.get("pdb_id") or "").strip().lower()
    chain = (request.args.get("chain") or "").strip().upper()
    residue_id_raw = str(request.args.get("residue_id") or "").strip()

    # 🔥 FIX: normalize residue_id exactly like CSV build
    residue_id = _norm_resid(residue_id_raw)

    if not (pdb_id and chain and residue_id):
        return jsonify({"ok": False, "error": "Require pdb_id, chain, residue_id"}), 400

    key = (pdb_id, chain, residue_id)
    data = store.by_key.get(key)

    if not data:
        return jsonify({"ok": False, "error": f"No SASA data for {pdb_id}|{chain}|{residue_id}"}), 404


    resp = jsonify({
        "ok": True,
        "key": f"{pdb_id}|{chain}|{residue_id}",
        "pdb_id": pdb_id,
        "chain": chain,
        "residue_id": residue_id,
        "stats": {"min": data.minv, "max": data.maxv, "p95": data.p95},
        "atoms": data.atoms,
    })
    resp.headers["ETag"] = f"\"{store.etag}\""
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.post("/api/jobs/<job_id>/sasa/bulk")
def sasa_bulk(job_id: str):
    store = _get_store(job_id)
    maybe = _maybe_304(store)
    if maybe:
        return maybe

    payload = request.get_json(silent=True) or {}
    reqs = payload.get("requests") or []
    if not isinstance(reqs, list) or not reqs:
        return jsonify({"ok": False, "error": "Body must include requests: [...]"}), 400

    results: Dict[str, Any] = {}
    for r in reqs:
        pdb_id = str(r.get("pdb_id", "")).strip().lower()
        chain = str(r.get("chain", "")).strip().upper()
        residue_id = str(r.get("residue_id", "")).strip()
        if not (pdb_id and chain and residue_id):
            continue
        key = (pdb_id, chain, residue_id)
        data = store.by_key.get(key)
        if not data:
            continue
        kstr = f"{pdb_id}|{chain}|{residue_id}"
        results[kstr] = {
            "pdb_id": pdb_id,
            "chain": chain,
            "residue_id": residue_id,
            "stats": {"min": data.minv, "max": data.maxv, "p95": data.p95},
            "atoms": data.atoms,
        }

    resp = jsonify({"ok": True, "results": results})
    resp.headers["ETag"] = f"\"{store.etag}\""
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.get("/api/jobs/<job_id>/sasa/residue_for_ligand")
def sasa_residue_for_ligand(job_id: str):
    store = _get_store(job_id)
    maybe = _maybe_304(store)
    if maybe:
        return maybe

    pdb_id = (request.args.get("pdb_id") or "").strip().lower()
    chain  = (request.args.get("chain") or "").strip().upper()
    ligand = (request.args.get("ligand") or "").strip().upper()

    if not (pdb_id and chain and ligand):
        return jsonify({"ok": False, "error": "Require pdb_id, chain, ligand"}), 400

    resid = store.resid_by_ligand.get((pdb_id, chain, ligand))
    if not resid:
        return jsonify({"ok": False, "error": f"No residue_id for {pdb_id}|{chain}|{ligand}"}), 404

    resp = jsonify({
        "ok": True,
        "pdb_id": pdb_id,
        "chain": chain,
        "ligand": ligand,
        "residue_id": resid
    })
    resp.headers["ETag"] = f"\"{store.etag}\""
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp
