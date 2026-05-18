#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

from _api_common import choose_base, download_file, get_json


def main(argv: list[str]) -> None:
    base = choose_base()
    job_id = argv[1] if len(argv) > 1 else "b281996d"
    outdir = Path(argv[2]) if len(argv) > 2 else Path("prosettac_stage") / job_id
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Using base: {base}")

    war_zip = download_file(f"{base}/api/jobs/{job_id}/war-pdbs.zip", outdir / f"{job_id}_WAR_PDB.zip")
    print(f"Downloaded WAR_PDB archive: {war_zip}")

    sdf_listing = get_json(f"{base}/api/jobs/{job_id}/artifacts?kind=sdf&limit=5")
    files = sdf_listing.get("files", [])
    if files:
        first = files[0]
        sdf_path = download_file(f"{base}{first['download_url']}", outdir / first["filename"])
        print(f"Downloaded example ligand SDF: {sdf_path}")
    else:
        print("No SDF files found for this job.")


if __name__ == "__main__":
    main(sys.argv)
