"""Host supervisor for disposable B5 load qualification; uses only stdlib.

The reporter container has no Docker socket. This supervisor measures the
whole Compose project and qualifies its report only with complete evidence.
It neither creates an environment file nor deletes containers or volumes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time


def docker_json(arguments: list[str]) -> dict:
    return json.loads(subprocess.check_output(["docker", *arguments], text=True, timeout=30))


def resource_checks(samples: list[dict], *, oom_events: int | None, machine: dict) -> bool:
    return (len(samples) >= 2 and oom_events == 0
            and machine.get("NCPU", 0) == 8 and 16_000_000_000 <= machine.get("MemTotal", 0) <= 17_179_869_184
            and all(sample["memory_bytes"] <= 14_000_000_000 and sample["unexpected_restarts"] == 0 for sample in samples))


def qualifies(report: dict, resource_ok: bool) -> bool:
    integrity = report.get("integrity", {})
    flink = report.get("health", {}).get("flink", {})
    return (report.get("mode") == "load" and report.get("status") == "passed" and resource_ok
            and flink.get("status") == "healthy" and flink.get("checkpoint_health", {}).get("status") == "healthy"
            and integrity.get("duplicate_semantic_relations") is False
            and integrity.get("sustained_backlog_growth") is False
            and integrity.get("broker_dlq_total") == 0
            and all(integrity.get(key) is True for key in (
                "no_lost_canonical_documents", "backlog_drained", "freshness_samples_complete",
                "raw_to_search_samples_complete", "latency_thresholds_met", "cache_metrics_complete", "targets_match_canonical"))
            and len(report.get("phase_windows", [])) == 2
            and [(window.get("count"), window.get("rate_per_minute")) for window in report["phase_windows"]] == [(3000, 100), (2500, 500)]
            and all(window.get("duration_seconds", 0) >= required for window, required in zip(report["phase_windows"], (1800, 300)))
            and report.get("runtime", {}).get("concurrent_query_clients") == 10
            and report.get("seed", {}).get("requested") == 10_000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", default="artifacts/b5-qualification.json")
    args = parser.parse_args()
    # Keep the explicit acceptance guard on the host as well as in the container.
    if "b5" not in args.project.lower() or "test" not in args.project.lower():
        parser.error("Use an isolated project name containing b5 and test")
    compose = ["docker", "compose", "--project-name", args.project, "--env-file", args.env_file,
               "-f", "compose.yml", "-f", "infra/b5/compose.b5.yml", "-f", "infra/b5/compose.acceptance.yml",
               "--profile", "b5", "--profile", "acceptance"]
    started = int(time.time())
    machine = docker_json(["info", "--format", "{{json .}}"])
    samples, failures, restart_baseline = [], [], {}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Logs remain in an artifact, rather than printing environment/configuration.
    with output.with_suffix(".log").open("w", encoding="utf-8") as log:
        job = subprocess.Popen([*compose, "run", "--rm", "b5-acceptance"], stdout=log, stderr=subprocess.STDOUT)
        while job.poll() is None:
            try:
                ids = subprocess.check_output(["docker", "ps", "--filter", f"label=com.docker.compose.project={args.project}", "-q"], text=True, timeout=15).split()
                if not ids:
                    raise RuntimeError("No project containers")
                total, restarts = 0, 0
                for identity in ids:
                    container = docker_json(["inspect", "--format", "{{json .}}", identity])
                    restart_baseline.setdefault(identity, container["RestartCount"])
                    restarts += container["RestartCount"] - restart_baseline[identity]
                    # Conservatively pad Docker's formatted working set by 1 MiB.
                    used = subprocess.check_output(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", identity], text=True, timeout=15).split("/")[0].strip()
                    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "kB": 1000, "MB": 1000**2, "GB": 1000**3}
                    unit = next(unit for unit in sorted(units, key=len, reverse=True) if used.endswith(unit))
                    total += int(float(used[:-len(unit)]) * units[unit]) + 1024**2
                samples.append({"elapsed_seconds": time.time() - started, "memory_bytes": total, "unexpected_restarts": restarts})
            except Exception as exc:
                failures.append(type(exc).__name__)
            time.sleep(2)
    try:
        report = json.loads(subprocess.check_output([*compose, "run", "--rm", "--no-deps", "--entrypoint", "cat", "b5-acceptance", "/reports/b5-acceptance.json"], text=True, timeout=60))
        events = subprocess.check_output(["docker", "events", "--since", str(started), "--until", str(int(time.time())),
                                         "--filter", f"label=com.docker.compose.project={args.project}", "--filter", "event=oom", "--format", "{{json .}}"], text=True, timeout=30)
        oom_events = len(events.splitlines())
    except Exception as exc:
        failures.append(type(exc).__name__)
        report, oom_events = {"status": "blocked", "mode": "load"}, None
    resources_ok = not failures and resource_checks(samples, oom_events=oom_events, machine=machine)
    report["resources"] = {"samples": samples, "oom_events": oom_events, "checks_passed": resources_ok,
                           "machine": {key: machine.get(key) for key in ("NCPU", "MemTotal", "ServerVersion")}, "failures": failures}
    report["qualified"] = qualifies(report, resources_ok)
    if oom_events is not None:
        report.setdefault("integrity", {})["oom_events"] = oom_events
        report["limitations"] = [item for item in report.get("limitations", []) if not item.startswith("container OOM probe unavailable")]
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "qualified": report["qualified"], "report": str(output)}))
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
