#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import job_state


def fetch_status(url: str) -> tuple[int | None, str]:
    req = Request(url, headers={"User-Agent": "warhead-hunter-job-fallback-debug/1.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(512).decode("utf-8", errors="ignore")
    except HTTPError as exc:
      try:
        body = exc.read(512).decode("utf-8", errors="ignore")
      except Exception:
        body = ""
      return exc.code, body
    except URLError as exc:
      return None, str(exc.reason)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check disk-backed job fallback and optional live endpoint behavior.")
    parser.add_argument("job_id", help="Job id to inspect")
    parser.add_argument("--base-url", default="", help="Optional live app base URL, for example http://localhost:5000")
    args = parser.parse_args()

    job_id = str(args.job_id).strip()
    meta = job_state.load_job_metadata(job_id)
    log_lines = job_state.load_job_log_lines(job_id)
    hydrated = job_state.hydrate_job_from_disk(job_id)

    print(f"job_id: {job_id}")
    print(f"jobs_root: {job_state.get_jobs_root()}")

    if hydrated is None:
        print("FAIL: hydrate_job_from_disk returned None")
        return 1

    print(f"PASS: hydrate_job_from_disk resolved local job state -> status={hydrated.get('status')} results_ready={hydrated.get('results_ready')}")
    print(f"PASS: metadata {'present' if meta else 'absent but non-fatal'}")
    print(f"PASS: log lines available -> {len(log_lines)}")
    print(f"PASS: results_ready_from_disk -> {job_state.results_ready_from_disk(job_id)}")

    if args.base_url:
        root = args.base_url.rstrip("/")
        for path in [
            f"/api/jobs/{job_id}",
            f"/api/job_log/{job_id}",
            f"/api/jobs/{job_id}/results",
            f"/results/{job_id}",
            f"/monitor/{job_id}",
        ]:
            status, body = fetch_status(f"{root}{path}")
            preview = body.replace("\n", " ")[:160]
            print(f"{path}: {'PASS' if status and status < 500 else 'FAIL'} status={status} preview={json.dumps(preview)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
