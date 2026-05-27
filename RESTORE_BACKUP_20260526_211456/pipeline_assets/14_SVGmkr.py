#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SASA -> SVG (DETERMINISTIC ATOM MAPPING)

Key fixes:
1) Ligand extraction includes ATOM + HETATM (not HETATM-only).
2) AtomIndex is NOT assumed to be RDKit index.
   We try multiple interpretable spaces and choose the best mapper:
   - pdb_serial
   - pdb_order_heavy (0/1)
   - pdb_order_all   (0/1)
   - rdkit_all       (0/1)
   - rdkit_heavy     (0/1)
3) Debug SVGs are written to visually confirm indexing:
   - *_debug_idx.svg    (RDKit atom indices)
   - *_debug_serial.svg (PDB serial numbers on drawn atoms)
4) Optional atommap CSV per ligand instance for audit.
"""

import re
import time
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Callable

import pandas as pd
from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

# Bond perception (RDKit option 2)
try:
    from rdkit.Chem import rdDetermineBonds
    HAVE_RD_DETERMINE_BONDS = True
except Exception:
    HAVE_RD_DETERMINE_BONDS = False


# =============================================================================
# CONFIG
# =============================================================================
DEBUG = True

WRITE_SVGS = True
WRITE_LIGAND_PDB = True
WRITE_SDF = True

# Prefer exact cleaned PDB naming convention: {pdb}_{chain}_{ligand}.pdb inside WAR_PDB/<Target>/
USE_EXACT_WAR_PDB_FILENAME = True
ALLOW_RECURSIVE_WAR_PDB_SEARCH = True

# If ligand is absent by (resname+chain+resid), try (resname+chain) then (resname).
FALLBACK_ANY_RESID_OF_LIGAND_NAME = True

# If multiple altLocs appear, keep the first seen for each atom name.
KEEP_FIRST_ALTLOC = True

# If True, skip writing outputs that already exist.
SKIP_IF_EXISTS = True

# Step C conversion method: choose Open Babel when available, otherwise RDKit.
PDB_TO_SDF_METHOD = "obabel" if shutil.which("obabel") else "rdkit"

# NEW: include both ATOM and HETATM when selecting ligand atoms
INCLUDE_ATOM_RECORDS_FOR_LIGANDS = True

# NEW: write debug label SVGs and atom map CSV
WRITE_DEBUG_LABEL_SVGS = True
WRITE_ATOMMAP_CSV = True


# =============================================================================
# Helpers
# =============================================================================
def find_col(df, options):
    for opt in options:
        for col in df.columns:
            if opt.lower() == col.lower():
                return col
    return None


def bucket_from_exposure(a2: float):
    if a2 < 15.0:
        return "low"
    if a2 <= 35.0:
        return "medium"
    return "high"


def clean_smiles(s):
    return str(s).strip().strip('"').strip("'")


def safe_int(x):
    # tolerate things like "123.0"
    return int(float(x))


def safe_int_loose(x):
    """
    For cases where resid might contain junk (rare but happens).
    Extract the first integer substring if present.
    """
    if x is None:
        return None
    s = str(x)
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def should_write(path: Path) -> bool:
    return (not SKIP_IF_EXISTS) or (not path.exists())


def pick_smiles(group: pd.DataFrame, smiles_col: str) -> Optional[str]:
    vals = group[smiles_col].dropna().astype(str)
    if vals.empty:
        return None
    smi = vals.value_counts().idxmax()
    return clean_smiles(smi)


def is_hydrogen_atom_record(a: Dict[str, Any]) -> bool:
    el = (a.get("element") or "").strip().upper()
    nm = (a.get("atom_name") or "").strip().upper()
    if el == "H":
        return True
    if nm.startswith("H"):
        return True
    return False


# =============================================================================
# SVG utilities
# =============================================================================
def recolor_svg(svg: str):
    CYAN = "#00D9FF"
    svg = re.sub(r"stroke:#000000", f"stroke:{CYAN}", svg)
    svg = re.sub(r"stroke-width:2px", "stroke-width:2.4px", svg)
    return svg


def draw_svg(mol, highlights=None, atom_labels: Optional[Dict[int, str]] = None, add_atom_indices: bool = False):
    drawer = rdMolDraw2D.MolDraw2DSVG(420, 420)
    opts = drawer.drawOptions()
    opts.backgroundColour = (0, 0, 0, 0)
    opts.bondLineWidth = 2

    # Debug: RDKit indices
    if add_atom_indices:
        try:
            opts.addAtomIndices = True
        except Exception:
            pass

    # Debug: custom labels (e.g., PDB serial)
    if atom_labels:
        try:
            for k, v in atom_labels.items():
                opts.atomLabels[k] = str(v)
        except Exception:
            # if RDKit build doesn't expose atomLabels, just ignore
            pass

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


# =============================================================================
# Cleaned PDB lookup
# =============================================================================
def find_cleaned_pdb_for_instance(
    res_path: Path,
    target: Optional[str],
    pdb_id: str,
    chain: str,
    ligand: str
) -> Optional[Path]:
    war_root = res_path / "WAR_PDB"
    pid = str(pdb_id).strip().lower()
    ch = str(chain).strip()
    lig = str(ligand).strip()

    if USE_EXACT_WAR_PDB_FILENAME and target:
        exact = war_root / str(target) / f"{pid}_{ch}_{lig}.pdb"
        if exact.exists():
            return exact
        exact2 = war_root / str(target) / f"{pid.upper()}_{ch}_{lig}.pdb"
        if exact2.exists():
            return exact2

    if ALLOW_RECURSIVE_WAR_PDB_SEARCH and war_root.exists():
        fname = f"{pid}_{ch}_{lig}.pdb"
        hits = list(war_root.rglob(fname))
        if hits:
            return hits[0]
        fname2 = f"{pid.upper()}_{ch}_{lig}.pdb"
        hits = list(war_root.rglob(fname2))
        if hits:
            return hits[0]

    return None


# =============================================================================
# PDB parsing + ligand-only PDB extraction
# =============================================================================
def _pdb_atom_line_ok(line: str) -> bool:
    return line.startswith("HETATM") or line.startswith("ATOM  ")


def _parse_pdb_atom_line(line: str) -> Optional[Dict[str, Any]]:
    if len(line) < 54:
        return None

    rec = line[0:6].strip()
    if rec not in ("ATOM", "HETATM"):
        return None

    try:
        serial = int(line[6:11])
    except Exception:
        serial = None

    atom_name = line[12:16].rstrip()
    altloc = line[16:17].strip()
    resname = line[17:20].strip()
    chain = line[21:22].strip()
    try:
        resid = int(line[22:26])
    except Exception:
        resid = None

    try:
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
    except Exception:
        return None

    element = ""
    if len(line) >= 78:
        element = line[76:78].strip()

    return {
        "record": rec,
        "serial": serial,
        "atom_name": atom_name.strip(),
        "altloc": altloc,
        "resname": resname,
        "chain": chain,
        "resid": resid,
        "x": x,
        "y": y,
        "z": z,
        "element": element,
        "raw": line.rstrip("\n"),
    }


def extract_ligand_pdb_text(
    pdb_path: Path,
    ligand_resname: str,
    chain: Optional[str] = None,
    resid: Optional[int] = None
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """
    Returns:
      - ligand-only PDB text (ATOM/HETATM lines + filtered CONECT + END)
      - meta dict
      - picked_atoms list (parsed dicts, useful for mapping/debug)
    """
    ligand_resname = str(ligand_resname).strip().upper()
    chain = (str(chain).strip() if chain is not None else None)
    if chain == "":
        chain = None

    lines = pdb_path.read_text(errors="ignore").splitlines()

    atoms: List[Dict[str, Any]] = []
    conect_lines: List[str] = []
    for ln in lines:
        if ln.startswith("CONECT"):
            conect_lines.append(ln.rstrip())
        elif _pdb_atom_line_ok(ln):
            a = _parse_pdb_atom_line(ln)
            if a:
                atoms.append(a)

    def match_atom(a):
        if a["resname"].upper() != ligand_resname:
            return False
        if chain is not None and a["chain"] != chain:
            return False
        if resid is not None and a["resid"] != resid:
            return False
        # optionally ignore record type
        if not INCLUDE_ATOM_RECORDS_FOR_LIGANDS and a["record"] != "HETATM":
            return False
        return True

    picked = [a for a in atoms if match_atom(a)]

    if not picked and FALLBACK_ANY_RESID_OF_LIGAND_NAME:
        if chain is not None:
            picked = [a for a in atoms if (a["resname"].upper() == ligand_resname and a["chain"] == chain)]
        else:
            picked = [a for a in atoms if (a["resname"].upper() == ligand_resname)]

        if not INCLUDE_ATOM_RECORDS_FOR_LIGANDS:
            picked = [a for a in picked if a["record"] == "HETATM"]

    if not picked:
        return None, None, None

    # altLoc handling
    if KEEP_FIRST_ALTLOC:
        seen = set()
        uniq = []
        for a in picked:
            key = (a["atom_name"],)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(a)
        picked = uniq

    ligand_serials = {a["serial"] for a in picked if a.get("serial") is not None}

    out_lines: List[str] = [a["raw"] for a in picked]

    # Filter CONECT lines to ligand-only
    for c in conect_lines:
        parts = c.split()
        if len(parts) < 2:
            continue
        try:
            src = int(parts[1])
        except Exception:
            continue
        if src not in ligand_serials:
            continue

        kept_targets: List[int] = []
        for p in parts[2:]:
            try:
                t = int(p)
            except Exception:
                continue
            if t in ligand_serials:
                kept_targets.append(t)

        if kept_targets:
            out_lines.append("CONECT{:5d}{}".format(src, "".join(f"{t:5d}" for t in kept_targets)))

    out_lines.append("END")

    meta = {
        "resname": ligand_resname,
        "chain": chain,
        "resid": resid,
        "picked_atoms": len(picked),
        "pdb_path": str(pdb_path),
    }
    return "\n".join(out_lines) + "\n", meta, picked


# =============================================================================
# RDKit mol construction from ligand-only PDB text
# =============================================================================
def mol_from_ligand_pdb_text(pdb_text: str) -> Optional[Chem.Mol]:
    mol = Chem.MolFromPDBBlock(pdb_text, removeHs=False, sanitize=False)
    if mol is None:
        return None

    # Build bonds if missing
    if HAVE_RD_DETERMINE_BONDS and mol.GetNumBonds() == 0:
        try:
            rdDetermineBonds.DetermineConnectivity(mol)
        except Exception:
            pass
        if mol.GetNumBonds() == 0:
            try:
                rdDetermineBonds.DetermineBonds(mol)
            except Exception:
                pass

    # Try sanitization for clean drawing; keep if fails
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        pass

    return mol


def _pdbinfo(atom: Chem.Atom):
    info = atom.GetPDBResidueInfo()
    return info


def _atom_serial(atom: Chem.Atom) -> Optional[int]:
    info = _pdbinfo(atom)
    if info is None:
        return None
    try:
        return int(info.GetSerialNumber())
    except Exception:
        return None


def _atom_pdbname(atom: Chem.Atom) -> Optional[str]:
    info = _pdbinfo(atom)
    if info is None:
        return None
    try:
        return str(info.GetName()).strip()
    except Exception:
        return None


# =============================================================================
# AtomIndex -> drawn heavy-atom idx mapping (deterministic)
# =============================================================================
def build_indexing_context(mol_all: Chem.Mol, picked_atoms: List[Dict[str, Any]]):
    """
    Builds everything needed to interpret AtomIndex in multiple possible spaces
    and map it into "drawn heavy-atom indices" (mol_draw = RemoveHs(mol_all)).
    """
    n_all = mol_all.GetNumAtoms()

    # all_idx -> heavy_idx (None for H)
    all_to_heavy: List[Optional[int]] = [None] * n_all
    heavy_names: List[Optional[str]] = []
    heavy_serials: List[Optional[int]] = []

    h = 0
    for ai, atom in enumerate(mol_all.GetAtoms()):
        if atom.GetSymbol() == "H":
            all_to_heavy[ai] = None
            continue
        all_to_heavy[ai] = h
        heavy_names.append(_atom_pdbname(atom))
        heavy_serials.append(_atom_serial(atom))
        h += 1

    n_heavy = h

    # PDB order lists from extracted PDB text (serials)
    pdb_serials_all: List[int] = [a["serial"] for a in picked_atoms if a.get("serial") is not None]
    pdb_serials_heavy: List[int] = [a["serial"] for a in picked_atoms if (a.get("serial") is not None and not is_hydrogen_atom_record(a))]

    # serial -> all_idx map from RDKit (if PDB residue info exists)
    serial_to_all: Dict[int, int] = {}
    for ai, atom in enumerate(mol_all.GetAtoms()):
        s = _atom_serial(atom)
        if s is None:
            continue
        if s not in serial_to_all:
            serial_to_all[s] = ai

    # serial -> heavy_idx
    serial_to_heavy: Dict[int, int] = {}
    for s, ai in serial_to_all.items():
        hi = all_to_heavy[ai]
        if hi is not None:
            serial_to_heavy[s] = hi

    return {
        "n_all": n_all,
        "n_heavy": n_heavy,
        "all_to_heavy": all_to_heavy,
        "pdb_serials_all": pdb_serials_all,
        "pdb_serials_heavy": pdb_serials_heavy,
        "serial_to_all": serial_to_all,
        "serial_to_heavy": serial_to_heavy,
        "heavy_names": heavy_names,
        "heavy_serials": heavy_serials,
    }

def infer_base(atom_indices, n_atoms):
    """
    Decide whether AtomIndex is 0-based or 1-based.
    """
    vals = pd.to_numeric(atom_indices, errors="coerce").dropna().astype(int).tolist()
    if not vals:
        return 0

    # score how many indices land in-range
    def score(base):
        ok = 0
        for v in vals:
            i = v - base
            if 0 <= i < n_atoms:
                ok += 1
        return ok

    s0 = score(0)
    s1 = score(1)
    return 0 if s0 >= s1 else 1


def render_exposed_svg_from_smiles(smiles, group_df, idx_col, exp_col):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None, "MolFromSmiles failed"

    rdDepictor.Compute2DCoords(mol)
    n = mol.GetNumAtoms()

    base = infer_base(group_df[idx_col], n)

    # bucket colors
    BUCKET_RGB = {
        "low": (0.00, 0.78, 0.33),
        "medium": (1.00, 0.84, 0.00),
        "high": (0.84, 0.00, 0.00),
    }

    g = group_df.copy()
    g[idx_col] = pd.to_numeric(g[idx_col], errors="coerce")
    g[exp_col] = pd.to_numeric(g[exp_col], errors="coerce")

    highlights = {}
    for _, r in g.dropna(subset=[idx_col, exp_col]).iterrows():
        raw = int(r[idx_col])
        i = raw - base
        if 0 <= i < n:
            bucket = bucket_from_exposure(float(r[exp_col]))
            highlights[i] = BUCKET_RGB[bucket]

    svg = draw_svg(mol, highlights=highlights)
    return svg, f"ok atoms={n} base={base} highlights={len(highlights)}"




def choose_best_mapper(
    atomindex_series: pd.Series,
    ctx: Dict[str, Any],
) -> Tuple[Callable[[int], Optional[int]], str]:
    """
    Returns:
      mapper(raw_atomindex:int) -> heavy_idx (or None)
      label describing chosen mapping
    """
    raw_vals = pd.to_numeric(atomindex_series, errors="coerce").dropna().astype(int).tolist()
    if not raw_vals:
        return (lambda x: None), "no-atomindex"

    n_all = ctx["n_all"]
    n_heavy = ctx["n_heavy"]
    all_to_heavy = ctx["all_to_heavy"]
    pdb_all = ctx["pdb_serials_all"]
    pdb_heavy = ctx["pdb_serials_heavy"]
    serial_to_heavy = ctx["serial_to_heavy"]

    def score(mapper: Callable[[int], Optional[int]]) -> int:
        ok = 0
        for v in raw_vals:
            hi = mapper(v)
            if hi is not None and 0 <= hi < n_heavy:
                ok += 1
        return ok

    # Candidate mappers (map raw AtomIndex -> heavy idx)
    cands: List[Tuple[str, int, Callable[[int], Optional[int]]]] = []

    # Preference ranks (tie-breaker): higher = preferred
    PREF = {
        "pdb_serial": 50,
        "pdb_order_heavy/0": 45,
        "pdb_order_heavy/1": 44,
        "pdb_order_all/0": 40,
        "pdb_order_all/1": 39,
        "rdkit_all/0": 30,
        "rdkit_all/1": 29,
        "rdkit_heavy/0": 20,
        "rdkit_heavy/1": 19,
    }

    # pdb_serial: raw is PDB serial
    def m_pdb_serial(v: int) -> Optional[int]:
        return serial_to_heavy.get(v)

    cands.append(("pdb_serial", PREF["pdb_serial"], m_pdb_serial))

    # pdb_order_all: raw is position in picked_atoms (all atoms) list
    for off in (0, 1):
        def make_pdb_order_all(offset):
            def _m(v: int) -> Optional[int]:
                pos = v - offset
                if 0 <= pos < len(pdb_all):
                    s = pdb_all[pos]
                    return serial_to_heavy.get(s)
                return None
            return _m
        name = f"pdb_order_all/{off}"
        cands.append((name, PREF[name], make_pdb_order_all(off)))

    # pdb_order_heavy: raw is position in heavy-only picked list
    for off in (0, 1):
        def make_pdb_order_heavy(offset):
            def _m(v: int) -> Optional[int]:
                pos = v - offset
                if 0 <= pos < len(pdb_heavy):
                    s = pdb_heavy[pos]
                    return serial_to_heavy.get(s)
                return None
            return _m
        name = f"pdb_order_heavy/{off}"
        cands.append((name, PREF[name], make_pdb_order_heavy(off)))

    # rdkit_all: raw is RDKit all-atom idx
    for off in (0, 1):
        def make_rdkit_all(offset):
            def _m(v: int) -> Optional[int]:
                ai = v - offset
                if 0 <= ai < n_all:
                    return all_to_heavy[ai]
                return None
            return _m
        name = f"rdkit_all/{off}"
        cands.append((name, PREF[name], make_rdkit_all(off)))

    # rdkit_heavy: raw is heavy index directly
    for off in (0, 1):
        def make_rdkit_heavy(offset):
            def _m(v: int) -> Optional[int]:
                hi = v - offset
                if 0 <= hi < n_heavy:
                    return hi
                return None
            return _m
        name = f"rdkit_heavy/{off}"
        cands.append((name, PREF[name], make_rdkit_heavy(off)))

    # Evaluate
    scored = []
    for name, pref, mapper in cands:
        s = score(mapper)
        scored.append((s, pref, name, mapper))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_s, best_pref, best_name, best_mapper = scored[0]

    if DEBUG:
        n = len(raw_vals)
        top = scored[:5]
        msg = " | ".join([f"{nm}:{sc}/{n}" for sc, _, nm, _ in top])
        print(f"🧭 AtomIndex mapper pick: {best_name} ({best_s}/{n})  top5= {msg}")

    return best_mapper, best_name


def build_highlight_map(
    group_df: pd.DataFrame,
    idx_col: str,
    exp_col: str,
    mapper: Callable[[int], Optional[int]],
    n_heavy: int
) -> Dict[int, Tuple[float, float, float]]:
    BUCKET_RGB = {
        "low": (0.00, 0.78, 0.33),
        "medium": (1.00, 0.84, 0.00),
        "high": (0.84, 0.00, 0.00),
    }

    g = group_df.copy()
    g[idx_col] = pd.to_numeric(g[idx_col], errors="coerce")
    g[exp_col] = pd.to_numeric(g[exp_col], errors="coerce")

    highlight_map: Dict[int, Tuple[float, float, float]] = {}

    for _, r in g.dropna(subset=[idx_col, exp_col]).iterrows():
        raw = int(r[idx_col])
        hi = mapper(raw)
        if hi is None or not (0 <= hi < n_heavy):
            continue
        bucket = bucket_from_exposure(float(r[exp_col]))
        highlight_map[hi] = BUCKET_RGB[bucket]

    return highlight_map


def render_svgs_from_ligand_pdb(
    lig_pdb_text: str,
    picked_atoms: List[Dict[str, Any]],
    group_df: pd.DataFrame,
    base: str,
    out_svg_dir: Path,
    idx_col: str,
    exp_col: Optional[str]
) -> Tuple[Optional[str], Optional[str], str]:
    """
    Returns (plain_svg, exposed_svg, debug_msg).
    Draws a HEAVY-ATOM molecule (RemoveHs) and maps AtomIndex into that space.
    """
    mol_all = mol_from_ligand_pdb_text(lig_pdb_text)
    if mol_all is None:
        return None, None, "mol3d_parse_failed"

    ctx = build_indexing_context(mol_all, picked_atoms)
    mol_draw = Chem.RemoveHs(mol_all)
    n_heavy = ctx["n_heavy"]

    try:
        rdDepictor.Compute2DCoords(mol_draw)
    except Exception as e:
        return None, None, f"Compute2DCoords failed: {e}"

    # plain
    plain_svg = draw_svg(mol_draw)

    exposed_svg = None
    mapper_name = "no-exp"
    if exp_col is not None:
        mapper, mapper_name = choose_best_mapper(group_df[idx_col], ctx)
        hmap = build_highlight_map(group_df, idx_col, exp_col, mapper, n_heavy=n_heavy)
        if DEBUG:
            print(f"🎯 {base} highlights: {len(hmap)} / {n_heavy} (mapper={mapper_name})")
        exposed_svg = draw_svg(mol_draw, highlights=hmap)

    # Debug label SVGs (visual ground truth)
    if WRITE_DEBUG_LABEL_SVGS and should_write(out_svg_dir / f"{base}_debug_idx.svg"):
        (out_svg_dir / f"{base}_debug_idx.svg").write_text(
            draw_svg(mol_draw, add_atom_indices=True),
            encoding="utf-8"
        )

    if WRITE_DEBUG_LABEL_SVGS and should_write(out_svg_dir / f"{base}_debug_serial.svg"):
        # label drawn heavy idx with PDB serial numbers (from ctx heavy_serials)
        labels = {}
        for hi, serial in enumerate(ctx["heavy_serials"]):
            if serial is not None:
                labels[hi] = str(serial)
        (out_svg_dir / f"{base}_debug_serial.svg").write_text(
            draw_svg(mol_draw, atom_labels=labels),
            encoding="utf-8"
        )

    # Audit CSV
    if WRITE_ATOMMAP_CSV and should_write(out_svg_dir / f"{base}_atommap.csv"):
        rows = []
        for hi in range(n_heavy):
            rows.append({
                "draw_heavy_idx": hi,
                "pdb_serial": ctx["heavy_serials"][hi],
                "pdb_atom_name": ctx["heavy_names"][hi],
            })
        pd.DataFrame(rows).to_csv(out_svg_dir / f"{base}_atommap.csv", index=False)

    return plain_svg, exposed_svg, f"ok heavy_atoms={n_heavy} mapper={mapper_name}"


# =============================================================================
# PDB -> SDF
# =============================================================================
def pdb_to_sdf_rdkit(pdb_file: Path, sdf_file: Path) -> Tuple[bool, str]:
    if not HAVE_RD_DETERMINE_BONDS:
        return False, "rdDetermineBonds not available; use Open Babel."

    try:
        txt = pdb_file.read_text(errors="ignore")
        mol = Chem.MolFromPDBBlock(txt, removeHs=False, sanitize=False)
        if mol is None:
            return False, "RDKit MolFromPDBBlock returned None."
        if mol.GetNumConformers() == 0:
            return False, "Parsed molecule has no conformers."

        if mol.GetNumBonds() == 0:
            try:
                rdDetermineBonds.DetermineConnectivity(mol)
            except Exception:
                pass
            if mol.GetNumBonds() == 0:
                try:
                    rdDetermineBonds.DetermineBonds(mol)
                except Exception:
                    pass

        try:
            Chem.SanitizeMol(mol)
        except Exception:
            pass

        mol.SetProp("_Name", pdb_file.stem)
        sdf_file.parent.mkdir(parents=True, exist_ok=True)
        w = Chem.SDWriter(str(sdf_file))
        w.write(mol)
        w.close()
        return True, f"OK atoms={mol.GetNumAtoms()} bonds={mol.GetNumBonds()}"

    except Exception as e:
        return False, f"Exception: {e}"


def pdb_to_sdf_obabel(pdb_file: Path, sdf_file: Path) -> Tuple[bool, str]:
    try:
        sdf_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["obabel", "-ipdb", str(pdb_file), "-osdf", "-O", str(sdf_file)]
        p = subprocess.run(cmd, capture_output=True, text=True)

        if p.returncode != 0:
            return False, (p.stderr.strip() or p.stdout.strip() or f"obabel failed code={p.returncode}")
        if not sdf_file.exists() or sdf_file.stat().st_size == 0:
            return False, "SDF not created or empty."

        warn = (p.stderr.strip() or "")
        if warn:
            return True, f"OK (warnings): {warn.splitlines()[0][:180]}"
        return True, "OK"

    except Exception as e:
        return False, f"Exception: {e}"


# =============================================================================
# Main
# =============================================================================
def main():
    res_path = Path("TARGET_RESULTS")
    master_file = res_path / "3DSASAmapped.csv"
    if not master_file.exists():
        print("❌ No merged SASA file found:", master_file)
        raise SystemExit(1)

    out_svg_dir = res_path / "LIGAND_SVGS"
    out_pdb_dir = res_path / "LIGAND_PDB"
    out_sdf_dir = res_path / "LIGAND_SDF"

    if WRITE_SVGS:
        out_svg_dir.mkdir(exist_ok=True)
    if WRITE_LIGAND_PDB:
        out_pdb_dir.mkdir(exist_ok=True)
    if WRITE_SDF:
        out_sdf_dir.mkdir(exist_ok=True)

    df = pd.read_csv(master_file)

    # Columns
    PDB_COL = find_col(df, ["pdb_id", "pdb"])
    WAR_COL = find_col(df, ["Warhead", "ligand", "Ligand"])
    CHN_COL = find_col(df, ["Chain", "chain"])
    TAR_COL = find_col(df, ["Target", "Protein", "protein", "target"])
    SMI_COL = find_col(df, ["SMILES"])
    EXP_COL = find_col(df, ["Exposure_A2", "SASA", "Exposure"])
    IDX_COL = find_col(df, ["AtomIndex"])
    RES_COL = find_col(df, ["Residue_ID", "Residue", "residue_id", "resid"])

    if not all([PDB_COL, WAR_COL, CHN_COL, SMI_COL, IDX_COL]):
        print("❌ Missing required columns in 3DSASAmapped.csv")
        print("   Found:", list(df.columns))
        raise SystemExit(1)

    have_exposure = (EXP_COL is not None)

    svg_count = 0
    ligand_pdb_count = 0
    ligand_pdb_skipped = 0

    for (pdb_id, chain, warhead), group in df.groupby([PDB_COL, CHN_COL, WAR_COL]):
        pdb_id_s = str(pdb_id).strip()
        chain_s = str(chain).strip()
        lig_s = str(warhead).strip()
        base = f"{pdb_id_s}_{chain_s}_{lig_s}"

        plain_svg_path = out_svg_dir / f"{base}_plain.svg"
        exposed_svg_path = out_svg_dir / f"{base}_exposed.svg"
        ligand_pdb_path = out_pdb_dir / f"{base}.pdb"

        want_plain_svg = WRITE_SVGS and should_write(plain_svg_path)
        want_exposed_svg = WRITE_SVGS and have_exposure and should_write(exposed_svg_path)
        want_ligand_pdb = WRITE_LIGAND_PDB and should_write(ligand_pdb_path)

        if not (want_plain_svg or want_exposed_svg or want_ligand_pdb):
            continue

        target_s = None
        if TAR_COL is not None:
            tvals = group[TAR_COL].dropna().unique()
            if len(tvals):
                target_s = str(tvals[0]).strip()

        pdb_path = find_cleaned_pdb_for_instance(res_path, target_s, pdb_id_s, chain_s, lig_s)

        lig_text = None
        meta = None
        picked_atoms = None

        if pdb_path is not None:
            resid_val = None
            if RES_COL is not None:
                vals = group[RES_COL].dropna().unique()
                if len(vals):
                    resid_val = safe_int_loose(vals[0])

            lig_text, meta, picked_atoms = extract_ligand_pdb_text(
                pdb_path=pdb_path,
                ligand_resname=lig_s,
                chain=chain_s,
                resid=resid_val,
            )

        # ---- STEP A: SVGs ----
        # ---- STEP A: SVGs ----
        if WRITE_SVGS and (want_plain_svg or want_exposed_svg):

            smiles = pick_smiles(group, SMI_COL)

            # 1) plain SVG: still fine from SMILES
            if want_plain_svg and smiles:
                mol2d = Chem.MolFromSmiles(smiles)
                if mol2d:
                    rdDepictor.Compute2DCoords(mol2d)
                    plain_svg_path.write_text(draw_svg(mol2d), encoding="utf-8")
                    svg_count += 1

            # 2) exposed SVG: MUST be from SMILES because AtomIndex is SMILES-order
            if want_exposed_svg and smiles:
                exposed_svg, dbg = render_exposed_svg_from_smiles(
                    smiles=smiles,
                    group_df=group,
                    idx_col=IDX_COL,
                    exp_col=EXP_COL
                )
                if DEBUG:
                    print(f"🎯 EXPOSED {base} via SMILES -> {dbg}")
                if exposed_svg:
                    exposed_svg_path.write_text(exposed_svg, encoding="utf-8")
                    svg_count += 1
                else:
                    if DEBUG:
                        print(f"⚠️ EXPOSED {base} failed via SMILES")

        # ---- STEP B: Ligand-only PDB ----
        if WRITE_LIGAND_PDB and want_ligand_pdb:
            if lig_text is None:
                ligand_pdb_skipped += 1
                if DEBUG:
                    msg = f"⚠️ Could not extract ligand atoms for {base}"
                    if pdb_path is None:
                        msg += " (cleaned PDB not found)"
                    else:
                        msg += f" from {pdb_path.name}"
                    print(msg)
            else:
                ligand_pdb_path.write_text(lig_text, encoding="utf-8")
                ligand_pdb_count += 1
                if DEBUG and meta:
                    print(
                        f"✅ LIGAND_PDB {base} from={Path(meta['pdb_path']).name} "
                        f"resname={meta['resname']} chain={chain_s} resid={meta.get('resid')} picked={meta['picked_atoms']}"
                    )

    # ---- STEP C: Convert Ligand_PDB -> SDF ----
    sdf_count = 0
    sdf_skipped = 0

    if WRITE_SDF:
        pdb_files = sorted(out_pdb_dir.glob("*.pdb"))

        print("\n" + "=" * 60)
        print(f"STEP C — PDB → SDF ({PDB_TO_SDF_METHOD})")
        print(f"PDB input dir : {out_pdb_dir.resolve()}")
        print(f"SDF output dir: {out_sdf_dir.resolve()}")
        print(f"Found {len(pdb_files)} ligand PDBs")
        print("=" * 60, flush=True)

        for i, pdb_file in enumerate(pdb_files, 1):
            sdf_file = out_sdf_dir / f"{pdb_file.stem}.sdf"
            if SKIP_IF_EXISTS and sdf_file.exists():
                if DEBUG:
                    print(f"↩︎ [{i}/{len(pdb_files)}] skip (exists): {sdf_file.name}", flush=True)
                continue

            print(f"→ [{i}/{len(pdb_files)}] converting {pdb_file.name} ...", flush=True)
            t0 = time.perf_counter()

            if PDB_TO_SDF_METHOD.lower() == "rdkit":
                ok, msg = pdb_to_sdf_rdkit(pdb_file, sdf_file)
            else:
                ok, msg = pdb_to_sdf_obabel(pdb_file, sdf_file)

            dt = time.perf_counter() - t0
            if ok:
                sdf_count += 1
                print(f"✅ [{i}/{len(pdb_files)}] wrote {sdf_file.name} in {dt:.2f}s — {msg}", flush=True)
            else:
                sdf_skipped += 1
                print(f"❌ [{i}/{len(pdb_files)}] FAIL in {dt:.2f}s — {msg}", flush=True)

    # ---- Summary ----
    print("\n===================================================")
    if WRITE_SVGS:
        print(f"✅ SVGs generated        : {svg_count}")
        print(f"📁 SVG  → {out_svg_dir}")
    if WRITE_LIGAND_PDB:
        print(f"✅ Ligand PDBs generated : {ligand_pdb_count}")
        print(f"⚠️ Ligand PDBs skipped   : {ligand_pdb_skipped}")
        print(f"📁 PDB  → {out_pdb_dir}")
    if WRITE_SDF:
        print(f"✅ SDFs generated        : {sdf_count}")
        print(f"⚠️ SDFs skipped          : {sdf_skipped}")
        print(f"📁 SDF  → {out_sdf_dir}")
    print("===================================================")


if __name__ == "__main__":
    main()
