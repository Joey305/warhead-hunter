#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

from _api_common import choose_base, download_file, get_json


def main(argv: list[str]) -> None:
    base = choose_base()
    job_id = argv[1] if len(argv) > 1 else "b281996d"
    outdir = Path(argv[2]) if len(argv) > 2 else Path("downloads") / job_id

    print(f"Using base: {base}")
    listing = get_json(f"{base}/api/jobs/{job_id}/war-pdbs")
    print(f"WAR_PDB count for {job_id}: {listing.get('count', 0)}")

    zip_path = download_file(
        f"{base}/api/jobs/{job_id}/war-pdbs.zip",
        outdir / f"{job_id}_WAR_PDB.zip",
    )
    print(f"Downloaded ZIP: {zip_path}")


if __name__ == "__main__":
    main(sys.argv)
