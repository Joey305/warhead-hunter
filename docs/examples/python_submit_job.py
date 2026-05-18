#!/usr/bin/env python3

import sys
import time
from pathlib import Path

import requests

BASES = [
    "http://cartman.rove-vernier.ts.net",
    "https://warheadhunter.com",
]


def choose_base():
    for base in BASES:
        try:
            r = requests.get(f"{base}/api/health", timeout=5)
            if r.ok:
                return base
        except requests.RequestException:
            pass
    raise RuntimeError("No Warhead Hunter API base is reachable")


def submit_job(base):
    payload = {
        "pdb_id": "4EIY",
        "ligand": "ABC",
        "options": {
            "run_sasa": True,
            "generate_svg": True,
            "generate_viewer": True,
        },
    }
    r = requests.post(f"{base}/api/jobs", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def poll_job(base, job_id, interval=5, max_attempts=24):
    for _ in range(max_attempts):
        r = requests.get(f"{base}/api/jobs/{job_id}", timeout=30)
        r.raise_for_status()
        data = r.json()
        status = (data.get("job") or {}).get("status", "")
        print("status:", status)
        if status in {"completed", "failed"}:
            return data
        time.sleep(interval)
    raise RuntimeError("Timed out waiting for job completion")


def fetch_results(base, job_id):
    r = requests.get(f"{base}/api/jobs/{job_id}/results", timeout=30)
    if r.status_code not in {200, 202}:
        r.raise_for_status()
    return r.json()


def download_bundle(base, job_id, outdir="."):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / f"{job_id}_results.zip"
    with requests.get(f"{base}/api/jobs/{job_id}/bundle", timeout=120, stream=True) as r:
        r.raise_for_status()
        with open(outfile, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
    return outfile


def main():
    base = choose_base()
    print("Using base:", base)

    submission = submit_job(base)
    print("Submitted:", submission)

    job_id = submission["job_id"]
    final_status = poll_job(base, job_id)
    print("Final status:", final_status)

    results = fetch_results(base, job_id)
    print("Results manifest:", results)

    if (final_status.get("job") or {}).get("status") == "completed":
        bundle = download_bundle(base, job_id)
        print("Downloaded bundle:", bundle)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
