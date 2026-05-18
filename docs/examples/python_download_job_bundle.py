#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

from _api_common import choose_base, download_file


def main(argv: list[str]) -> None:
    base = choose_base()
    job_id = argv[1] if len(argv) > 1 else "b281996d"
    outdir = Path(argv[2]) if len(argv) > 2 else Path("downloads")
    output = outdir / f"{job_id}_warhead_hunter_results.zip"

    print(f"Using base: {base}")
    result = download_file(f"{base}/api/jobs/{job_id}/bundle", output)
    print(f"Downloaded bundle: {result}")


if __name__ == "__main__":
    main(sys.argv)
