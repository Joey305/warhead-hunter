#!/usr/bin/env python3

from __future__ import annotations

import time

from _api_common import choose_base, get_json, post_json, print_json


def main() -> None:
    base = choose_base()
    payload = {
        "target_name": "OGA",
        "search_query": "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",
        "fasta_seq": ">EXAMPLE_FASTA\nMKT...",
    }

    print(f"Using base: {base}")
    submission = post_json(f"{base}/api/jobs", payload)
    print("Submitted job:")
    print_json(submission)

    job_id = submission["job_id"]
    for attempt in range(1, 11):
        status = get_json(f"{base}/api/jobs/{job_id}")
        print(f"[poll {attempt}] status={status.get('status')}")
        if status.get("status") in {"completed", "failed"}:
            break
        time.sleep(5)

    results = get_json(f"{base}/api/jobs/{job_id}/results")
    print("Results manifest:")
    print_json(results)


if __name__ == "__main__":
    main()
