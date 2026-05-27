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

Future production durability should move metadata and artifacts into durable storage appropriate for the deployment target, such as a database plus object storage, or another persistent volume strategy outside the Heroku demo filesystem.
