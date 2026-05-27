#!/usr/bin/env python3
import ast
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOB_RUNNER = ROOT / "job_runner.py"


def import_requests_here() -> str:
    try:
        import requests  # noqa: F401
        return "OK"
    except Exception as exc:
        return f"FAIL ({type(exc).__name__}: {exc})"


def import_requests_with_python3() -> str:
    try:
        proc = subprocess.run(
            ["python3", "-c", "import requests; print('OK')"],
            text=True,
            capture_output=True,
            timeout=20,
        )
    except Exception as exc:
        return f"FAIL ({type(exc).__name__}: {exc})"

    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode == 0:
        return output or "OK"
    return f"FAIL (exit {proc.returncode}: {output})"


def job_runner_python_bin() -> str:
    source = JOB_RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PYTHON_BIN":
                    return ast.get_source_segment(source, node.value) or "<found>"
    return "NOT FOUND"


def active_subprocess_check() -> tuple[bool, list[str]]:
    source = JOB_RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    failures: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "cmd"):
                continue
            value = node.value
            first = None
            if isinstance(value, ast.List) and value.elts:
                first = value.elts[0]
            elif isinstance(value, ast.BinOp) and isinstance(value.left, ast.List) and value.left.elts:
                first = value.left.elts[0]
            if isinstance(first, ast.Constant) and first.value == "python3":
                failures.append(f"line {node.lineno}: hardcoded python3")

    return not failures, failures


def main() -> int:
    uses_python_bin, failures = active_subprocess_check()

    print(f"Current sys.executable: {sys.executable}")
    print(f"which python: {shutil.which('python') or 'NOT FOUND'}")
    print(f"which python3: {shutil.which('python3') or 'NOT FOUND'}")
    print(f"import requests in current Python: {import_requests_here()}")
    print(f"import requests with python3 -c: {import_requests_with_python3()}")
    print(f"job_runner.py PYTHON_BIN: {job_runner_python_bin()}")
    print(
        "job_runner.py active subprocess commands: "
        + ("OK (PYTHON_BIN/sys.executable)" if uses_python_bin else "FAIL")
    )
    for failure in failures:
        print(f"  {failure}")
    print("PASS" if uses_python_bin else "FAIL")
    return 0 if uses_python_bin else 1


if __name__ == "__main__":
    raise SystemExit(main())
