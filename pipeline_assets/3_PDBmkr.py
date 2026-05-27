#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import gzip
import io
import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm
from Bio.PDB import MMCIFParser, PDBIO, Select

# =============================================================================
# CONFIG
# =============================================================================
MAX_WORKERS = 12
LIGMAP_FILE = "5CharMAP.csv"
CHAINMAP_FILE = "ChainRenameMAP.csv"
MANIFEST_FILE = "CIF_Download_Manifest.csv"

LIGMAP_COLS = ["protein", "pdb", "ligand5", "ligand3", "ligandX"]
CHAINMAP_COLS = ["pdb", "orig_chain", "new_chain"]


def normalize_pdb_id(pdb_id: str) -> str:
    return str(pdb_id or "").strip().lower()


def parse_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def verify_cif_file(path: Path) -> tuple[bool, int, str]:
    try:
        if not path.exists() or not path.is_file():
            return False, 0, "missing"
        size = path.stat().st_size
        if size <= 0:
            return False, size, "empty"
        if path.suffix.lower() == ".gz" and path.name.lower().endswith(".cif.gz"):
            with gzip.open(path, "rb") as handle:
                sample = handle.read(256)
        else:
            with path.open("rb") as handle:
                sample = handle.read(256)
        if not sample:
            return False, size, "unreadable"
        return True, size, ""
    except Exception as exc:
        return False, 0, f"verify_error: {exc}"


def path_for_log(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def safe_resolve(path: Path, job_root: Path) -> Path | None:
    try:
        resolved = path.resolve()
    except Exception:
        return None
    try:
        resolved.relative_to(job_root)
    except ValueError:
        return None
    return resolved


def recorded_candidates(recorded_path: str, job_root: Path) -> list[Path]:
    raw = str(recorded_path or "").strip()
    if not raw:
        return []
    candidate = Path(raw)
    if candidate.is_absolute():
        safe = safe_resolve(candidate, job_root)
        return [safe] if safe else []
    candidates = []
    for base in (Path.cwd(), job_root):
        safe = safe_resolve(base / candidate, job_root)
        if safe and safe not in candidates:
            candidates.append(safe)
    return candidates


def file_index_for(root: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    if not root.exists():
        return index
    for pattern in ("*.cif", "*.CIF", "*.cif.gz", "*.CIF.GZ"):
        for path in root.rglob(pattern):
            key = normalize_pdb_id(path.name.replace(".cif.gz", "").replace(".CIF.GZ", "").replace(".cif", "").replace(".CIF", ""))
            index.setdefault(key, []).append(path.resolve())
    return index


class ChainSelect(Select):
    def __init__(self, chain_id):
        self.chain_id = chain_id

    def accept_chain(self, chain):
        return chain.id == self.chain_id


def next_ligandX(idx: int) -> str:
    return f"{chr(ord('A') + (idx // 100))}{idx % 100:02d}"


def generate_chain_id(idx: int) -> str:
    if idx < 26:
        return chr(ord("A") + idx)
    if idx < 52:
        return chr(ord("a") + (idx - 26))
    return "X"


def build_single_pdb(job: dict):
    protein, pdb_id = job["protein"], job["pdb"]
    orig_chain, new_chain = job["orig_chain"], job["new_chain"]
    cif_path = job["cif_path"]

    key = (protein, pdb_id, orig_chain, job["job_index"])

    cif_file = Path(cif_path)
    ok, _size, err = verify_cif_file(cif_file)
    if not ok:
        return key, None, f"CIF missing: {cif_path} ({err})"

    try:
        parser = MMCIFParser(QUIET=True)
        if cif_file.suffix.lower() == ".gz" and cif_file.name.lower().endswith(".cif.gz"):
            with gzip.open(cif_file, "rt", encoding="utf-8", errors="ignore") as handle:
                structure = parser.get_structure(pdb_id, handle)
        else:
            structure = parser.get_structure(pdb_id, str(cif_file))
    except Exception as exc:
        return key, None, f"CIF parse error: {exc}"

    if new_chain != orig_chain:
        for model in structure:
            for ch in model:
                if ch.id == orig_chain:
                    ch.id = new_chain

    io_obj = io.StringIO()
    io_pdb = PDBIO()
    io_pdb.set_structure(structure)
    try:
        io_pdb.save(io_obj, ChainSelect(new_chain))
    except Exception as exc:
        return key, None, f"PDB save error: {exc}"

    return key, io_obj.getvalue(), None


def ensure_csv_with_headers(path: str, columns: list[str]) -> None:
    try:
        if not os.path.exists(path):
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return

        try:
            df = pd.read_csv(path)
        except Exception:
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return

        if df is None or df.columns is None:
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return

        lower_map = {c.lower(): c for c in df.columns}
        if "ligandx" in lower_map and "ligandX" not in df.columns:
            df = df.rename(columns={lower_map["ligandx"]: "ligandX"})

        if not all(c in df.columns for c in columns):
            pd.DataFrame(columns=columns).to_csv(path, index=False)
    except Exception:
        try:
            pd.DataFrame(columns=columns).to_csv(path, index=False)
        except Exception:
            pass


def write_headers_only(path: str, columns: list[str]) -> None:
    pd.DataFrame(columns=columns).to_csv(path, index=False)


def load_manifest_rows(job_root: Path) -> list[dict]:
    manifest_path = job_root / MANIFEST_FILE
    if not manifest_path.exists():
        return []
    try:
        return pd.read_csv(manifest_path, dtype=str).fillna("").to_dict("records")
    except Exception:
        return []


def load_cifdata_rows(job_root: Path) -> list[dict]:
    cifdata_path = job_root / "CIFdata.csv"
    if not cifdata_path.exists():
        return []
    try:
        return pd.read_csv(cifdata_path, dtype=str).fillna("").to_dict("records")
    except Exception:
        return []


def build_record_indexes(manifest_rows: list[dict], cifdata_rows: list[dict]) -> tuple[dict, dict]:
    manifest_index: dict[tuple[str, str], list[dict]] = {}
    cifdata_index: dict[tuple[str, str], list[dict]] = {}

    for row in manifest_rows:
        key = (str(row.get("target") or "").strip().lower(), normalize_pdb_id(row.get("pdb_id", "")))
        manifest_index.setdefault(key, []).append(row)

    for row in cifdata_rows:
        if "pdb_id" not in row:
            continue
        key = (str(row.get("protein") or "").strip().lower(), normalize_pdb_id(row.get("pdb_id", "")))
        cifdata_index.setdefault(key, []).append(row)

    return manifest_index, cifdata_index


def resolve_from_rows(
    rows: list[dict],
    job_root: Path,
    path_fields: list[str],
    preferred_mode: str,
    attempts: list[str],
) -> tuple[Path | None, str | None]:
    for row in rows:
        for field in path_fields:
            recorded = str(row.get(field) or "").strip()
            if not recorded:
                continue
            for candidate in recorded_candidates(recorded, job_root):
                attempts.append(f"{preferred_mode}:{field}:{candidate}")
                ok, _size, _err = verify_cif_file(candidate)
                if ok:
                    return candidate, preferred_mode
    return None, None


def resolve_legacy_path(
    job_root: Path,
    cif_base: str,
    protein: str,
    pdb_id: str,
    attempts: list[str],
) -> tuple[Path | None, str | None]:
    protein_dir = safe_resolve(job_root / cif_base / protein, job_root)
    if protein_dir is None:
        return None, None

    names = []
    for stem in (pdb_id, pdb_id.lower(), pdb_id.upper()):
        for suffix in (".cif", ".CIF", ".cif.gz", ".CIF.GZ"):
            name = f"{stem}{suffix}"
            if name not in names:
                names.append(name)

    for name in names:
        candidate = safe_resolve(protein_dir / name, job_root)
        if candidate is None:
            continue
        attempts.append(f"legacy:{candidate}")
        ok, _size, _err = verify_cif_file(candidate)
        if ok:
            return candidate, "legacy_reconstructed_path"

    return None, None


def resolve_fallback_search(
    job_root: Path,
    file_index: dict[str, list[Path]],
    pdb_id: str,
    attempts: list[str],
) -> tuple[Path | None, str | None]:
    for candidate in file_index.get(normalize_pdb_id(pdb_id), []):
        safe = safe_resolve(candidate, job_root)
        if safe is None:
            continue
        attempts.append(f"fallback_search:{safe}")
        ok, _size, _err = verify_cif_file(safe)
        if ok:
            return safe, "fallback_search"
    return None, None


def resolve_cif_path(
    job_root: Path,
    protein: str,
    pdb_id: str,
    cif_base: str,
    manifest_index: dict,
    cifdata_index: dict,
    file_index: dict[str, list[Path]],
) -> tuple[Path | None, str, list[str]]:
    attempts: list[str] = []
    key = (protein.strip().lower(), normalize_pdb_id(pdb_id))

    manifest_rows = manifest_index.get(key, [])
    path, mode = resolve_from_rows(
        manifest_rows,
        job_root,
        ["actual_path", "intended_path"],
        "manifest_path",
        attempts,
    )
    if path is not None:
        return path, mode or "manifest_path", attempts

    cifdata_rows = cifdata_index.get(key, [])
    path, mode = resolve_from_rows(
        cifdata_rows,
        job_root,
        ["actual_path", "cif_path", "intended_path", "protein_dir"],
        "cifdata_path",
        attempts,
    )
    if path is not None:
        return path, mode or "cifdata_path", attempts

    path, mode = resolve_legacy_path(job_root, cif_base, protein, normalize_pdb_id(pdb_id), attempts)
    if path is not None:
        return path, mode or "legacy_reconstructed_path", attempts

    path, mode = resolve_fallback_search(job_root, file_index, pdb_id, attempts)
    if path is not None:
        return path, mode or "fallback_search", attempts

    return None, "missing", attempts


def main():
    ensure_csv_with_headers(LIGMAP_FILE, LIGMAP_COLS)
    ensure_csv_with_headers(CHAINMAP_FILE, CHAINMAP_COLS)

    if not os.path.exists("filtered_data.csv") or not os.path.exists("chain_similarity.csv"):
        print("❌ Input CSVs missing. Did Step 2 finish?")
        return

    filtered_df = pd.read_csv("filtered_data.csv")
    sim_df = pd.read_csv("chain_similarity.csv")
    cifinfo = pd.read_csv("CIFdata.csv")
    query = pd.read_csv("queries.csv").iloc[0]

    target_min_id = float(query["MinID"])
    cif_base = str(cifinfo.iloc[0]["outdir"])
    output_root = f"{cif_base}_PDB"
    os.makedirs(output_root, exist_ok=True)

    job_root = Path.cwd().resolve()
    manifest_rows = load_manifest_rows(job_root)
    cifdata_rows = load_cifdata_rows(job_root)
    manifest_index, cifdata_index = build_record_indexes(manifest_rows, cifdata_rows)
    cif_root = safe_resolve(job_root / cif_base, job_root) or (job_root / cif_base)
    file_index = file_index_for(cif_root)
    verified_manifest_count = sum(1 for row in manifest_rows if parse_bool(row.get("exists")))
    print(f"📁 Step 3 output root: {Path(output_root).resolve()}")
    print(
        f"🧾 CIF sources: manifest_rows={len(manifest_rows)} "
        f"verified_manifest={verified_manifest_count} indexed_cifs={sum(len(v) for v in file_index.values())}"
    )

    merged = filtered_df.merge(sim_df, on=["protein", "pdb", "chain"], how="inner")

    if not merged.empty:
        max_sim = merged["Similarity"].max()
        print(f"📊 Similarity Stats: Max found = {max_sim:.2f}% (Threshold: {target_min_id}%)")
    else:
        print("📊 Merge result is empty.")

    final_set = merged[merged["Similarity"] >= target_min_id].drop_duplicates()

    if final_set.empty and not merged.empty:
        print(f"⚠️ Strict filter ({target_min_id}%) dropped everything.")
        print("🔄 Auto-lowering threshold to 30% to salvage data...")
        final_set = merged[merged["Similarity"] >= 30.0].drop_duplicates()

    if final_set.empty:
        print("❗ No entries found even after relaxing filter.")
        write_headers_only(LIGMAP_FILE, LIGMAP_COLS)
        write_headers_only(CHAINMAP_FILE, CHAINMAP_COLS)
        return

    all_ligands = final_set["ligand"].astype(str)
    has_five_letter = any(len(l.strip()) == 5 for l in all_ligands)

    if not has_five_letter:
        print("⚠️ No 5-letter ligands detected. Renaming map not required.")
        with open("Skip4.txt", "w") as handle:
            handle.write("No 5-letter ligands detected. Step 4 renaming not required.\n")
        print("🛑 Created Skip4.txt — Step 4 will be skipped.")
        write_headers_only(LIGMAP_FILE, LIGMAP_COLS)

    for protein in final_set["protein"].unique():
        os.makedirs(f"{output_root}/{protein}", exist_ok=True)

    ligmap_rows = []
    lig5_to_ligX = {}
    ligand_counter = 0

    if has_five_letter:
        try:
            ligmap_df = pd.read_csv(LIGMAP_FILE)
        except Exception:
            ligmap_df = pd.DataFrame(columns=LIGMAP_COLS)

        if not ligmap_df.empty:
            lower_map = {c.lower(): c for c in ligmap_df.columns}
            if "ligandx" in lower_map and "ligandX" not in ligmap_df.columns:
                ligmap_df = ligmap_df.rename(columns={lower_map["ligandx"]: "ligandX"})

        for col in LIGMAP_COLS:
            if col not in ligmap_df.columns:
                ligmap_df[col] = pd.Series(dtype="object")

        ligmap_rows = ligmap_df[LIGMAP_COLS].to_dict("records")
        for row in ligmap_rows:
            lig5 = str(row.get("ligand5", "")).strip()
            ligx = str(row.get("ligandX", "")).strip()
            if lig5 and ligx:
                lig5_to_ligX[lig5] = ligx
                try:
                    ligand_counter = max(ligand_counter, int(ligx[1:]) + 1)
                except Exception:
                    pass

        five_ligs = final_set[final_set["ligand"].astype(str).str.len() == 5]["ligand"].unique()
        for lig5 in five_ligs:
            lig5 = str(lig5).strip()
            if lig5 and lig5 not in lig5_to_ligX:
                lig5_to_ligX[lig5] = next_ligandX(ligand_counter)
                ligand_counter += 1

    try:
        chainmap_df = pd.read_csv(CHAINMAP_FILE)
    except Exception:
        chainmap_df = pd.DataFrame(columns=CHAINMAP_COLS)

    for col in CHAINMAP_COLS:
        if col not in chainmap_df.columns:
            chainmap_df[col] = pd.Series(dtype="object")

    chainmap_rows = chainmap_df[CHAINMAP_COLS].to_dict("records")
    chain_map = {
        (row["pdb"], row["orig_chain"]): row["new_chain"]
        for row in chainmap_rows
        if row.get("pdb") and row.get("orig_chain")
    }
    chain_counter = len(chain_map)

    long_chains = final_set[final_set["chain"].astype(str).str.len() > 1][["pdb", "chain"]].drop_duplicates()
    for _, row in long_chains.iterrows():
        key = (row["pdb"], row["chain"])
        if key not in chain_map:
            chain_map[key] = generate_chain_id(chain_counter)
            chainmap_rows.append({
                "pdb": row["pdb"],
                "orig_chain": row["chain"],
                "new_chain": chain_map[key],
            })
            chain_counter += 1

    jobs = []
    missing_cifs = []
    seen_ligmap = set()

    for job_idx, row in enumerate(final_set.itertuples(index=False)):
        protein = row.protein
        pdb_id = normalize_pdb_id(row.pdb)
        chain = row.chain
        ligand = row.ligand

        resolved_cif, lookup_mode, attempts = resolve_cif_path(
            job_root,
            protein,
            pdb_id,
            cif_base,
            manifest_index,
            cifdata_index,
            file_index,
        )

        new_chain = chain_map.get((pdb_id, chain), chain)
        ligand_str = str(ligand).strip()

        if has_five_letter and len(ligand_str) == 5:
            lig5 = ligand_str
            key = (protein, pdb_id, lig5)
            if key not in seen_ligmap:
                seen_ligmap.add(key)
                ligmap_rows.append({
                    "protein": protein,
                    "pdb": pdb_id,
                    "ligand5": lig5,
                    "ligand3": lig5[:3],
                    "ligandX": lig5_to_ligX.get(lig5, ""),
                })

        safe_ligand = ligand_str.replace("/", "-")
        out_rel = f"{protein}/{pdb_id}_{chain}_{safe_ligand}.pdb"
        out_path = os.path.join(output_root, out_rel)

        expected_paths = attempts[:20]
        if resolved_cif is None:
            missing_cifs.append({
                "job_index": job_idx,
                "protein": protein,
                "pdb": pdb_id,
                "chain": chain,
                "ligand": ligand,
                "lookup_mode": lookup_mode,
                "cif_path": "",
                "expected_paths": " | ".join(expected_paths),
                "error": f"CIF missing for pdb={pdb_id}",
            })
            continue

        jobs.append({
            "job_index": job_idx,
            "protein": protein,
            "pdb": pdb_id,
            "orig_chain": chain,
            "new_chain": new_chain,
            "ligand": ligand,
            "cif_path": str(resolved_cif),
            "cif_lookup_mode": lookup_mode,
            "expected_paths": " | ".join(expected_paths),
            "out_path": out_path,
        })

    print(f"\n🚀 BUILDING {len(jobs)} PDB CHAINS...\n")
    print(f"🧾 Step 3 selected build rows={len(final_set)} resolvable_cifs={len(jobs)} missing_cifs={len(missing_cifs)}")
    if jobs:
        sample_modes = [f"{job['pdb']}:{job['cif_lookup_mode']}:{path_for_log(Path(job['cif_path']), job_root)}" for job in jobs[:10]]
        print(f"🧾 Step 3 sample CIF resolutions: {sample_modes}")

    results = {}
    failures = list(missing_cifs)

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as exe:
        futmap = {exe.submit(build_single_pdb, job): job["job_index"] for job in jobs}
        for fut in tqdm(as_completed(futmap), total=len(jobs)):
            idx = futmap[fut]
            try:
                _key, pdbtext, err = fut.result()
                if pdbtext:
                    results[idx] = pdbtext
                else:
                    failures.append({
                        "job_index": idx,
                        "protein": jobs[idx]["protein"],
                        "pdb": jobs[idx]["pdb"],
                        "chain": jobs[idx]["orig_chain"],
                        "ligand": jobs[idx]["ligand"],
                        "lookup_mode": jobs[idx]["cif_lookup_mode"],
                        "cif_path": jobs[idx]["cif_path"],
                        "expected_paths": jobs[idx]["expected_paths"],
                        "error": err or "unknown worker failure",
                    })
            except Exception as exc:
                failures.append({
                    "job_index": idx,
                    "protein": jobs[idx]["protein"],
                    "pdb": jobs[idx]["pdb"],
                    "chain": jobs[idx]["orig_chain"],
                    "ligand": jobs[idx]["ligand"],
                    "lookup_mode": jobs[idx]["cif_lookup_mode"],
                    "cif_path": jobs[idx]["cif_path"],
                    "expected_paths": jobs[idx]["expected_paths"],
                    "error": f"executor exception: {exc}",
                })

    for idx, pdb_text in results.items():
        out_path = jobs[idx]["out_path"]
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as handle:
            handle.write(pdb_text)

    built_files = sorted(Path(output_root).rglob("*.pdb"))
    print(f"📁 Step 3 output root resolved: {Path(output_root).resolve()}")
    print(f"📊 Step 3 requested={len(final_set)} built={len(results)} on_disk={len(built_files)}")
    if built_files:
        print(f"🧾 Step 3 sample PDBs: {[str(p.relative_to(output_root)) for p in built_files[:10]]}")
    if failures:
        fail_df = pd.DataFrame(failures)
        fail_df.to_csv("PDB_BUILD_FAILURES.csv", index=False)
        print(f"⚠️ Step 3 build failures: {len(failures)} rows written to PDB_BUILD_FAILURES.csv")
        for row in failures[:10]:
            print(
                f"⚠️ PDB build miss: pdb={row['pdb']} chain={row['chain']} "
                f"ligand={row['ligand']} mode={row.get('lookup_mode', 'unknown')} "
                f"error={row['error']}"
            )
    if len(built_files) == 0:
        war_samples = []
        if cif_root.exists():
            war_samples = [path_for_log(path, job_root) for path in sorted(cif_root.rglob("*")) if path.is_file()][:20]
        sample_expected = [row.get("expected_paths", "") for row in failures[:10]]
        raise RuntimeError(
            "Step 3 produced zero PDB files. "
            f"manifest_rows={len(manifest_rows)} verified_cifs={verified_manifest_count} "
            f"selected_build_rows={len(final_set)} resolved_jobs={len(jobs)} "
            f"sample_expected_paths={sample_expected} sample_war_files={war_samples}"
        )

    if has_five_letter:
        pd.DataFrame(ligmap_rows, columns=LIGMAP_COLS).to_csv(LIGMAP_FILE, index=False)
    else:
        write_headers_only(LIGMAP_FILE, LIGMAP_COLS)

    pd.DataFrame(chainmap_rows, columns=CHAINMAP_COLS).drop_duplicates().to_csv(CHAINMAP_FILE, index=False)

    if len(built_files) == len(jobs) and not failures:
        print("\n✅ All PDBs built successfully.")
    else:
        print(f"\n⚠️ Step 3 completed with partial output: built {len(built_files)} of {len(final_set)} requested PDBs.")
    if has_five_letter:
        print(f"✅ Wrote {LIGMAP_FILE} with mapping rows (5-letter ligands detected).")
    else:
        print(f"✅ Wrote {LIGMAP_FILE} as headers-only (no 5-letter ligands detected).")


if __name__ == "__main__":
    main()
