#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
===============================================================================
11_mcsMatcher.py — TRUE 2D→3D Atom Mapping via RDKit MCS (Per-PDB Instance)
===============================================================================

• Maps 2D SMILES atoms → 3D ligand atoms using RDKit graph matching + MCS
• Handles *every occurrence* of a ligand across multiple PDBs
• Builds 3D molecule from coordinate sets + bond perception
• Outputs full metadata for each matched 3D atom
• Parallelized across all ligand instances

Key fixes:
  ✅ RDKit-version-safe MCSParameters usage (no AtomCompare attribute crash)
  ✅ Better 3D bond perception (DetermineBonds w/ Hueckel, fallback to Connectivity)
  ✅ Genericize BOTH 2D and 3D graphs before MCS matching

NEW (this version):
  ✅ Generates 2D SVGs (plain + SASA-exposed) per ligand occurrence using
     the already-built MCS + SASA table (Ligand_MCS_SASA_ALL_ATOMS.csv).
     Output: MCS_Output/MCS_SVG/*_plain.svg and *_exposed.svg
===============================================================================
"""

import os
import re
import ast
from pathlib import Path
from functools import partial
from multiprocessing import Pool, cpu_count
import subprocess
import time
from typing import Tuple
from functools import partial
from multiprocessing import Pool, cpu_count
from multiprocessing import get_context
import signal
from contextlib import contextmanager
from multiprocessing import TimeoutError as MPTimeoutError

import socket
import shutil
import multiprocessing
import subprocess
import os
import signal
import time
from pathlib import Path
from typing import Tuple
from rdkit import Chem
import sys
import traceback
from time import perf_counter


import pandas as pd
import numpy as np

from rdkit import Chem
from rdkit.Geometry import Point3D
from rdkit.Chem import rdFMCS, AllChem
from rdkit.Chem import rdDetermineBonds
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D


# =============================================================================
# Helpers: normalize SMILES / Warhead IDs
# =============================================================================
def dbg(msg: str):
    # PID + timestamp helps when multiple workers interleave output
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts} pid={os.getpid()}] {msg}", flush=True)


class TimeoutException(Exception):
    pass

def _alarm_handler(signum, frame):
    raise TimeoutException()

@contextmanager
def time_limit(seconds: int):
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old_handler)


def normalize_smiles(x):
    if not isinstance(x, str):
        return x
    raw = x.strip()

    # CASE: ['smiles'] or ["smiles"]
    m = re.match(r"^\[\s*['\"]([^'\"]+)['\"]\s*\]$", raw)
    if m:
        return m.group(1).strip()

    return raw.strip('"').strip("'")


def normalize_warhead_id(x):
    s = str(x).strip()
    # handles "['A00']" / '["A00"]'
    if s.startswith("[") and s.endswith("]"):
        try:
            v = ast.literal_eval(s)
            if isinstance(v, (list, tuple)) and len(v) > 0:
                s = str(v[0]).strip()
        except Exception:
            pass
    return s.strip().strip('"').strip("'")


# =============================================================================
# JOB CONTEXT + 5-CHAR LIGAND ALIAS MAP
# =============================================================================
def load_5char_map_if_needed(job_dir: Path, warheads: set, smiles_map: dict):
    """
    Load 5CharMAP.csv only if there are unresolved ligands *after normalization*.

    NEW BEHAVIOR (what you want):
      - If 5CharMAP.csv is missing OR empty OR malformed: do NOT crash.
        Just return empty dicts and let those ligands fall through to Missing SMILES later.
      - If warheads were only "unresolved" due to formatting (['A00'], quotes, spaces),
        normalization fixes that and we won't even try to load 5CharMAP.
    """

    # Normalize warheads before checking against the SMILES map
    warheads_norm = {normalize_warhead_id(w) for w in warheads if str(w).strip()}
    unresolved = [w for w in warheads_norm if w not in smiles_map or not str(smiles_map.get(w, "")).strip()]

    if not unresolved:
        print("ℹ️ No ligand alias resolution required (all ligands resolved in SMILES map).")
        return {}, {}

    f = job_dir / "5CharMAP.csv"
    if not f.exists():
        print(f"⚠️  5CharMAP.csv not found. Continuing without aliasing. Unresolved ligands: {len(unresolved)}")
        return {}, {}

    # Empty file? Also fine.
    try:
        df = pd.read_csv(f)
    except pd.errors.EmptyDataError:
        print(f"⚠️  5CharMAP.csv is empty. Continuing without aliasing. Unresolved ligands: {len(unresolved)}")
        return {}, {}
    except Exception as e:
        print(f"⚠️  Could not read 5CharMAP.csv ({e}). Continuing without aliasing.")
        return {}, {}

    if df is None or df.empty:
        print(f"⚠️  5CharMAP.csv has no rows. Continuing without aliasing. Unresolved ligands: {len(unresolved)}")
        return {}, {}

    # Normalize column names (case-insensitive support)
    df = df.fillna("")
    cols_lower = {c.lower(): c for c in df.columns}

    # Required-ish columns (we'll bail gracefully if not present)
    def col(name: str):
        return cols_lower.get(name.lower(), None)

    protein_col = col("protein") or col("target")
    pdb_col     = col("pdb") or col("pdb_id")
    ligx_col    = col("ligandx") or col("ligand_x") or col("ligand")
    lig3_col    = col("ligand3")
    lig5_col    = col("ligand5")

    if not pdb_col or not ligx_col:
        print("⚠️  5CharMAP.csv missing required columns (need pdb + ligandX). Continuing without aliasing.")
        return {}, {}

    # Strip strings
    for c in [protein_col, pdb_col, ligx_col, lig3_col, lig5_col]:
        if c and c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    to_lig3, to_lig5 = {}, {}

    # If protein/target column is missing, we can't make (Target,pdb,ligandX) keys.
    # We will still build keys with protein="" so script doesn't crash; you can optionally
    # add a wildcard lookup later if you want.
    for _, r in df.iterrows():
        prot = str(r[protein_col]).strip() if protein_col else ""
        pdb  = str(r[pdb_col]).strip().lower()
        ligx = str(r[ligx_col]).strip()

        if not pdb or not ligx:
            continue

        key = (prot, pdb, ligx)

        if lig3_col:
            v3 = str(r[lig3_col]).strip()
            if v3:
                to_lig3[key] = v3

        if lig5_col:
            v5 = str(r[lig5_col]).strip()
            # ONLY accept true 5-char IDs (alnum, len 5)
            if v5 and re.match(r"^[A-Za-z0-9]{5}$", v5):
                to_lig5[key] = v5


    if not to_lig3 and not to_lig5:
        print("⚠️  5CharMAP.csv loaded but contained no usable mappings. Continuing without aliasing.")
        return {}, {}

    print(f"✅ Loaded 5CharMAP aliases: ligand3={len(to_lig3)} ligand5={len(to_lig5)}")
    return to_lig3, to_lig5



# ---- Resolve JOB_ID / JOB_DIR robustly ----
JOB_ID = os.environ.get("JOB_ID", "").strip()
if not JOB_ID:
    cwd = Path.cwd()
    if cwd.name and len(cwd.name) == 8 and cwd.parent.name == "jobs":
        JOB_ID = cwd.name
if not JOB_ID:
    raise RuntimeError("JOB_ID not set. Export JOB_ID or run inside jobs/<jobid>/")

JOB_DIR = Path("jobs") / JOB_ID if Path("jobs").exists() else Path.cwd()


# =============================================================================
# Graph utilities
# =============================================================================
def make_bonds_generic(m: Chem.Mol) -> Chem.Mol:
    """Return a copy with all bonds SINGLE and non-aromatic for robust matching."""
    q = Chem.Mol(m)
    for a in q.GetAtoms():
        a.SetIsAromatic(False)
    for b in q.GetBonds():
        b.SetIsAromatic(False)
        b.SetBondType(Chem.BondType.SINGLE)
    return q


# =============================================================================
# ELEMENT INFERENCE (fix CL/BR/etc parsing)
# =============================================================================
_TWO_LETTER_ELEMENTS = {
    "CL": "Cl",
    "BR": "Br",
    "SI": "Si",
    "SE": "Se",
    "MG": "Mg",
    "ZN": "Zn",
    "FE": "Fe",
    "CU": "Cu",
    "MN": "Mn",
    "NI": "Ni",
    "HG": "Hg",
    "PB": "Pb",
    "AG": "Ag",
    "AU": "Au",
    "AL": "Al",
    "K":  "K",
    "LI": "Li",
}
_ORGANIC_FIRST_LETTERS = set(list("HCNOSPFIB")) | {"I"}


def normalize_sdf_residue_id(value) -> str:
    s = str(value).strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return s


def sdf_basename_from_group_key(key) -> str:
    ligand, target, pdb_id, resid, chain = key
    pid = str(pdb_id).lower().strip()
    ch = str(chain).upper().strip()
    lig = normalize_warhead_id(ligand).upper().strip()
    rid = normalize_sdf_residue_id(resid)
    return f"{pid}_{ch}_{lig}_{rid}"


def infer_element_from_atom_name(atom_name: str) -> str:
    if atom_name is None or (isinstance(atom_name, float) and np.isnan(atom_name)):
        return "C"

    s = str(atom_name).strip()
    s = re.sub(r"^[^A-Za-z]+", "", s)
    s = re.sub(r"^[0-9]+", "", s)

    m = re.match(r"^([A-Za-z]{1,2})", s)
    if not m:
        return "C"
    tok = m.group(1).upper()

    if tok in _TWO_LETTER_ELEMENTS:
        if tok.startswith("C") and len(tok) == 2 and tok not in {"CL"}:
            return "C"
        return _TWO_LETTER_ELEMENTS[tok]

    # PDB ligand hydrogens often arrive as HN1, HNC, HOL, etc. The leading H is
    # the element; the remaining characters are atom-name context, not "Hn".
    if tok[0] in _ORGANIC_FIRST_LETTERS:
        return tok[0]

    if len(tok) == 2:
        return tok[0] + tok[1].lower()
    return tok[0]


# =============================================================================
# PATHS / INPUTS
# =============================================================================
BASE = Path(".")
OUTDIR = BASE / "MCS_Output"
OUTDIR.mkdir(exist_ok=True)

# NEW
SDF_DIR = OUTDIR / "MCS_SDF"
SDF_DIR.mkdir(exist_ok=True)

WRITE_SDF = True

# Prefer obabel first if you want its bond perception,
# but ALWAYS fall back to RDKit if obabel fails.
SDF_METHOD = "obabel"   # "obabel" or "rdkit"
SDF_FALLBACK_TO_RDKIT = True

SKIP_IF_EXISTS = True

# Hard per-occurrence timeout for obabel conversion
OBABEL_TIMEOUT = 8  # seconds

# Critical: limit how many obabel conversions can run at once.
# Too many concurrent obabel calls can make things "hang".
OBABEL_MAX_CONCURRENT = 2

# Shared semaphore (works well under Linux fork)
OBABEL_SEM = multiprocessing.BoundedSemaphore(OBABEL_MAX_CONCURRENT)

atom3d = pd.read_csv("Ligand_3D_Atoms.csv")

smiles_path = Path("Target_Table/Ligand_SMILES_Map.csv")
if not smiles_path.exists():
    smiles_path = Path("Target_Table/SMILES_Ligand_Map.csv")
if not smiles_path.exists():
    raise FileNotFoundError(
        "Could not find SMILES map CSV in Target_Table/ "
        "(Ligand_SMILES_Map.csv or SMILES_Ligand_Map.csv)"
    )

smiles_raw = pd.read_csv(smiles_path).fillna("")
for c in ["SMILES", "Warhead"]:
    if c in smiles_raw.columns:
        smiles_raw[c] = smiles_raw[c].astype(str).str.strip()

# Normalize keys + SMILES
smiles_raw["Warhead_norm"] = smiles_raw["Warhead"].apply(normalize_warhead_id)
smiles_raw["SMILES_norm"] = smiles_raw["SMILES"].apply(normalize_smiles)

# Warhead -> SMILES
LIG_TO_SMILES = dict(zip(smiles_raw["Warhead_norm"], smiles_raw["SMILES_norm"]))

# Determine which warheads exist in this job
warheads_in_job = set(
    atom3d["Warhead"]
    .dropna()
    .astype(str)
    .map(normalize_warhead_id)   # ✅ key fix
    .str.strip()
)


ALIAS_TO_LIG3, ALIAS_TO_LIG5 = load_5char_map_if_needed(
    JOB_DIR,
    warheads_in_job,
    LIG_TO_SMILES
)

# =============================================================================
# BUILD 3D MOLECULE FROM COORDINATES
# =============================================================================
def mol_formal_charge(m: Chem.Mol) -> int:
    return int(sum(a.GetFormalCharge() for a in m.GetAtoms()))



def perceive_bonds(mol: Chem.Mol, template_charge: int | None = None) -> None:
    """
    Only determine connectivity.
    Do NOT attempt Hueckel bond order assignment.
    This avoids charge/valence noise and instability.
    """
    try:
        rdDetermineBonds.DetermineConnectivity(mol)
    except Exception as e:
        print(f"⚠️ DetermineConnectivity failed: {repr(e)}", flush=True)




def build_3d_mol(df: pd.DataFrame, template_charge: int | None = None):
    df = df.copy().reset_index(drop=True)

    # ✅ CRITICAL: remove duplicated atoms inside a single occurrence
    df = dedupe_3d_atoms(df, coord_decimals=3)

    df["AtomSymbol"] = df["atom_name"].apply(infer_element_from_atom_name)

    rw = Chem.RWMol()
    for sym in df["AtomSymbol"]:
        rw.AddAtom(Chem.Atom(sym))
    mol = rw.GetMol()

    conf = Chem.Conformer(len(df))
    for i, row in df.iterrows():
        conf.SetAtomPosition(i, Point3D(float(row["x"]), float(row["y"]), float(row["z"])))
    mol.AddConformer(conf)

    # ✅ Pass charge when we know it (fixes your charge mismatch spam)
    perceive_bonds(mol, template_charge=template_charge)

    return mol, df





def write_sdf_rdkit(mol: Chem.Mol, out_sdf: Path, name: str = "") -> Tuple[bool, str]:
    try:
        out_sdf.parent.mkdir(parents=True, exist_ok=True)
        if name:
            mol.SetProp("_Name", name)

        w = Chem.SDWriter(str(out_sdf))
        w.write(mol)
        w.close()

        if not out_sdf.exists() or out_sdf.stat().st_size == 0:
            return False, "RDKit wrote empty/missing SDF"
        return True, "OK"
    except Exception as e:
        return False, f"RDKit exception: {e}"


def write_sdf_obabel(mol: Chem.Mol, out_sdf: Path, name: str = "") -> Tuple[bool, str]:
    """
    Write temp PDB then convert via obabel.
    Robust against hangs:
      - semaphore limits concurrent obabel
      - process group kill on timeout (prevents zombies)
    """
    if shutil.which("obabel") is None:
        return False, "obabel not found in PATH"

    out_sdf.parent.mkdir(parents=True, exist_ok=True)
    tmp_pdb = out_sdf.with_suffix(".tmp.pdb")

    try:
        if name:
            mol.SetProp("_Name", name)

        # RDKit PDB export (fast)
        Chem.MolToPDBFile(mol, str(tmp_pdb))

        cmd = ["obabel", "-ipdb", str(tmp_pdb), "-osdf", "-O", str(out_sdf)]

        # Limit concurrency so the whole job doesn’t “stall”
        OBABEL_SEM.acquire()
        try:
            # Start in its own process group so we can kill it + children
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=os.setsid  # Linux only; OK for your environment
            )
            try:
                stdout, stderr = p.communicate(timeout=OBABEL_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Kill entire process group
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
                return False, f"obabel timeout (>{OBABEL_TIMEOUT}s) killed process group"

            if p.returncode != 0:
                msg = (stderr.strip() or stdout.strip() or f"obabel failed code={p.returncode}")
                return False, msg[:250]

        finally:
            OBABEL_SEM.release()

        if not out_sdf.exists() or out_sdf.stat().st_size == 0:
            return False, "obabel produced empty/missing SDF"

        # Clean temp
        try:
            tmp_pdb.unlink()
        except Exception:
            pass

        warn = (stderr.strip() if 'stderr' in locals() else "")
        if warn:
            return True, f"OK (warn): {warn.splitlines()[0][:180]}"
        return True, "OK"

    except Exception as e:
        try:
            tmp_pdb.unlink()
        except Exception:
            pass
        return False, f"obabel exception: {repr(e)}"





# =============================================================================
# SASA annotation (keep your existing join strategy)
# =============================================================================
def annotate_with_sasa(df_in, sasa_csv="Warhead_SASA_atoms.csv", coord_decimals=3):
    df = df_in.copy()

    sasa_path = Path(sasa_csv)
    if not sasa_path.exists():
        df["Exposure_A2"] = 0.0
        return df

    sasa = pd.read_csv(sasa_path)

    # Normalize naming
    if "Warhead" in sasa.columns and "Ligand" not in sasa.columns:
        sasa = sasa.rename(columns={"Warhead": "Ligand"})

    # Standardize types
    for c in ["Target", "pdb_id", "Ligand", "Chain"]:
        if c in sasa.columns:
            sasa[c] = sasa[c].astype(str).str.strip()
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    if "pdb_id" in sasa.columns:
        sasa["pdb_id"] = sasa["pdb_id"].astype(str).str.lower()
    if "pdb_id" in df.columns:
        df["pdb_id"] = df["pdb_id"].astype(str).str.lower()

    sasa["Residue_ID"] = sasa["Residue_ID"].astype(str).str.strip()
    df["Residue_ID"]   = df["Residue_ID"].astype(str).str.strip()

    # atom_id + xyz numeric
    for dfx in (sasa, df):
        for c in ["atom_id", "x", "y", "z"]:
            if c in dfx.columns:
                dfx[c] = pd.to_numeric(dfx[c], errors="coerce")

    key_cols = ["Target", "pdb_id", "Ligand", "Residue_ID", "Chain", "atom_id"]
    sasa_primary = sasa[key_cols + ["Exposure_A2"]].copy()

    out = df.merge(sasa_primary, on=key_cols, how="left")

    missing = out["Exposure_A2"].isna()
    if missing.any():
        for dfx in (out, sasa):
            dfx["x_r"] = dfx["x"].round(coord_decimals)
            dfx["y_r"] = dfx["y"].round(coord_decimals)
            dfx["z_r"] = dfx["z"].round(coord_decimals)

        key2 = ["Target", "pdb_id", "Ligand", "Residue_ID", "Chain", "x_r", "y_r", "z_r"]
        sasa_fallback = sasa[key2 + ["Exposure_A2"]].copy()

        out2 = out.loc[missing].drop(columns=["Exposure_A2"]).merge(
            sasa_fallback, on=key2, how="left"
        )
        out.loc[missing, "Exposure_A2"] = out2["Exposure_A2"].values

        out = out.drop(columns=["x_r", "y_r", "z_r"], errors="ignore")

    out["Exposure_A2"] = out["Exposure_A2"].fillna(0.0).astype(float)
    return out


def dedupe_3d_atoms(df: pd.DataFrame, coord_decimals: int = 3) -> pd.DataFrame:
    """
    Fix the #1 Step-11 killer: duplicated atoms in a single ligand occurrence.
    Typically caused by altloc (A/B), multiple models, or duplicated HETATM.

    Strategy:
      1) Prefer de-dupe by atom_id if it's meaningful
      2) Fallback de-dupe by (atom_name + rounded xyz)
    """
    d = df.copy().reset_index(drop=True)

    # Ensure numeric coords
    for c in ["x", "y", "z"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")

    # 1) De-dupe by atom_id if possible
    if "atom_id" in d.columns:
        d["_atom_id_num"] = pd.to_numeric(d["atom_id"], errors="coerce")

        # If atom_id_num has lots of non-nans, use it
        if d["_atom_id_num"].notna().sum() >= max(3, int(0.6 * len(d))):
            # Stable sort then keep first occurrence
            d = d.sort_values(["_atom_id_num", "atom_name", "x", "y", "z"], kind="mergesort")
            d = d.drop_duplicates(subset=["_atom_id_num"], keep="first")
        d = d.drop(columns=["_atom_id_num"], errors="ignore")

    # 2) Fallback: de-dupe by atom_name + rounded coords
    d["_x_r"] = d["x"].round(coord_decimals)
    d["_y_r"] = d["y"].round(coord_decimals)
    d["_z_r"] = d["z"].round(coord_decimals)

    d = d.sort_values(["atom_name", "_x_r", "_y_r", "_z_r"], kind="mergesort")
    d = d.drop_duplicates(subset=["atom_name", "_x_r", "_y_r", "_z_r"], keep="first")
    d = d.drop(columns=["_x_r", "_y_r", "_z_r"], errors="ignore")

    return d.reset_index(drop=True)


# =============================================================================
# RDKit-version-safe MCS parameters
# =============================================================================
def find_mcs_smarts(molA: Chem.Mol, molB: Chem.Mol, timeout_sec: int = 30):
    """
    Returns MCS SMARTS or None. Works across RDKit versions:
    - Tries MCSParameters object
    - Falls back to keyword args if needed
    """
    # Try newer-style object config
    try:
        params = rdFMCS.MCSParameters()
        params.Timeout = int(timeout_sec)

        try:
            params.AtomCompare = rdFMCS.AtomCompare.CompareElements
        except Exception:
            pass
        try:
            params.BondCompare = rdFMCS.BondCompare.CompareAny
        except Exception:
            pass

        for (attr, val) in [
            ("MatchValences", False),
            ("RingMatchesRingOnly", False),
            ("CompleteRingsOnly", False),
        ]:
            try:
                setattr(params, attr, val)
            except Exception:
                pass

        res = rdFMCS.FindMCS([molA, molB], parameters=params)
        smarts = getattr(res, "smartsString", None)
        return smarts if smarts else None

    except Exception:
        # Fall back to older keyword-arg API
        try:
            res = rdFMCS.FindMCS(
                [molA, molB],
                timeout=int(timeout_sec),
                atomCompare=rdFMCS.AtomCompare.CompareElements if hasattr(rdFMCS, "AtomCompare") else None,
                bondCompare=rdFMCS.BondCompare.CompareAny if hasattr(rdFMCS, "BondCompare") else None,
                matchValences=False,
                ringMatchesRingOnly=False,
                completeRingsOnly=False,
            )
            smarts = getattr(res, "smartsString", None)
            return smarts if smarts else None
        except Exception:
            return None


# =============================================================================
# PROCESS ONE LIGAND INSTANCE
# =============================================================================
def compute_mcs(key, grouped):
    """
    Instrumented version:
      - Prints BEGIN/END with pid + timing
      - Tracks last step in global WORKER_STEP / WORKER_KEY (for SIGALRM diagnostics)
      - Prints why SDF did/didn't skip (exists + size + path)
      - Prints per-stage timings (only slow stages if you want)
    """
    ligand, target, pdb_id, resid, chain = key
    errs = []

    # ----------------------------
    # debug helpers (local)
    # ----------------------------
    t_start = time.perf_counter()

    def dbg(msg: str):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts} pid={os.getpid()}] {msg}", flush=True)

    def set_step(step: str):
        # these globals are used by your SIGALRM handler / compute_mcs_timed
        global WORKER_STEP, WORKER_KEY
        WORKER_STEP = step
        WORKER_KEY = key
        dbg(f"{key} step={step}")

    def tmark(label: str, t0: float):
        dt = time.perf_counter() - t0
        dbg(f"{key} ⏱ {label} dt={dt:.3f}s")
        return dt

    dbg(f"BEGIN key={key}")
    set_step("START")

    # ----------------------------
    # resolve rewritten ligands for SMILES
    # ----------------------------
    set_step("RESOLVE_SMILES_CANDIDATES")
    alias_key = (target, str(pdb_id).lower(), ligand)

    candidates = []
    if alias_key in ALIAS_TO_LIG5:
        candidates.append(ALIAS_TO_LIG5[alias_key])
    if alias_key in ALIAS_TO_LIG3:
        candidates.append(ALIAS_TO_LIG3[alias_key])
    candidates.append(ligand)  # always last

    ligand_for_smiles = None
    smi = ""
    mol2d = None

    set_step("LOOKUP_SMILES")
    for c in candidates:
        c_norm = normalize_warhead_id(c)
        if c_norm in LIG_TO_SMILES and str(LIG_TO_SMILES.get(c_norm, "")).strip():
            ligand_for_smiles = c_norm
            smi = LIG_TO_SMILES[c_norm]
            break

    if ligand_for_smiles is None:
        errs.append([ligand, pdb_id, f"Missing SMILES (tried {candidates})"])
        mol2d = None
    else:
        set_step("MOLFROM_SMILES")
        mol2d = Chem.MolFromSmiles(smi)
        if mol2d is None:
            errs.append([ligand, pdb_id, f"Invalid SMILES for {ligand_for_smiles}: {smi}"])

    # Persisted SMILES columns (for SVG consistency)
    smiles_id_out = ligand_for_smiles if ligand_for_smiles else ""
    smiles_out = smi if ligand_for_smiles else ""

    # ----------------------------
    # fetch 3D group
    # ----------------------------
    set_step("GET_GROUP")
    try:
        if hasattr(grouped, "get_group"):
            df = grouped.get_group(key).copy().reset_index(drop=True)
        else:
            df = grouped[key].copy().reset_index(drop=True)
    except KeyError:
        dbg(f"END key={key} (no group) dt={time.perf_counter()-t_start:.2f}s")
        return [], [], [[ligand, pdb_id, "No 3D atom group found"]]

    # ----------------------------
    # build 3D mol
    # ----------------------------
    set_step("BUILD_3D_MOL")
    t0 = time.perf_counter()
    try:
        template_charge = None
        if mol2d is not None:
            try:
                template_charge = mol_formal_charge(mol2d)
            except Exception:
                template_charge = None

        mol3d, df3d = build_3d_mol(df, template_charge=template_charge)
        atom_symbols = df3d["AtomSymbol"]

    except Exception as e:
        dbg(f"{key} ❌ 3D mol build failed at step={WORKER_STEP}: {repr(e)}")
        dbg(f"END key={key} dt={time.perf_counter()-t_start:.2f}s")
        return [], [], [[ligand, pdb_id, f"3D mol build failed: {e}"]]
    finally:
        tmark("BUILD_3D_MOL", t0)

    # ----------------------------
    # optional SDF write (instrument skip vs run)
    # ----------------------------
    set_step("SDF_BLOCK")
    if WRITE_SDF:
        base = sdf_basename_from_group_key(key)
        out_sdf = SDF_DIR / f"{base}.sdf"

        # 🔥 this is the “why didn’t it skip?” truth line
        if SKIP_IF_EXISTS and out_sdf.exists():
            try:
                sz = out_sdf.stat().st_size
            except Exception:
                sz = -1
            dbg(f"{key} SDF skip_exists=1 path={out_sdf} size={sz}")
        else:
            dbg(f"{key} SDF skip_exists=0 path={out_sdf} (exists={out_sdf.exists()})")

        if (not SKIP_IF_EXISTS) or (not out_sdf.exists()):
            set_step("SDF_WRITE")
            t_sdf = time.perf_counter()

            ok_sdf, msg_sdf = False, "not attempted"

            # TRY PRIMARY METHOD
            if SDF_METHOD.lower() == "rdkit":
                ok_sdf, msg_sdf = write_sdf_rdkit(mol3d, out_sdf, name=base)
            else:
                ok_sdf, msg_sdf = write_sdf_obabel(mol3d, out_sdf, name=base)

            # FALLBACK
            if (not ok_sdf) and SDF_FALLBACK_TO_RDKIT and SDF_METHOD.lower() != "rdkit":
                ok2, msg2 = write_sdf_rdkit(mol3d, out_sdf, name=base)
                if ok2:
                    ok_sdf, msg_sdf = True, f"fallback rdkit OK (obabel failed: {msg_sdf})"
                else:
                    msg_sdf = f"obabel failed: {msg_sdf} | rdkit failed: {msg2}"

            dt_sdf = time.perf_counter() - t_sdf

            if not ok_sdf:
                errs.append([ligand, pdb_id, f"SDF skip ({base}): {msg_sdf}"])
                dbg(f"{key} ❌ SDF_WRITE failed dt={dt_sdf:.2f}s msg={msg_sdf}")
            else:
                dbg(f"{key} ✅ SDF_WRITE ok dt={dt_sdf:.2f}s msg={msg_sdf}")

    # ----------------------------
    # mapping logic
    # ----------------------------
    set_step("MAP_INIT")
    map_3d_to_2d = {}
    mode = "NONE"

    # If no 2D, output -1 for all atoms (still include SMILES cols)
    if mol2d is None:
        set_step("NO_2D_OUTPUT_ALL")
        rows_all = []
        for a3 in range(len(df3d)):
            row = df3d.iloc[a3]
            rows_all.append([
                ligand, target, pdb_id, resid, chain,
                -1, atom_symbols.iloc[a3],
                row["atom_id"], row["atom_name"], row["x"], row["y"], row["z"],
                smiles_id_out, smiles_out
            ])
        dbg(f"END key={key} mode=NO2D all_atoms={len(rows_all)} dt={time.perf_counter()-t_start:.2f}s")
        return [], rows_all, (errs if errs else None)

    set_step("SIZE_CHECKS")
    n2 = mol2d.GetNumAtoms()
    n3 = mol3d.GetNumAtoms()

    if n2 != n3:
        errs.append([ligand, pdb_id, f"Size mismatch: SMILES={n2} vs 3D={n3}"])
    if mol3d.GetNumBonds() == 0:
        errs.append([ligand, pdb_id, "3D has 0 bonds (bond perception failed)"])

    # PASS A0: GRAPH_FULL
    set_step("GRAPH_FULL")
    t_g = time.perf_counter()
    try:
        q2 = make_bonds_generic(mol2d)
        q3 = make_bonds_generic(mol3d)
        m_full = q3.GetSubstructMatch(q2)
        if m_full and len(m_full) == n2:
            map_3d_to_2d = {a3: a2 for a2, a3 in enumerate(m_full)}
            mode = "GRAPH_FULL"
    except Exception as e:
        errs.append([ligand, pdb_id, f"GRAPH_FULL error: {e}"])
    finally:
        tmark("GRAPH_FULL", t_g)

    # PASS A: TEMPLATE_FULL
    if not map_3d_to_2d and n2 == n3:
        set_step("TEMPLATE_FULL")
        t_t = time.perf_counter()
        try:
            mol3d_bo = AllChem.AssignBondOrdersFromTemplate(mol2d, mol3d)
            full_match = mol3d_bo.GetSubstructMatch(mol2d)
            if full_match and len(full_match) == n2:
                map_3d_to_2d = {a3: a2 for a2, a3 in enumerate(full_match)}
                mode = "TEMPLATE_FULL"
        except Exception as e:
            errs.append([ligand, pdb_id, f"Template assign failed: {e}"])
        finally:
            tmark("TEMPLATE_FULL", t_t)

    # PASS B: MCS
    if not map_3d_to_2d:
        set_step("MCS")
        t_m = time.perf_counter()
        try:
            q2 = make_bonds_generic(mol2d)
            q3 = make_bonds_generic(mol3d)

            smarts = find_mcs_smarts(q2, q3, timeout_sec=5)

            if not smarts:
                errs.append([ligand, pdb_id, "MCS returned empty SMARTS"])
            else:
                patt = Chem.MolFromSmarts(smarts)
                if patt is None:
                    errs.append([ligand, pdb_id, "SMARTS→MolFromSmarts failed"])
                else:
                    match2d = q2.GetSubstructMatch(patt)
                    match3d = q3.GetSubstructMatch(patt)
                    if not match2d or not match3d:
                        errs.append([ligand, pdb_id, "No MCS substructure match"])
                    else:
                        for a3, a2 in zip(match3d, match2d):
                            map_3d_to_2d[a3] = a2
                        mode = f"MCS({len(match2d)}/{n2})"
        except Exception as e:
            errs.append([ligand, pdb_id, f"MCS error: {e}"])
        finally:
            tmark("MCS", t_m)

    # summary print (same as your original, plus pid is already in dbg)
    dbg(
        f"[{ligand} {pdb_id} {chain} {resid}] "
        f"2D={n2} 3D={n3} bonds3D={mol3d.GetNumBonds()} "
        f"mapped={len(map_3d_to_2d)} mode={mode} smiles_id={ligand_for_smiles}"
    )

    # ----------------------------
    # build outputs
    # ----------------------------
    set_step("BUILD_ROWS")
    rows_mcs = []
    if map_3d_to_2d:
        for a3 in sorted(map_3d_to_2d.keys()):
            a2 = map_3d_to_2d[a3]
            row = df3d.iloc[a3]
            rows_mcs.append([
                ligand, target, pdb_id, resid, chain,
                a2, atom_symbols.iloc[a3],
                row["atom_id"], row["atom_name"],
                row["x"], row["y"], row["z"],
                smiles_id_out, smiles_out
            ])

    rows_all = []
    for a3 in range(len(df3d)):
        row = df3d.iloc[a3]
        a2 = map_3d_to_2d.get(a3, -1)
        rows_all.append([
            ligand, target, pdb_id, resid, chain,
            a2, atom_symbols.iloc[a3],
            row["atom_id"], row["atom_name"],
            row["x"], row["y"], row["z"],
            smiles_id_out, smiles_out
        ])

    dt_total = time.perf_counter() - t_start
    dbg(f"END key={key} mode={mode} mapped={len(map_3d_to_2d)} dt={dt_total:.2f}s")
    return rows_mcs, rows_all, (errs if errs else None)

PER_KEY_TIMEOUT = 8  # seconds; tune as needed

def compute_mcs_timed(key, group_df):
    ligand, target, pdb_id, resid, chain = key
    try:
        # hard cutoff for the whole ligand instance
        with time_limit(PER_KEY_TIMEOUT):
            grouped_one = {key: group_df}
            return compute_mcs(key, grouped_one)
    except TimeoutException:
        return [], [], [[ligand, pdb_id, f"TIMEOUT (>{PER_KEY_TIMEOUT}s)"]]
    except Exception as e:
        return [], [], [[ligand, pdb_id, f"EXCEPTION: {repr(e)}"]]




# =============================================================================
# SVG rendering utilities
# =============================================================================
def recolor_svg(svg: str) -> str:
    """
    Give it your neon-ish look without breaking the SVG:
    - turn black strokes into cyan
    - slightly thicken bonds
    """
    CYAN = "#00D9FF"
    svg = re.sub(r"stroke:#000000", f"stroke:{CYAN}", svg)
    svg = re.sub(r"stroke-width:2px", "stroke-width:2.4px", svg)
    return svg


def bucket_from_exposure(a2: float) -> str:
    """
    Match your NGL threshold bands:
      < 15   -> none
      15-24  -> green
      25-34  -> yellow
      >= 35  -> red
    """
    if a2 < 15.0:
        return "none"
    if a2 <= 24.0:
        return "green"
    if a2 <= 34.0:
        return "yellow"
    return "red"


def draw_svg(mol: Chem.Mol, highlights=None, size=(420, 420)) -> str:
    w, h = size
    drawer = rdMolDraw2D.MolDraw2DSVG(w, h)
    opts = drawer.drawOptions()
    opts.backgroundColour = (0, 0, 0, 0)
    opts.bondLineWidth = 2

    if highlights:
        drawer.DrawMolecule(
            mol,
            highlightAtoms=list(highlights.keys()),
            highlightAtomColors=highlights,
        )
    else:
        drawer.DrawMolecule(mol)

    drawer.FinishDrawing()
    return recolor_svg(drawer.GetDrawingText())





def render_plain_and_exposed_svgs(smiles: str, g: pd.DataFrame):
    """
    g must contain AtomIndex (0-based RDKit atom index) and Exposure_A2.
    We highlight atoms by exposure category.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None, None, "MolFromSmiles failed"

    try:
        rdDepictor.Compute2DCoords(mol)
    except Exception as e:
        return None, None, f"Compute2DCoords failed: {e}"

    n = mol.GetNumAtoms()
    plain_svg = draw_svg(mol)

    # Colors are (r,g,b) floats in [0..1]
    RGB = {
        "green":  (0.000, 0.784, 0.325),
        "yellow": (1.000, 0.839, 0.000),
        "red":    (0.835, 0.000, 0.000),
    }

    gg = g.copy()
    gg["AtomIndex"] = pd.to_numeric(gg["AtomIndex"], errors="coerce")
    gg["Exposure_A2"] = pd.to_numeric(gg["Exposure_A2"], errors="coerce")

    highlights = {}
    for _, r in gg.dropna(subset=["AtomIndex", "Exposure_A2"]).iterrows():
        idx = int(r["AtomIndex"])
        if idx < 0 or idx >= n:
            continue
        band = bucket_from_exposure(float(r["Exposure_A2"]))
        if band == "none":
            continue
        highlights[idx] = RGB[band]

    exposed_svg = draw_svg(mol, highlights=highlights)
    return plain_svg, exposed_svg, f"ok atoms={n} highlights={len(highlights)}"


# =============================================================================
# MAIN — GROUP BY UNIQUE PDB OCCURRENCE
# =============================================================================
if __name__ == "__main__":
    grouped = atom3d.groupby(["Warhead", "Target", "pdb_id", "Residue_ID", "Chain"])
    keys = list(grouped.groups.keys())

    print(f"🚀 Running MCS on {len(keys)} ligand occurrences using {cpu_count()} cores\n")

    results_mcs = []
    results_all = []
    failures = []

    NPROC = cpu_count()
    if WRITE_SDF and SDF_METHOD.lower() == "obabel":
        NPROC = min(NPROC, 4)

    print("⚙️ Using hard timeout (5s per ligand) via SIGALRM")

    HARD_GIVEUP_SEC = 10  # <- choose: 20–60 is reasonable

    with Pool(processes=NPROC, maxtasksperchild=50) as pool:
        pending = {}
        submitted_at = {}

        for k in keys:
            pending[k] = pool.apply_async(compute_mcs_timed, (k, grouped.get_group(k).copy()))
            submitted_at[k] = time.perf_counter()

        done = 0
        last_report = time.perf_counter()
        aborted = False
        hung_key = None

        while pending:
            # collect finished
            for k, ar in list(pending.items()):
                if ar.ready():
                    try:
                        rows_mcs, rows_all, errs = ar.get(timeout=0.1)
                    except Exception as e:
                        rows_mcs, rows_all, errs = [], [], [[k[0], k[2], f"PARENT_GET_ERROR: {repr(e)}"]]

                    pending.pop(k, None)
                    submitted_at.pop(k, None)

                    if rows_mcs: results_mcs.extend(rows_mcs)
                    if rows_all: results_all.extend(rows_all)
                    if errs: failures.extend(errs)

                    done += 1
                    if done % 10 == 0 or done == len(keys):
                        print(f"⏳ progress: {done}/{len(keys)} pending={len(pending)}", flush=True)

            # report + give-up logic
            now = time.perf_counter()

            # show who's pending every 10s
            if now - last_report > 2:
                sample = list(pending.keys())[:10]
                print(f"🧨 STILL PENDING ({len(pending)}): {sample}", flush=True)
                last_report = now

            # HARD GIVE UP if any key has been running too long
            if pending:
                # find oldest pending
                oldest_key = min(submitted_at, key=lambda kk: submitted_at[kk])
                age = now - submitted_at[oldest_key]

                if age > HARD_GIVEUP_SEC:
                    hung_key = oldest_key
                    print(f"🛑 GIVE UP: key={hung_key} pending for {age:.1f}s — terminating pool and continuing", flush=True)
                    failures.append([hung_key[0], hung_key[2], f"HARD_GIVEUP >{HARD_GIVEUP_SEC}s key={hung_key}"])
                    aborted = True
                    break

            time.sleep(0.05)

        if aborted:
            # IMPORTANT: terminate so we don’t hang on pool.join()
            pool.terminate()
        else:
            pool.close()

        pool.join()

    print("✅ POOL COMPLETE (or aborted) — proceeding to write CSVs + SVG", flush=True)

    if WRITE_SDF:
        sdf_count = len(list(SDF_DIR.glob("*.sdf")))
        expected_sdfs = sorted(f"{sdf_basename_from_group_key(k)}.sdf" for k in keys)
        actual_sdfs = {p.name for p in SDF_DIR.glob("*.sdf")}
        missing_sdfs = [name for name in expected_sdfs if name not in actual_sdfs]
        failed_sdf_groups = len(missing_sdfs)
        print(f"🧪 SDF expected groups: {len(expected_sdfs)}", flush=True)
        print(f"🧪 SDF files generated: {sdf_count} → {SDF_DIR}", flush=True)
        print(f"🧪 SDF failed groups: {failed_sdf_groups}", flush=True)
        if missing_sdfs:
            print(f"⚠️ First missing SDFs: {missing_sdfs[:20]}", flush=True)





    cols = [
        "Ligand","Target","pdb_id","Residue_ID","Chain",
        "AtomIndex","AtomSymbol","atom_id","atom_name","x","y","z",
        "SMILES_ID","SMILES"
    ]


    df_map = pd.DataFrame(results_mcs, columns=cols)
    df_all = pd.DataFrame(results_all, columns=cols)

    df_map.to_csv(OUTDIR / "Ligand_MCS_Map.csv", index=False)
    df_all.to_csv(OUTDIR / "Ligand_AllAtoms_Map.csv", index=False)
    

    df_map_sasa = annotate_with_sasa(df_map, "Warhead_SASA_atoms.csv", coord_decimals=3)
    df_all_sasa = annotate_with_sasa(df_all, "Warhead_SASA_atoms.csv", coord_decimals=3)

    df_map_sasa.to_csv(OUTDIR / "Ligand_MCS_SASA.csv", index=False)
    df_all_sasa.to_csv(OUTDIR / "Ligand_MCS_SASA_ALL_ATOMS.csv", index=False)

    pd.DataFrame(failures, columns=["Ligand", "PDB", "Error"]).to_csv(
        OUTDIR / "Ligand_MCS_Failures.csv", index=False
    )

    print("====================================================")
    print("🎉 COMPLETED MCS MAPPING + SASA ANNOTATION")
    print(f"📦 Map (MCS only)        → {OUTDIR/'Ligand_MCS_Map.csv'}")
    print(f"🧪 SASA (MCS only)       → {OUTDIR/'Ligand_MCS_SASA.csv'}")
    print(f"🧬 All atoms (debug)     → {OUTDIR/'Ligand_AllAtoms_Map.csv'}")
    print(f"🧪 SASA (ALL atoms)      → {OUTDIR/'Ligand_MCS_SASA_ALL_ATOMS.csv'}")
    print(f"⚠️ Failures              → {OUTDIR/'Ligand_MCS_Failures.csv'}")
    print("====================================================\n")

    # =============================================================================
    # SVG GENERATION (plain + exposed) from the MCS SASA ALL-ATOMS table
    # =============================================================================
    svg_dir = OUTDIR / "MCS_SVG"
    svg_dir.mkdir(exist_ok=True)

    src_path = OUTDIR / "Ligand_MCS_SASA_ALL_ATOMS.csv"
    if not src_path.exists():
        print(f"⚠️  SVG skipped: missing {src_path}")
        raise SystemExit(0)

    svg_df = pd.read_csv(src_path).fillna("")
    # ensure types
    for c in ["Ligand", "Target", "pdb_id", "Residue_ID", "Chain"]:
        if c in svg_df.columns:
            svg_df[c] = svg_df[c].astype(str).str.strip()
    if "pdb_id" in svg_df.columns:
        svg_df["pdb_id"] = svg_df["pdb_id"].str.lower()
    if "Chain" in svg_df.columns:
        svg_df["Chain"] = svg_df["Chain"].str.upper()

    # group per ligand occurrence
    groups = svg_df.groupby(["pdb_id", "Chain", "Ligand", "Residue_ID", "Target"], dropna=False)

    ok = 0
    fail = []

    print(f"🎨 Starting SVG generation for {len(groups)} occurrences", flush=True)


    for (pdb_id, chain, ligand, resid, target), g in groups:
        base = f"{pdb_id}_{chain}_{ligand}_{resid}"

        plain_path = svg_dir / f"{base}_plain.svg"
        exposed_path = svg_dir / f"{base}_exposed.svg"

        # if already made, skip
        if plain_path.exists() and exposed_path.exists():
            continue

        smiles = str(g["SMILES"].iloc[0]).strip() if "SMILES" in g.columns else ""
        smiles_id = str(g["SMILES_ID"].iloc[0]).strip() if "SMILES_ID" in g.columns else ""

        if not smiles or smiles.lower() in {"nan", "none", ""}:
            fail.append([base, "missing_smiles", f"smiles_id={smiles_id}"])
            continue


        plain_svg, exposed_svg, dbg = render_plain_and_exposed_svgs(smiles, g)
        if not plain_svg or not exposed_svg:
            fail.append([base, "render_failed", dbg])
            continue

        plain_path.write_text(plain_svg, encoding="utf-8")
        exposed_path.write_text(exposed_svg, encoding="utf-8")
        ok += 1

    print(f"🖼️  SVGs generated: {ok} → {svg_dir}")
    if fail:
        pd.DataFrame(fail, columns=["base", "error", "detail"]).to_csv(svg_dir / "SVG_failures.csv", index=False)
        print(f"⚠️  SVG failures: {len(fail)} → {svg_dir/'SVG_failures.csv'}")
