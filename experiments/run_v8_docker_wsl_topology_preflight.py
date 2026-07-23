#!/usr/bin/env python3
"""Run Docker clients against WSL-hosted Route A path, CS, and control roles."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RunningRole:
    role_id: str
    process: subprocess.Popen[str]
    metrics_path: Path
    runtime: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wsl_path(path: Path, distro: str) -> str:
    windows_path = str(path.absolute()).replace("\\", "/")
    return subprocess.check_output(
        ["wsl", "-d", distro, "--", "wslpath", "-a", windows_path],
        text=True,
    ).strip()


def start_process(role_id: str, command: list[str], metrics_dir: Path, runtime: str) -> RunningRole:
    process = subprocess.Popen(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return RunningRole(role_id, process, metrics_dir / f"{role_id}.json", runtime)


def collect_role(role: RunningRole, timeout: int) -> dict[str, Any]:
    try:
        stdout, stderr = role.process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        role.process.kill()
        stdout, stderr = role.process.communicate()
        role.metrics_path.with_suffix(".stdout.txt").write_text(stdout, encoding="utf-8")
        role.metrics_path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")
        raise RuntimeError(f"role timeout: {role.role_id}")
    role.metrics_path.parent.mkdir(parents=True, exist_ok=True)
    role.metrics_path.with_suffix(".stdout.txt").write_text(stdout, encoding="utf-8")
    role.metrics_path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")
    if role.process.returncode != 0:
        raise RuntimeError(f"role failed: {role.role_id}: {stderr[-2000:]}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid role JSON: {role.role_id}: {error}") from error
    payload["execution_runtime"] = role.runtime
    role.metrics_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if payload.get("status") != "PASS":
        raise RuntimeError(f"role did not pass: {role.role_id}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--docker-image", default="route-a-v8-client:rc1")
    parser.add_argument("--clients", type=int, required=True)
    parser.add_argument("--dimension", type=int, required=True)
    parser.add_argument("--noise", choices=("zero", "small", "dgg32"), required=True)
    parser.add_argument("--seed", type=int, default=2024)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-port", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--server-host")
    args = parser.parse_args()

    # Keep an ASCII junction path intact; resolving it would reintroduce a
    # non-ASCII host path into older Windows Python/WSL argument conversion.
    root = args.experiment_root.absolute()
    binary_host = root / "build" / "openfhe_dcrtpoly_wire_integration"
    if not binary_host.is_file():
        raise FileNotFoundError(binary_host)
    run_dir = args.out_dir.absolute()
    if run_dir.exists():
        raise FileExistsError(f"topology output already exists: {run_dir}")
    metrics_dir = run_dir / "metrics"
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True)

    binary_wsl = wsl_path(binary_host, args.distro)
    work_wsl = wsl_path(work_dir, args.distro)
    run_mount = str(run_dir)
    server_host = args.server_host or subprocess.check_output(
        ["wsl", "-d", args.distro, "--", "hostname", "-I"],
        text=True,
        encoding="utf-8",
        errors="ignore",
    ).split()[0]
    common = [
        "--work-dir", work_wsl,
        "--transport", "tcp",
        "--host", server_host,
        "--base-port", str(args.base_port),
        "--control-barrier", "true",
        "--clients", str(args.clients),
        "--dimension", str(args.dimension),
        "--ring-dim", "16384",
        "--towers", "2",
        "--bits", "50",
        "--plaintext-modulus", "2199023288321",
        "--k", "2",
        "--k0", "2",
        "--seed", str(args.seed),
        "--noise", args.noise,
        "--variant", "full_protocol",
        "--packing", "intcrt_polysubr",
        "--run-id", args.run_id,
        "--release-id", "v8_RC1_DOCKER_WSL",
    ]
    setup_common = [*common]
    setup_common[setup_common.index(work_wsl)] = work_wsl

    def wsl_command(role_args: list[str]) -> list[str]:
        return ["wsl", "-d", args.distro, "--", binary_wsl, *common, *role_args]

    docker_common = [*common]
    docker_common[docker_common.index(work_wsl)] = "/run/work"

    def docker_command(client_index: int) -> list[str]:
        return [
            "docker", "run", "--rm",
            "--name", f"route-a-{args.run_id}-client-{client_index:03d}",
            "--mount", f"type=bind,source={run_mount},target=/run,readonly",
            args.docker_image,
            *docker_common,
            "--role", "client", "--client-index", str(client_index),
        ]

    roles: list[dict[str, Any]] = []
    running: list[RunningRole] = []
    failure: dict[str, Any] | None = None
    try:
        setup = start_process("setup", wsl_command(["--role", "setup"]), metrics_dir, "WSL")
        roles.append(collect_role(setup, 90))

        orchestrator = start_process(
            "orchestrator", wsl_command(["--role", "orchestrator"]), metrics_dir, "WSL"
        )
        cs = start_process("CS", wsl_command(["--role", "cs"]), metrics_dir, "WSL")
        paths = [
            start_process(
                path_id,
                wsl_command(["--role", "path", "--path-id", path_id]),
                metrics_dir,
                "WSL",
            )
            for path_id in ("S1", "S2", "T1", "T2")
        ]
        clients = [
            start_process(
                f"client_{index:03d}", docker_command(index), metrics_dir, "Docker"
            )
            for index in range(args.clients)
        ]
        running = [orchestrator, cs, *paths, *clients]

        for client in clients:
            roles.append(collect_role(client, 240))
        for path in paths:
            roles.append(collect_role(path, 240))
        roles.append(collect_role(cs, 240))
        roles.append(collect_role(orchestrator, 240))
    except Exception as error:
        failure = {"type": type(error).__name__, "message": str(error)}
        for role in running:
            if role.process.poll() is None:
                role.process.kill()
                stdout, stderr = role.process.communicate()
                role.metrics_path.parent.mkdir(parents=True, exist_ok=True)
                role.metrics_path.with_suffix(".stdout.txt").write_text(stdout, encoding="utf-8")
                role.metrics_path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")

    clients = [role for role in roles if str(role.get("role_id", "")).startswith("client_")]
    paths = [role for role in roles if role.get("role_id") in {"S1", "S2", "T1", "T2"}]
    cs_metrics = next((role for role in roles if role.get("role_id") == "CS"), {})
    orchestrator_metrics = next((role for role in roles if role.get("role_id") == "orchestrator"), {})
    setup_ids = {role.get("setup_id") for role in roles if role.get("setup_id")}
    public_digests = {role.get("public_a_sha256") for role in roles if role.get("public_a_sha256")}
    prefixes = [role["initial_prng_prefix_digest"] for role in roles if role.get("initial_prng_prefix_digest")]
    client_sent = sum(int(role.get("client_upload_bytes", 0)) for role in clients)
    path_received = sum(int(role.get("received_client_bytes", 0)) for role in paths)
    path_sent = sum(int(role.get("relay_bytes", 0)) for role in paths)
    cs_received = int(cs_metrics.get("shuffle_to_cs_relay_payload_bytes", 0))
    checks = {
        "all_expected_roles_present": len(roles) == args.clients + 7,
        "all_clients_executed_in_docker": len(clients) == args.clients and all(
            role.get("execution_runtime") == "Docker" for role in clients
        ),
        "all_servers_executed_in_wsl": all(
            role.get("execution_runtime") == "WSL"
            for role in roles
            if role.get("role_id") in {"S1", "S2", "T1", "T2", "CS", "orchestrator"}
        ),
        "all_roles_ready": orchestrator_metrics.get("all_roles_ready") is True,
        "cs_complete_after_correctness": (
            orchestrator_metrics.get("cs_complete_after_release") is True
            and cs_metrics.get("cs_complete_sent_after_correctness") is True
        ),
        "shared_setup_id": len(setup_ids) == 1,
        "shared_public_a_digest": len(public_digests) == 1,
        "duplicate_initial_prng_prefixes_zero": len(prefixes) == len(set(prefixes)),
        "client_to_path_socket_byte_conservation": client_sent == path_received,
        "path_to_cs_socket_byte_conservation": path_sent == cs_received,
        "encoded_plaintext_mismatch_count_zero": cs_metrics.get("encoded_plaintext_mismatch_count") == 0,
        "direct_client_to_cs_protocol_bytes_zero": cs_metrics.get("direct_client_to_cs_bytes") == 0,
        "control_plane_excluded": all(
            role.get("control_plane_included_in_protocol_bytes") is False
            for role in roles
            if role.get("role_id") not in {"setup"}
        ),
    }
    passed = failure is None and all(checks.values())
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", args.docker_image, "--format", "{{.Id}}"], text=True
    ).strip()
    summary = {
        "schema": "route_a_v8_docker_wsl_topology_preflight_v1",
        "status": "PASS" if passed else "FAIL",
        "formal": False,
        "formal_evaluation_authorized": False,
        "run_id": args.run_id,
        "topology": {
            "clients": "independent Docker containers",
            "setup_paths_cs_orchestrator": f"WSL distro {args.distro}",
            "docker_network_mode": "bridge",
            "server_host_ipv4": server_host,
            "network_policy_client_to_cs_rejection_claimed": False,
            "protocol_direct_client_to_cs_messages": 0,
        },
        "parameters": {
            "clients": args.clients,
            "dimension": args.dimension,
            "noise": args.noise,
            "seed": args.seed,
            "base_port": args.base_port,
        },
        "artifacts": {
            "host_binary_sha256": sha256_file(binary_host),
            "docker_image": args.docker_image,
            "docker_image_id": image_id,
        },
        "communication_bytes": {
            "client_to_paths_sender": client_sent,
            "paths_receiver": path_received,
            "paths_to_cs_sender": path_sent,
            "cs_receiver": cs_received,
            "total_application_protocol_bytes": client_sent + path_sent,
            "control_plane_included": False,
        },
        "correctness": {
            "encoded_plaintext_diff_linf": cs_metrics.get("encoded_plaintext_diff_linf"),
            "encoded_plaintext_mismatch_count": cs_metrics.get("encoded_plaintext_mismatch_count"),
        },
        "checks": checks,
        "failure": failure,
        "limitations": [
            "This nonformal preflight validates process placement and protocol data flow, not WAN performance.",
            "It does not claim firewall-enforced denial of arbitrary client-to-CS connections."
        ],
    }
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
