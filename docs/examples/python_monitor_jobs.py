#!/usr/bin/env python3

from __future__ import annotations

import sys

from _api_common import choose_base, get_json, print_json, require_job_ids


DEFAULT_JOB_IDS = ["2d9b72a8", "000e32cc", "e8acbf53", "a3de8e79", "c210f932"]


def main(argv: list[str]) -> None:
    base = choose_base()
    job_ids = require_job_ids(argv[1:], DEFAULT_JOB_IDS)
    print(f"Using base: {base}")

    for job_id in job_ids:
        print(f"\n=== {job_id} ===")
        payload = get_json(f"{base}/api/jobs/{job_id}")
        print_json(payload)


if __name__ == "__main__":
    main(sys.argv)
