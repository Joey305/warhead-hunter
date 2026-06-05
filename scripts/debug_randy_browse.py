#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import browse_source_mode, load_browse_jobs
from api.randy_archive_client import archive_enabled, list_archived_jobs


def _masked_base_url() -> str:
    raw = (
        os.environ.get("RANDY_ARCHIVE_BASE_URL", "").strip()
        or os.environ.get("RANDY_BACKUP_BASE_URL", "").strip()
        or os.environ.get("WARHEAD_HANDOFF_STORAGE_URL", "").strip()
    )
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path
    path = parsed.path if parsed.netloc else ""
    return f"{parsed.scheme}://{host}{path}" if parsed.scheme else raw


def main() -> None:
    jobs, meta = load_browse_jobs(refresh=True)
    print("archive_enabled:", archive_enabled())
    print("browse_source_mode:", browse_source_mode())
    print("randy_base_url:", _masked_base_url() or "(not configured)")
    print("job_count:", len(jobs))
    print("available_results:", meta.get("available_count", 0))
    print("warning:", meta.get("warning", ""))

    if archive_enabled():
        archived = list_archived_jobs(limit=10, refresh=True)
        print("randy_list_count:", len(archived))
        print("first_10_jobs:")
        for item in archived[:10]:
            print(
                "-",
                json.dumps(
                    {
                        "job_id": item.get("job_id"),
                        "protein": item.get("protein"),
                        "has_results": item.get("has_results"),
                        "status": item.get("status"),
                        "source": item.get("source"),
                    },
                    sort_keys=True,
                ),
            )
    else:
        print("first_10_jobs:")
        for item in jobs[:10]:
            print(
                "-",
                json.dumps(
                    {
                        "job_id": item.get("job_id"),
                        "protein": item.get("protein"),
                        "has_results": item.get("has_results"),
                        "status": item.get("status"),
                        "source": item.get("source"),
                    },
                    sort_keys=True,
                ),
            )


if __name__ == "__main__":
    main()
