#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JOB_PATH_RE = re.compile(r'path="/api/job_log/([A-Za-z0-9_-]+)"')
MONITOR_PATH_RE = re.compile(r'path="/monitor/([A-Za-z0-9_-]+)"')
JOB_DIR_RE = re.compile(r"jobs/([A-Za-z0-9_-]+)")
MEM_RE = re.compile(
    r"\[mem\] (?P<phase>before|after|running|during)\s+"
    r"(?P<step>[A-Za-z0-9_./-]+)\s+"
    r"(?P<body>.*)$"
)
NUMBER_RE = re.compile(r"([a-z_]+)=([+-]?[0-9.]+)MB")
RUN_STEP_RE = re.compile(r"Running ([A-Za-z0-9_./-]+)")
R14_RE = re.compile(r"Error R1[45]")
BACKUP_RE = re.compile(r"RANDY backup")
FAIL_RE = re.compile(r"CRITICAL ERROR")
SUCCESS_RE = re.compile(r"PIPELINE FINISHED SUCCESSFULLY")
NO_OUTPUT_KILL_RE = re.compile(r"(?P<step>[A-Za-z0-9_./-]+)\s+produced no output for >(?P<seconds>\d+)s")
RESULTS_ROUTE_RE = re.compile(r"(GET|POST)\s+/results/([A-Za-z0-9_-]+)")
ROUTE_STATUS_RE = re.compile(r"\bstatus=(\d{3})\b")
SDF_404_RE = re.compile(r"/api/sdf/")
SVG_404_RE = re.compile(r"/api/svg(?:-plain)?/")
JOB_SUMMARY_404_RE = re.compile(r"/api/job_summary/")
RESULTS_424_RE = re.compile(r"/results/([A-Za-z0-9_-]+).*\b424\b")
SAFE_FAILURE_RE = re.compile(
    r"Job failed safely before dyno crash:\s+"
    r"(?P<step>[A-Za-z0-9_./-]+)\s+exceeded\s+(?P<metric>[a-z_]+)\s+memory guard during\s+(?P<phase>[a-z_]+)\.\s+"
    r"(?P<body>.*)$"
)


def _line_payload(line: str) -> str:
    try:
        return line.split("]: ", 1)[1].strip()
    except Exception:
        return line.strip()


@dataclass
class StepSample:
    job_id: str
    step: str
    before_parent_mb: float | None = None
    after_parent_mb: float | None = None
    parent_delta_mb: float | None = None
    child_peak_mb: float | None = None
    dyno_peak_mb: float | None = None
    tree_peak_mb: float | None = None
    combined_peak_mb: float | None = None
    r14_after: bool = False
    r15_after: bool = False
    guard_failure: str | None = None


@dataclass
class JobSummary:
    job_id: str
    steps: dict[str, StepSample] = field(default_factory=dict)
    step_order: list[str] = field(default_factory=list)
    poll_bytes: list[int] = field(default_factory=list)
    r14_lines: list[str] = field(default_factory=list)
    r15_lines: list[str] = field(default_factory=list)
    backup_seen: bool = False
    backup_before_failure: bool = False
    failed: bool = False
    succeeded: bool = False
    last_running_step: str = ""
    sdf_404s: list[str] = field(default_factory=list)
    svg_404s: list[str] = field(default_factory=list)
    job_summary_404s: list[str] = field(default_factory=list)
    results_424s: list[str] = field(default_factory=list)
    results_opened_before_finish: bool = False
    no_output_kills: list[str] = field(default_factory=list)

    def step(self, name: str) -> StepSample:
        if name not in self.steps:
            self.steps[name] = StepSample(job_id=self.job_id, step=name)
            self.step_order.append(name)
        return self.steps[name]


def parse_mb_fields(body: str) -> dict[str, float]:
    return {key: float(value) for key, value in NUMBER_RE.findall(body)}


def normalize_step_name(raw: str) -> str:
    return raw.rstrip(".").strip()


def parse_log(path: Path) -> tuple[dict[str, JobSummary], dict[str, Any]]:
    jobs: dict[str, JobSummary] = {}
    current_job: str | None = None
    current_step_by_job: dict[str, str] = {}

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        payload = _line_payload(raw)

        path_match = JOB_PATH_RE.search(raw)
        if path_match:
            job_id = path_match.group(1)
            current_job = job_id
            jobs.setdefault(job_id, JobSummary(job_id=job_id)).poll_bytes.append(
                int(re.search(r"\bbytes=(\d+)\b", raw).group(1)) if re.search(r"\bbytes=(\d+)\b", raw) else 0
            )

        monitor_match = MONITOR_PATH_RE.search(raw)
        if monitor_match:
            current_job = monitor_match.group(1)
            jobs.setdefault(current_job, JobSummary(job_id=current_job))

        dir_match = JOB_DIR_RE.search(payload)
        if dir_match:
            current_job = dir_match.group(1)
            jobs.setdefault(current_job, JobSummary(job_id=current_job))

        if "Job " in payload and " created for target:" in payload:
            pieces = payload.split()
            try:
                current_job = pieces[2]
                jobs.setdefault(current_job, JobSummary(job_id=current_job))
            except Exception:
                pass

        results_route = RESULTS_ROUTE_RE.search(raw)
        if results_route:
            routed_job = results_route.group(2)
            summary = jobs.setdefault(routed_job, JobSummary(job_id=routed_job))
            if not summary.succeeded and not summary.failed:
                summary.results_opened_before_finish = True

        status_match = ROUTE_STATUS_RE.search(raw)
        route_status = status_match.group(1) if status_match else ""
        if route_status == "404":
            if SDF_404_RE.search(raw):
                target_job = current_job or (results_route.group(2) if results_route else None)
                if target_job:
                    jobs.setdefault(target_job, JobSummary(job_id=target_job)).sdf_404s.append(raw)
            if SVG_404_RE.search(raw):
                target_job = current_job or (results_route.group(2) if results_route else None)
                if target_job:
                    jobs.setdefault(target_job, JobSummary(job_id=target_job)).svg_404s.append(raw)
            if JOB_SUMMARY_404_RE.search(raw):
                target_job = current_job or (results_route.group(2) if results_route else None)
                if target_job:
                    jobs.setdefault(target_job, JobSummary(job_id=target_job)).job_summary_404s.append(raw)
        if RESULTS_424_RE.search(raw):
            target_job = RESULTS_424_RE.search(raw).group(1)
            jobs.setdefault(target_job, JobSummary(job_id=target_job)).results_424s.append(raw)

        run_match = RUN_STEP_RE.search(payload)
        if run_match and current_job:
            step = normalize_step_name(run_match.group(1))
            summary = jobs.setdefault(current_job, JobSummary(job_id=current_job))
            summary.step(step)
            summary.last_running_step = step
            current_step_by_job[current_job] = step
            continue

        mem_match = MEM_RE.search(payload)
        if mem_match and current_job:
            phase = mem_match.group("phase")
            step = normalize_step_name(mem_match.group("step"))
            fields = parse_mb_fields(mem_match.group("body"))
            sample = jobs.setdefault(current_job, JobSummary(job_id=current_job)).step(step)
            if phase == "before":
                sample.before_parent_mb = fields.get("rss") or fields.get("parent")
            elif phase == "after":
                sample.after_parent_mb = fields.get("rss") or fields.get("parent")
                sample.parent_delta_mb = fields.get("delta")
                sample.child_peak_mb = fields.get("child_peak", sample.child_peak_mb)
                sample.dyno_peak_mb = fields.get("dyno_peak", sample.dyno_peak_mb)
                sample.tree_peak_mb = fields.get("tree_peak", sample.tree_peak_mb)
                sample.combined_peak_mb = fields.get("combined_peak", sample.combined_peak_mb)
            else:
                sample.child_peak_mb = max(sample.child_peak_mb or 0.0, fields.get("child_tree", 0.0)) or sample.child_peak_mb
                sample.tree_peak_mb = max(sample.tree_peak_mb or 0.0, fields.get("child_tree", 0.0)) or sample.tree_peak_mb
                sample.dyno_peak_mb = max(sample.dyno_peak_mb or 0.0, fields.get("dyno", 0.0)) or sample.dyno_peak_mb
                sample.combined_peak_mb = max(sample.combined_peak_mb or 0.0, fields.get("combined", 0.0)) or sample.combined_peak_mb
            continue

        if R14_RE.search(raw) and current_job:
            summary = jobs.setdefault(current_job, JobSummary(job_id=current_job))
            if "R15" in raw:
                summary.r15_lines.append(raw)
            else:
                summary.r14_lines.append(raw)
            step_name = current_step_by_job.get(current_job)
            if step_name:
                step = summary.step(step_name)
                if "R15" in raw:
                    step.r15_after = True
                else:
                    step.r14_after = True
            continue

        if BACKUP_RE.search(payload) and current_job:
            summary = jobs.setdefault(current_job, JobSummary(job_id=current_job))
            summary.backup_seen = True
            if not summary.failed:
                summary.backup_before_failure = True
            continue

        if FAIL_RE.search(payload) and current_job:
            summary = jobs.setdefault(current_job, JobSummary(job_id=current_job))
            summary.failed = True
            no_output_match = NO_OUTPUT_KILL_RE.search(payload)
            if no_output_match:
                summary.no_output_kills.append(payload)
            safe_failure = SAFE_FAILURE_RE.search(payload)
            if safe_failure:
                step_name = normalize_step_name(safe_failure.group("step"))
                fields = parse_mb_fields(safe_failure.group("body"))
                step = summary.step(step_name)
                step.guard_failure = payload
                step.child_peak_mb = max(step.child_peak_mb or 0.0, fields.get("child_tree", 0.0)) or step.child_peak_mb
                step.tree_peak_mb = max(step.tree_peak_mb or 0.0, fields.get("child_tree", 0.0)) or step.tree_peak_mb
                step.combined_peak_mb = max(step.combined_peak_mb or 0.0, fields.get("combined", 0.0)) or step.combined_peak_mb
                step.dyno_peak_mb = max(step.dyno_peak_mb or 0.0, fields.get("dyno", 0.0)) or step.dyno_peak_mb
                if fields.get("parent") is not None:
                    step.after_parent_mb = fields.get("parent")
            continue

        if SUCCESS_RE.search(payload) and current_job:
            jobs.setdefault(current_job, JobSummary(job_id=current_job)).succeeded = True

    aggregate = {
        "job_ids": list(jobs.keys()),
        "repeated_first_danger_steps": defaultdict(int),
    }
    for summary in jobs.values():
        ranked = []
        for step_name in summary.step_order:
            step = summary.steps[step_name]
            if (step.child_peak_mb or 0.0) >= 700.0 or step.r14_after or step.r15_after:
                ranked.append(step_name)
        if ranked:
            aggregate["repeated_first_danger_steps"][ranked[0]] += 1
    aggregate["repeated_first_danger_steps"] = dict(aggregate["repeated_first_danger_steps"])
    return jobs, aggregate


def fmt_mb(value: float | None) -> str:
    return "" if value is None else f"{value:.1f}"


def print_report(jobs: dict[str, JobSummary], aggregate: dict[str, Any]) -> None:
    print("job_id\tstep\tparent_before_mb\tparent_after_mb\tdelta_mb\tchild_peak_mb\ttree_peak_mb\tdyno_peak_mb\tcombined_peak_mb\theroku_r14_after\theroku_r15_after\tguard_failure")
    for job_id, summary in jobs.items():
        for step_name in summary.step_order:
            step = summary.steps[step_name]
            print(
                "\t".join(
                    [
                        job_id,
                        step_name,
                        fmt_mb(step.before_parent_mb),
                        fmt_mb(step.after_parent_mb),
                        fmt_mb(step.parent_delta_mb),
                        fmt_mb(step.child_peak_mb),
                        fmt_mb(step.tree_peak_mb),
                        fmt_mb(step.dyno_peak_mb),
                        fmt_mb(step.combined_peak_mb),
                        "yes" if step.r14_after else "no",
                        "yes" if step.r15_after else "no",
                        step.guard_failure or "",
                    ]
                )
            )

    print("\nSummary")
    print(json.dumps({
        "jobs": [
            {
                "job_id": job.job_id,
                "poll_bytes_min": min(job.poll_bytes) if job.poll_bytes else None,
                "poll_bytes_max": max(job.poll_bytes) if job.poll_bytes else None,
                "poll_count": len(job.poll_bytes),
                "r14_count": len(job.r14_lines),
                "r15_count": len(job.r15_lines),
                "backup_seen": job.backup_seen,
                "backup_before_failure": job.backup_before_failure,
                "failed": job.failed,
                "succeeded": job.succeeded,
                "incomplete": (not job.failed and not job.succeeded),
                "last_running_step": job.last_running_step or None,
                "sdf_404_count": len(job.sdf_404s),
                "svg_404_count": len(job.svg_404s),
                "job_summary_404_count": len(job.job_summary_404s),
                "results_424_count": len(job.results_424s),
                "results_opened_before_finish": job.results_opened_before_finish,
                "no_output_kill_count": len(job.no_output_kills),
                "classification": (
                    "no_output_watchdog_kill" if job.no_output_kills else
                    "missing_artifact" if (job.sdf_404s or job.svg_404s or job.job_summary_404s) else
                    "incomplete_pipeline" if (job.results_424s or ((not job.failed and not job.succeeded) and job.results_opened_before_finish)) else
                    "user_opened_results_early" if job.results_opened_before_finish else
                    "memory_guard_failure" if any(step.guard_failure for step in job.steps.values()) else
                    "heroku_dyno_crash" if (job.r14_lines or job.r15_lines) and not job.succeeded else
                    "completed" if job.succeeded else
                    "failed"
                ),
            }
            for job in jobs.values()
        ],
        "aggregate": aggregate,
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Heroku memory and polling patterns for Warhead Hunter logs.")
    parser.add_argument("log_path", type=Path)
    args = parser.parse_args()

    jobs, aggregate = parse_log(args.log_path)
    if not jobs:
        print("No job activity found.")
        return 1
    print_report(jobs, aggregate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
