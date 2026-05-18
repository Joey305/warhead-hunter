#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

from _api_common import choose_base, download_file, get_json


def main() -> None:
    base = choose_base()
    outdir = Path("structure_library")
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Using base: {base}")
    indexed = get_json(f"{base}/api/indexed-jobs?available=true&limit=25")

    for job in indexed.get("jobs", []):
        job_id = job["job_id"]
        target = job.get("target_name") or job.get("protein") or job_id
        payload = get_json(f"{base}/api/jobs/{job_id}/war-pdbs")
        if not payload.get("count"):
            continue
        outfile = outdir / f"{job_id}_{target}_WAR_PDB.zip"
        download_file(f"{base}/api/jobs/{job_id}/war-pdbs.zip", outfile)
        print(f"Saved {outfile}")


if __name__ == "__main__":
    main()
