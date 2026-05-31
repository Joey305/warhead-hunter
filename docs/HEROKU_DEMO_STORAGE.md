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
