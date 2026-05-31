# Heroku Demo Storage

Warhead Hunter demo jobs are currently stored on the local filesystem under `jobs/<job_id>/`.

Within a running single-dyno demo, disk artifacts are the source of truth:

- `jobs/<job_id>/job_metadata.json`
- `jobs/<job_id>/job.log`
- `jobs/<job_id>/TARGET_RESULTS/`
- `jobs/<job_id>/WAR_PDB/`
- other per-job pipeline artifacts

`JOB_STORE` remains only a live in-memory cache for the active worker. Read routes should still work when that cache is empty as long as the job folder still exists on disk.

This is acceptable only for demo mode on one dyno. Heroku dyno storage is ephemeral:

- files are lost on dyno restart,
- files are lost on deploy or dyno replacement,
- files are not shared across multiple dynos,
- filesystem-backed jobs are not production durable.

Current guidance:

- Keep the single-worker Heroku demo filesystem-backed for short-lived inspection.
- Treat disk artifacts as the authoritative job contract inside that one running dyno.
- Do not assume jobs survive restarts or scale-out.
- Keep long-running pipeline work memory-guarded on the web dyno until a dedicated worker dyno exists.

Recommended demo config on a 512 MB Heroku web dyno:

- `WARHEAD_JOB_LOG_TAIL_LINES=800`
- `WARHEAD_JOB_LOG_API_TAIL=400`
- `WARHEAD_MEMORY_WARN_MB=360`
- `WARHEAD_MEMORY_GUARD_MB=430`
- `WARHEAD_RUN_CLEANUP_STEP=0`

Operational notes:

- The web dyno should serve HTTP and only tolerate short-lived, guarded background work.
- If a job crosses the memory guard, Warhead Hunter should fail that job cleanly and keep the app up.
- Cleanup packaging is best disabled on Heroku unless memory headroom is confirmed.
- The long-term production fix is still a separate worker dyno or queue-backed job runner.

Future production durability should move metadata and artifacts into durable storage appropriate for the deployment target, such as a database plus object storage, or another persistent volume strategy outside the Heroku demo filesystem.

## RANDY completion backup

Warhead Hunter now supports post-run backup of completed and failed jobs to RANDY so `/results/<job_id>` can survive Heroku restarts when local `jobs/<job_id>/` disappears.

Recommended config:

- `RANDY_BACKUP_BASE_URL=https://.../backup`
- `RANDY_BACKUP_TOKEN=<secret>`
- `WARHEAD_BACKUP_ON_COMPLETE=1`
- `WARHEAD_BACKUP_ON_FAILURE=1`
- `WARHEAD_BACKUP_REQUIRED=0`
- `WARHEAD_BACKUP_TIMEOUT_SECONDS=60`
- `WARHEAD_BACKUP_MAX_BYTES=300000000`

Compatibility fallbacks still work:

- `RANDY_ARCHIVE_BASE_URL`
- `RANDY_ARCHIVE_TOKEN`
- `WARHEAD_HANDOFF_STORAGE_URL`
- `WARHEAD_HANDOFF_TOKEN`
- `PROTAC_BACKUP_TOKEN`

Operational notes:

- RANDY backup runs after the main pipeline completes and writes backup state into `job_metadata.json`.
- Backup failures are non-fatal by default. Set `WARHEAD_BACKUP_REQUIRED=1` only when you want a failed archive upload to fail the job.
- The backup client creates a curated on-disk ZIP and uploads it to RANDY's `/backup/hunter-job-archive` endpoint.
- RANDY extracts that ZIP into `job_files/` and keeps the uploaded archive under `archives/`.
- Manual dry-run/upload/verify tooling is available through `python scripts/debug_randy_completion_backup_contract.py <job_id> --dry-run|--upload|--verify`.
