#!/usr/bin/env python3
"""Run the minimal network-level TCP deployment matrix.

This runner reuses the distributed counted-TCP preflight and records the
orchestrator barrier interval as the steady-state secure-aggregation round.
It can optionally configure Linux tc/netem on one interface before each run.
Run it from the WSL environment that contains the OpenFHE binary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "experiments" / "correctness" / "run_v8_distributed_tcp_preflight.py"
DEFAULT_BINARY = Path("/home/liguiping/openfhe_splitpath_build/openfhe_dcrtpoly_wire_integration")


@dataclass(frozen=True)
class NetworkConfig:
    clients: int
    rtt_ms: int
    bandwidth_mbps: int
    group: str


SCHEMES = {
    "aggregate_only": "four_path_sum_only",
    "full": "full_protocol",
}


def default_matrix() -> list[NetworkConfig]:
    configs = [
        NetworkConfig(30, 1, 100, "rtt_sensitivity"),
        NetworkConfig(30, 20, 100, "rtt_sensitivity"),
        NetworkConfig(30, 50, 100, "center"),
        NetworkConfig(30, 100, 100, "rtt_sensitivity"),
        NetworkConfig(30, 50, 20, "bandwidth_sensitivity"),
        NetworkConfig(30, 50, 1000, "bandwidth_sensitivity"),
        NetworkConfig(10, 50, 100, "client_scalability"),
        NetworkConfig(50, 50, 100, "client_scalability"),
    ]
    return configs


def run_command(
    command: list[str],
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def tc_prefix(use_sudo: bool) -> list[str]:
    return ["sudo", "-n"] if use_sudo else []


def clear_netem(device: str, use_sudo: bool) -> None:
    run_command([*tc_prefix(use_sudo), "tc", "qdisc", "del", "dev", device, "root"])


def configure_netem(device: str, rtt_ms: int, bandwidth_mbps: int, use_sudo: bool) -> dict[str, Any]:
    if shutil.which("tc") is None and not use_sudo:
        raise RuntimeError("tc not found; install iproute2 or use a WSL/Linux environment with tc")
    one_way_delay = max(1.0, rtt_ms / 2.0)
    clear_netem(device, use_sudo)
    command = [
        *tc_prefix(use_sudo),
        "tc",
        "qdisc",
        "replace",
        "dev",
        device,
        "root",
        "netem",
        "delay",
        f"{one_way_delay:g}ms",
        "rate",
        f"{bandwidth_mbps}mbit",
    ]
    proc = run_command(command)
    if proc.returncode != 0:
        raise RuntimeError(f"tc netem configuration failed: {proc.stderr.strip()}")
    show = run_command([*tc_prefix(use_sudo), "tc", "qdisc", "show", "dev", device])
    ping = run_command(["ping", "-c", "3", "-w", "5", "127.0.0.1"])
    return {
        "device": device,
        "requested_rtt_ms": rtt_ms,
        "configured_one_way_delay_ms": one_way_delay,
        "requested_bandwidth_mbps": bandwidth_mbps,
        "tc_qdisc_show": show.stdout.strip(),
        "ping_returncode": ping.returncode,
        "ping_stdout": ping.stdout.strip(),
        "ping_stderr": ping.stderr.strip(),
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_summary(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["scheme"],
            row["clients"],
            row["dimension"],
            row["rtt_ms"],
            row["bandwidth_mbps"],
        )
        groups.setdefault(key, []).append(row)

    summary_rows = []
    for key, samples in sorted(groups.items()):
        latencies = [float(row["end_to_end_ms"]) for row in samples if row["status"] == "PASS"]
        bytes_values = [int(row["total_application_protocol_bytes"]) for row in samples if row["status"] == "PASS"]
        mean_ms = statistics.mean(latencies) if latencies else math.nan
        std_ms = statistics.stdev(latencies) if len(latencies) > 1 else 0.0
        mean_bytes = statistics.mean(bytes_values) if bytes_values else math.nan
        summary_rows.append(
            {
                "scheme": key[0],
                "clients": key[1],
                "dimension": key[2],
                "rtt_ms": key[3],
                "bandwidth_mbps": key[4],
                "runs": len(samples),
                "passed": len(latencies),
                "end_to_end_ms_mean": round(mean_ms, 6),
                "end_to_end_ms_std": round(std_ms, 6),
                "end_to_end_s_mean": round(mean_ms / 1000.0, 6),
                "total_application_protocol_bytes_mean": round(mean_bytes, 3),
            }
        )
    return summary_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def plot_summary(summary_rows: list[dict[str, Any]], out_dir: Path) -> list[str]:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    figure_dir = out_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    def draw(name: str, rows: list[dict[str, Any]], x_key: str, xlabel: str) -> None:
        if not rows:
            return
        plt.figure(figsize=(5.2, 3.4))
        for scheme, label in (("aggregate_only", "Aggregate-only"), ("full", "APBR-SplitMix")):
            scheme_rows = sorted(
                [row for row in rows if row["scheme"] == scheme],
                key=lambda row: int(row[x_key]),
            )
            if not scheme_rows:
                continue
            xs = [int(row[x_key]) for row in scheme_rows]
            ys = [float(row["end_to_end_s_mean"]) for row in scheme_rows]
            yerr = [float(row["end_to_end_ms_std"]) / 1000.0 for row in scheme_rows]
            plt.errorbar(xs, ys, yerr=yerr, marker="o", linewidth=1.8, capsize=3, label=label)
        plt.xlabel(xlabel)
        plt.ylabel("End-to-end aggregation latency (s)")
        plt.grid(True, axis="y", alpha=0.28)
        plt.legend(frameon=False)
        plt.tight_layout()
        path = figure_dir / f"{name}.png"
        plt.savefig(path, dpi=300)
        plt.close()
        generated.append(path.relative_to(ROOT).as_posix())

    draw(
        "network_rtt_sensitivity",
        [
            row
            for row in summary_rows
            if int(row["clients"]) == 30 and int(row["bandwidth_mbps"]) == 100
        ],
        "rtt_ms",
        "RTT (ms)",
    )
    draw(
        "network_bandwidth_sensitivity",
        [
            row
            for row in summary_rows
            if int(row["clients"]) == 30 and int(row["rtt_ms"]) == 50
        ],
        "bandwidth_mbps",
        "Bandwidth (Mbps)",
    )
    draw(
        "network_client_scalability",
        [
            row
            for row in summary_rows
            if int(row["rtt_ms"]) == 50 and int(row["bandwidth_mbps"]) == 100
        ],
        "clients",
        "Logical clients",
    )
    return generated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "formal" / "network_deployment")
    parser.add_argument("--dimension", type=int, default=784)
    parser.add_argument("--noise", choices=("zero", "small", "dgg32"), default="dgg32")
    parser.add_argument("--seed", type=int, default=2024)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--base-port", type=int, default=25100)
    parser.add_argument("--role-timeout", type=int, default=900)
    parser.add_argument("--openfhe-lib-dir", default="/home/liguiping/openfhe-install/lib")
    parser.add_argument("--apply-netem", action="store_true")
    parser.add_argument("--netem-device", default="lo")
    parser.add_argument("--sudo", action="store_true")
    parser.add_argument("--scheme", choices=tuple(SCHEMES), action="append")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plot-only", action="store_true")
    parser.add_argument(
        "--pilot-center",
        action="store_true",
        help="Run only c=30, d=784, RTT=50 ms, bandwidth=100 Mbps.",
    )
    args = parser.parse_args()

    if args.repetitions < 1:
        raise ValueError("repetitions must be positive")
    if args.warmups < 0:
        raise ValueError("warmups cannot be negative")

    out_dir = args.out_dir.resolve()
    raw_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.plot_only:
        summary_rows = read_csv_rows(out_dir / "network_deployment_summary.csv")
        if not summary_rows:
            run_rows = read_csv_rows(out_dir / "network_deployment_runs.csv")
            summary_rows = summarize(run_rows)
            write_csv(out_dir / "network_deployment_summary.csv", summary_rows)
        generated_figures = plot_summary(summary_rows, out_dir)
        summary_path = out_dir / "summary.json"
        if summary_path.is_file():
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_payload["figures"] = generated_figures
            write_json(summary_path, summary_payload)
        write_json(
            out_dir / "plot_summary.json",
            {
                "schema": "network_deployment_plot_summary_v1",
                "status": "PASS",
                "figures": generated_figures,
            },
        )
        print(json.dumps({"status": "PASS", "figures": generated_figures}, indent=2))
        return 0

    schemes = args.scheme or ["aggregate_only", "full"]
    configs = [NetworkConfig(30, 50, 100, "center")] if args.pilot_center else default_matrix()
    env = None
    if args.openfhe_lib_dir:
        import os

        env = os.environ.copy()
        old_path = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = (
            args.openfhe_lib_dir if not old_path else f"{args.openfhe_lib_dir}:{old_path}"
        )
    measured_rows: list[dict[str, Any]] = read_csv_rows(out_dir / "network_deployment_runs.csv") if args.resume else []
    seen_cases = {row["case_id"] for row in measured_rows}
    started = time.time()
    total_measured = len(configs) * len(schemes) * args.repetitions
    finished_measured = 0
    netem_log_path = out_dir / "network_conditions.jsonl"

    try:
        for config_index, config in enumerate(configs, start=1):
            netem_state = None
            if args.apply_netem:
                netem_state = configure_netem(
                    args.netem_device, config.rtt_ms, config.bandwidth_mbps, args.sudo
                )
                with netem_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "clients": config.clients,
                        "dimension": args.dimension,
                        "rtt_ms": config.rtt_ms,
                        "bandwidth_mbps": config.bandwidth_mbps,
                        "matrix_group": config.group,
                        "netem": netem_state,
                    }, sort_keys=True) + "\n")
            for scheme in schemes:
                variant = SCHEMES[scheme]
                total_attempts = args.warmups + args.repetitions
                for attempt in range(1, total_attempts + 1):
                    measured = attempt > args.warmups
                    rep = attempt - args.warmups if measured else 0
                    run_id = (
                        f"net_{scheme}_c{config.clients}_d{args.dimension}_"
                        f"rtt{config.rtt_ms}_bw{config.bandwidth_mbps}_"
                        f"{'r' + str(rep).zfill(3) if measured else 'warmup' + str(attempt).zfill(3)}"
                    )
                    run_dir = raw_dir / run_id
                    port = args.base_port + config_index * 100 + (0 if scheme == "aggregate_only" else 20)
                    if run_id in seen_cases and args.resume:
                        print(f"{run_id} SKIP existing measured row", flush=True)
                        continue
                    if run_dir.exists() and not args.resume:
                        raise FileExistsError(f"run output already exists: {run_dir}")
                    if run_dir.exists() and args.resume and (run_dir / "run_summary.json").is_file():
                        summary = read_summary(run_dir)
                        if summary.get("status") == "PASS":
                            if not measured:
                                print(f"{run_id} SKIP existing warmup", flush=True)
                                continue
                        else:
                            raise RuntimeError(f"existing run did not pass: {run_id}")
                    else:
                        command = [
                            sys.executable,
                            str(PREFLIGHT),
                            "--binary",
                            str(args.binary),
                            "--clients",
                            str(config.clients),
                            "--dimension",
                            str(args.dimension),
                            "--noise",
                            args.noise,
                            "--variant",
                            variant,
                            "--seed",
                            str(args.seed),
                            "--run-id",
                            run_id,
                            "--base-port",
                            str(port),
                            "--out-dir",
                            str(run_dir),
                            "--control-barrier",
                            "--role-timeout",
                            str(args.role_timeout),
                        ]
                        proc = run_command(command, timeout=args.role_timeout + 120, env=env)
                        (raw_dir / f"{run_id}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
                        (raw_dir / f"{run_id}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
                        if proc.returncode != 0:
                            raise RuntimeError(f"{run_id} failed: {proc.stderr[-2000:]}")
                        summary = read_summary(run_dir)
                        if summary.get("status") != "PASS":
                            raise RuntimeError(f"{run_id} did not pass")
                    if not measured:
                        print(f"{run_id} WARMUP PASS", flush=True)
                        continue

                    communication = summary["communication_bytes"]
                    row = {
                        "case_id": run_id,
                        "scheme": scheme,
                        "variant": variant,
                        "clients": config.clients,
                        "dimension": args.dimension,
                        "noise": args.noise,
                        "seed": args.seed,
                        "repetition": rep,
                        "rtt_ms": config.rtt_ms,
                        "bandwidth_mbps": config.bandwidth_mbps,
                        "matrix_group": config.group,
                        "status": summary["status"],
                        "end_to_end_ms": summary["orchestrator"]["steady_state_protocol_round_ms"],
                        "client_to_paths_bytes": communication["client_to_paths_sender"],
                        "paths_to_cs_bytes": communication["paths_to_cs_sender"],
                        "total_application_protocol_bytes": communication["total_application_protocol_bytes"],
                        "encoded_plaintext_mismatch_count": summary["correctness"]["encoded_plaintext_mismatch_count"],
                        "result_dir": run_dir.relative_to(ROOT).as_posix(),
                    }
                    measured_rows.append(row)
                    seen_cases.add(run_id)
                    finished_measured += 1
                    write_csv(out_dir / "network_deployment_runs.csv", measured_rows)
                    write_csv(out_dir / "network_deployment_summary.csv", summarize(measured_rows))
                    write_json(
                        out_dir / "progress.json",
                        {
                            "schema": "network_deployment_progress_v1",
                            "finished_measured_runs": finished_measured,
                            "total_measured_runs": total_measured,
                            "current": run_id,
                            "elapsed_hours": round((time.time() - started) / 3600.0, 6),
                            "netem": netem_state,
                        },
                    )
                    print(
                        f"{run_id} PASS e2e_ms={row['end_to_end_ms']} "
                        f"bytes={row['total_application_protocol_bytes']}",
                        flush=True,
                    )
    finally:
        if args.apply_netem:
            clear_netem(args.netem_device, args.sudo)

    summary_rows = summarize(measured_rows)
    write_csv(out_dir / "network_deployment_runs.csv", measured_rows)
    write_csv(out_dir / "network_deployment_summary.csv", summary_rows)
    generated_figures = plot_summary(summary_rows, out_dir)
    write_json(
        out_dir / "summary.json",
        {
            "schema": "network_deployment_matrix_summary_v1",
            "status": "PASS",
            "scope": "distributed counted-TCP secure-aggregation roles with optional Linux tc/netem emulation",
            "dimension": args.dimension,
            "seed": args.seed,
            "repetitions": args.repetitions,
            "warmups": args.warmups,
            "measured_runs": len(measured_rows),
            "csv": {
                "runs": (out_dir / "network_deployment_runs.csv").relative_to(ROOT).as_posix(),
                "summary": (out_dir / "network_deployment_summary.csv").relative_to(ROOT).as_posix(),
            },
            "figures": generated_figures,
            "limitations": [
                "The default runner uses local counted TCP roles; use --apply-netem in WSL/Linux to enforce tc/netem conditions.",
                "The client count denotes logical clients unless each client role is externally mapped to a separate host/container.",
                "Local FL training is excluded; the reported interval is the control-barrier protocol round.",
            ],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
