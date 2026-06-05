#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import gc
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd
from Bio.PDB import PDBParser, ShrakeRupley

# Output files
ATOM_CSV = "Warhead_SASA_atoms.csv"
SUMMARY_CSV = "Warhead_SASA_summary.csv"
FAILURE_CSV = "SASA_Failure.csv"

DEFAULT_PROBE = 1.4
THRESHOLD = 0.1
ATOM_WRITE_BATCH = 128
IS_HEROKU = bool(os.environ.get("DYNO"))
WRITE_LOCK = threading.Lock()


def env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def env_flag(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


DEFAULT_MAX_WORKERS = 1 if IS_HEROKU else min(4, max(1, os.cpu_count() or 1))
MAX_WORKERS = max(1, env_int("WARHEAD_SASA_MAX_WORKERS", DEFAULT_MAX_WORKERS))
STREAM_OUTPUT = env_flag("WARHEAD_SASA_STREAM_OUTPUT", True)
DEBUG_MEMORY = env_flag("WARHEAD_SASA_DEBUG_MEMORY", False)


def _current_rss_mb() -> float:
    status_path = Path("/proc/self/status")
    if status_path.exists():
        try:
            for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
        except Exception:
            pass
    try:
        import subprocess

        proc = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            check=False,
        )
        raw = (proc.stdout or "").strip()
        if raw:
            return round(int(raw.splitlines()[-1].strip()) / 1024.0, 1)
    except Exception:
        pass
    return 0.0


def debug_mem(message: str) -> None:
    if DEBUG_MEMORY:
        print(f"[sasa-mem] {message} rss={_current_rss_mb():.1f}MB", flush=True)


def atom_csv_header() -> list[str]:
    return [
        "Target", "pdb_id", "Warhead", "Residue_ID", "Variant",
        "Chain", "atom_id", "exact_atom", "x", "y", "z", "Exposure_A2",
    ]


def summary_csv_header() -> list[str]:
    return [
        "Target", "pdb_id", "Warhead", "Residue_ID", "Variant",
        "Total_atoms", "Exposed_atoms", "SASA_in_complex_A2",
        "%Exposed", "%Buried",
    ]


def write_headers() -> None:
    if not os.path.exists(ATOM_CSV):
        with open(ATOM_CSV, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(atom_csv_header())

    if not os.path.exists(SUMMARY_CSV):
        with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(summary_csv_header())


def append_atom_rows(rows: Iterable[list]) -> None:
    rows = list(rows)
    if not rows:
        return
    with WRITE_LOCK:
        with open(ATOM_CSV, "a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)


def append_summary_row(row: list) -> None:
    with WRITE_LOCK:
        with open(SUMMARY_CSV, "a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(row)


def pdb_metadata(pdb_path: Path) -> tuple[str, str, str, str]:
    target = pdb_path.parent.name
    stem = pdb_path.stem.split("_")
    pdb_id = stem[0]
    warhead = stem[2] if len(stem) >= 3 else "UNK"
    variant = stem[-1] if stem and stem[-1].isdigit() else "1"
    return target, pdb_id, warhead, variant


def analyze_sasa(
    pdb_path: Path,
    probe_radius: float,
    atom_sink: Optional[Callable[[list], None]] = None,
) -> tuple[list, int, list[list]]:
    target, pdb_id, warhead, variant = pdb_metadata(pdb_path)
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("s", str(pdb_path))
    debug_mem(f"parsed PDB {pdb_id}_{warhead}")

    for model in structure:
        for chain in model:
            waters = [residue.id for residue in chain if residue.resname == "HOH"]
            for residue_id in waters:
                chain.detach_child(residue_id)

    sr = ShrakeRupley(probe_radius=probe_radius)
    sr.compute(structure, level="A")
    debug_mem(f"computed SASA {pdb_id}_{warhead}")

    total_atoms = 0
    exposed_atoms = 0
    sasa_total = 0.0
    residue_id_value: str | int = "NA"
    rows_buffer: list[list] = []
    written_rows = 0
    collected_rows: list[list] = []

    def emit(row: list) -> None:
        nonlocal written_rows, rows_buffer
        if atom_sink is None:
            rows_buffer.append(row)
            return
        rows_buffer.append(row)
        if len(rows_buffer) >= ATOM_WRITE_BATCH:
            atom_sink(rows_buffer)
            written_rows += len(rows_buffer)
            rows_buffer = []

    for model in structure:
        for chain in model:
            for residue in chain:
                if residue.resname.strip() != warhead:
                    continue
                residue_id_value = residue.id[1]
                for atom in residue.get_atoms():
                    total_atoms += 1
                    sasa = float(getattr(atom, "sasa", 0.0) or 0.0)
                    if sasa <= THRESHOLD:
                        continue
                    exposed_atoms += 1
                    sasa_total += sasa
                    emit([
                        target,
                        pdb_id,
                        warhead,
                        residue_id_value,
                        variant,
                        chain.id,
                        atom.serial_number,
                        atom.name,
                        round(float(atom.coord[0]), 3),
                        round(float(atom.coord[1]), 3),
                        round(float(atom.coord[2]), 3),
                        round(sasa, 3),
                    ])

    if rows_buffer:
        if atom_sink is None:
            written_rows = len(rows_buffer)
            collected_rows = list(rows_buffer)
        else:
            atom_sink(rows_buffer)
            written_rows += len(rows_buffer)
            rows_buffer = []

    percent_exposed = (exposed_atoms / total_atoms) if total_atoms > 0 else 0.0
    summary_row = [
        target,
        pdb_id,
        warhead,
        residue_id_value,
        variant,
        total_atoms,
        exposed_atoms,
        round(sasa_total, 3),
        round(percent_exposed, 3),
        round(1 - percent_exposed, 3),
    ]

    del structure
    gc.collect()
    debug_mem(f"finished {pdb_path.name} exposed_rows={written_rows}")
    return summary_row, written_rows, collected_rows


def process_file(pdb_path: Path, probe_radius: float, stream_output: bool) -> dict:
    try:
        summary_row, written_rows, atom_rows = analyze_sasa(
            pdb_path,
            probe_radius,
            atom_sink=append_atom_rows if stream_output else None,
        )
        if not stream_output:
            append_atom_rows(atom_rows)
        append_summary_row(summary_row)
        return {
            "ok": True,
            "pdb_file": pdb_path.name,
            "summary_row": summary_row,
            "written_rows": written_rows,
        }
    except Exception as exc:
        return {
            "ok": False,
            "pdb_file": pdb_path.name,
            "error": str(exc),
        }


def discover_pdb_files(pdb_root: Path) -> list[Path]:
    return sorted(pdb_root.rglob("*.pdb"))


def write_failure_csv(rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with open(FAILURE_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=float, default=DEFAULT_PROBE)
    args = parser.parse_args()

    cifinfo = pd.read_csv("CIFdata.csv", usecols=["outdir"], dtype=str).fillna("")
    base_outdir = str(cifinfo.iloc[0]["outdir"]).rstrip("/")
    pdb_root = Path(base_outdir + "_PDB")

    if not pdb_root.is_dir():
        raise RuntimeError(f"ERROR: Directory not found: {pdb_root}")

    print(f"\n📁 Scanning WARHEAD PDB ROOT: {pdb_root}")
    print(f"📁 Step 6 input root resolved: {pdb_root.resolve()}")
    debug_mem("loaded CIFdata.csv")

    pdb_files = discover_pdb_files(pdb_root)
    print(f"🔎 Found {len(pdb_files)} PDB files")
    if pdb_files:
        print(f"🧾 Step 6 sample PDBs: {[str(p.relative_to(pdb_root)) for p in pdb_files[:10]]}")
    print(f"🧠 Using max_workers={min(MAX_WORKERS, max(1, len(pdb_files)))} stream_output={STREAM_OUTPUT}\n")

    write_headers()

    if not pdb_files:
        write_failure_csv([{
            "reason": "No PDB files found for SASA analysis",
            "pdb_root": str(pdb_root.resolve()),
            "discovered_pdb_count": 0,
        }])
        raise RuntimeError(
            f"Step 6 found 0 PDB files under {pdb_root.resolve()}. "
            f"Wrote {FAILURE_CSV}."
        )

    failures: list[dict] = []
    processed = 0
    written_rows_total = 0

    max_workers = min(MAX_WORKERS, max(1, len(pdb_files)))
    if max_workers == 1:
        for pdb_file in pdb_files:
            result = process_file(pdb_file, args.probe, STREAM_OUTPUT)
            processed += 1
            if result.get("ok"):
                summary = result["summary_row"]
                written_rows_total += int(result.get("written_rows") or 0)
                print(f"✅ {pdb_file.name}: {summary[6]}/{summary[5]} exposed ({summary[8]:.2f})")
            else:
                failures.append(result)
                print(f"❌ Error in {pdb_file.name}: {result.get('error')}")
            if processed % 25 == 0:
                print(f"📊 Progress: {processed}/{len(pdb_files)}")
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_file, pdb_file, args.probe, STREAM_OUTPUT): pdb_file for pdb_file in pdb_files}
            for future in as_completed(futures):
                pdb_file = futures[future]
                result = future.result()
                processed += 1
                if result.get("ok"):
                    summary = result["summary_row"]
                    written_rows_total += int(result.get("written_rows") or 0)
                    print(f"✅ {pdb_file.name}: {summary[6]}/{summary[5]} exposed ({summary[8]:.2f})")
                else:
                    failures.append(result)
                    print(f"❌ Error in {pdb_file.name}: {result.get('error')}")
                if processed % 25 == 0:
                    print(f"📊 Progress: {processed}/{len(pdb_files)}")

    debug_mem(f"wrote Warhead_SASA outputs processed={processed} atom_rows={written_rows_total}")

    if failures:
        write_failure_csv(failures)
        print(f"⚠️ Step 6 failures recorded: {len(failures)} rows written to {FAILURE_CSV}")
        for row in failures[:10]:
            print(f"⚠️ SASA failure: pdb={row.get('pdb_file')} error={row.get('error')}")

    print("\n🎉 WARHEAD SASA COMPLETE\n")


if __name__ == "__main__":
    main()
