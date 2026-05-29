#!/usr/bin/env bash
set -u

JOBS_ROOT="${1:-jobs}"
RANDY_URL="${RANDY_URL:-https://randy.rove-vernier.ts.net/backup/hunter-job-archive}"
TOKEN="${TOKEN:-}"

if [ -z "$TOKEN" ]; then
  echo "ERROR: TOKEN is not set."
  echo "Run: export TOKEN='YOUR_RANDY_TOKEN'"
  exit 1
fi

if [ ! -d "$JOBS_ROOT" ]; then
  echo "ERROR: jobs root not found: $JOBS_ROOT"
  exit 1
fi

BACKUP_TMP="/tmp/warhead_hunter_randy_backups"
mkdir -p "$BACKUP_TMP"

echo "Backing up jobs from: $JOBS_ROOT"
echo "RANDY endpoint: $RANDY_URL"
echo

uploaded=0
skipped=0
failed=0

for job_dir in "$JOBS_ROOT"/*; do
  [ -d "$job_dir" ] || continue

  job_id="$(basename "$job_dir")"
  [ "$job_id" = "_batches" ] && continue

  # Only upload real job folders that have useful job metadata or result artifacts.
  if [ ! -f "$job_dir/job_metadata.json" ] && \
     [ ! -f "$job_dir/Results_Display.csv" ] && \
     [ ! -f "$job_dir/TARGET_RESULTS/Results_Display.csv" ]; then
    echo "SKIP $job_id: no job_metadata.json or Results_Display.csv"
    skipped=$((skipped + 1))
    continue
  fi

  cifs="$(find "$job_dir" -iname "*.cif" -type f | wc -l | tr -d ' ')"
  if [ "$cifs" != "0" ]; then
    echo "SKIP $job_id: still has $cifs CIF files. Clean first."
    skipped=$((skipped + 1))
    continue
  fi

  zip_path="$BACKUP_TMP/${job_id}_full_job_no_cif.zip"
  rm -f "$zip_path"

  size_before="$(du -sh "$job_dir" | awk '{print $1}')"

  echo "============================================================"
  echo "Zipping $job_id  size=$size_before"

  (
    cd "$job_dir" || exit 1
    zip -qr "$zip_path" . \
      -x "*/__pycache__/*" \
      -x "*.pyc" \
      -x "*.pyo" \
      -x "*.tmp" \
      -x ".DS_Store" \
      -x "_randy_backup/*"
  )

  if [ ! -f "$zip_path" ]; then
    echo "FAILED $job_id: zip was not created"
    failed=$((failed + 1))
    continue
  fi

  zip_size="$(ls -lh "$zip_path" | awk '{print $5}')"
  echo "Uploading $job_id  zip=$zip_size"

  response_file="$BACKUP_TMP/${job_id}_response.json"
  http_code="$(
    curl -sS --connect-timeout 30 --max-time 1800 \
      -o "$response_file" \
      -w "%{http_code}" \
      -X POST "$RANDY_URL" \
      -H "Authorization: Bearer $TOKEN" \
      -F "job_id=$job_id" \
      -F "source=manual-local-batch-backfill-no-cif" \
      -F "status=completed" \
      -F "archive=@$zip_path"
  )"

  echo "HTTP $http_code"
  cat "$response_file"
  echo

  if [ "$http_code" = "200" ] && grep -q '"ok":true' "$response_file"; then
    echo "OK $job_id"
    uploaded=$((uploaded + 1))
  else
    echo "FAILED $job_id"
    failed=$((failed + 1))
  fi
done

echo
echo "============================================================"
echo "DONE"
echo "Uploaded: $uploaded"
echo "Skipped:  $skipped"
echo "Failed:   $failed"

if [ "$failed" -ne 0 ]; then
  exit 2
fi
