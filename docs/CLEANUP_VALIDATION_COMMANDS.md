# Cleanup Validation Commands

These commands exercise the downstream-aware cleanup/packaging utility conservatively.

## Compile validation

```bash
python -m py_compile job_runner.py app.py routes.py api/*.py pipeline_assets/*.py
```

## Real-job downstream dry-run audit

```bash
python pipeline_assets/18_CleanJobDirNzip.py --job-id 3b731e96 --jobs-root jobs --dry-run --trace-downstream --show-gallery-required --show-unused --show-cif --verbose
```

## Real-job safe package

```bash
python pipeline_assets/18_CleanJobDirNzip.py --job-id 3b731e96 --jobs-root jobs --safe-package --trace-downstream --verbose
```

## Destructive cleanup only after review

```bash
python pipeline_assets/18_CleanJobDirNzip.py --job-id 3b731e96 --jobs-root jobs --apply --delete-rebuildable --allow-delete-cif --trace-downstream --verbose
```

## Optional multi-job safe packaging

```bash
python pipeline_assets/18_CleanJobDirNzip.py --jobs-root jobs --all-completed --safe-package --trace-downstream --verbose
```

## Notes

- `--trace-downstream` is the intended default mode and should be kept on for audits.
- `--safe-package` is the recommended automated mode.
- No deletion occurs unless `--apply --delete-rebuildable` is used.
- `.cif` deletion requires the additional `--allow-delete-cif` flag.
