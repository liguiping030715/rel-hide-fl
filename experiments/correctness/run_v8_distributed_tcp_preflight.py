#!/usr/bin/env python3
"""Run the v8 OpenFHE roles over counted local TCP sockets."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BINARY = Path("/home/liguiping/openfhe_splitpath_build/openfhe_dcrtpoly_wire_integration")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class RunningRole:
    role_id: str
    process: subprocess.Popen[str]
    metrics_path: Path


def start_role(binary: Path, common: list[str], role_id: str, role_args: list[str], metrics_dir: Path) -> RunningRole:
    command = [str(binary), *common, *role_args]
    process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return RunningRole(role_id, process, metrics_dir / f"{role_id}.json")


def collect_role(running: RunningRole, timeout: int) -> dict[str, Any]:
    try:
        stdout, stderr = running.process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        running.process.kill()
        stdout, stderr = running.process.communicate()
        running.metrics_path.with_suffix(".stdout.txt").write_text(stdout, encoding="utf-8")
        running.metrics_path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")
        raise RuntimeError(f"role timeout: {running.role_id}")
    running.metrics_path.parent.mkdir(parents=True, exist_ok=True)
    running.metrics_path.with_suffix(".stdout.txt").write_text(stdout, encoding="utf-8")
    running.metrics_path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")
    if running.process.returncode != 0:
        raise RuntimeError(f"role failed: {running.role_id}: {stderr[-2000:]}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid role JSON: {running.role_id}: {error}") from error
    running.metrics_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if payload.get("status") != "PASS":
        raise RuntimeError(f"role did not pass: {running.role_id}")
    return payload


def run_setup(binary: Path, common: list[str], metrics_dir: Path) -> dict[str, Any]:
    running = start_role(binary, common, "setup", ["--role", "setup"], metrics_dir)
    return collect_role(running, 60)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--clients", type=int, required=True)
    parser.add_argument("--dimension", type=int, required=True)
    parser.add_argument("--noise", choices=("zero", "small", "dgg32"), required=True)
    parser.add_argument("--variant", choices=("full_protocol", "four_path_sum_only"), default="full_protocol")
    parser.add_argument("--seed", type=int, default=2024)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-port", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--control-barrier", action="store_true")
    parser.add_argument("--role-timeout", type=int, default=180)
    args = parser.parse_args()

    if not args.binary.is_file():
        raise FileNotFoundError(args.binary)
    if args.clients < 1 or args.clients > 50:
        raise ValueError("clients must be in [1,50]")
    if not (1024 <= args.base_port <= 65530):
        raise ValueError("base port must leave room for five listeners")

    run_dir = args.out_dir.resolve()
    if run_dir.exists():
        raise FileExistsError(f"preflight output already exists: {run_dir}")
    metrics_dir = run_dir / "metrics"
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True)

    common = [
        "--work-dir", str(work_dir),
        "--transport", "tcp",
        "--host", "127.0.0.1",
        "--base-port", str(args.base_port),
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
        "--variant", args.variant,
        "--packing", "intcrt_polysubr",
        "--run-id", args.run_id,
        "--release-id", "v8_RC1",
        "--control-barrier", "true" if args.control_barrier else "false",
    ]

    roles: list[dict[str, Any]] = []
    running: list[RunningRole] = []
    failure: dict[str, Any] | None = None
    orchestrator: RunningRole | None = None
    try:
        roles.append(run_setup(args.binary, common, metrics_dir))
        if args.control_barrier:
            orchestrator = start_role(
                args.binary, common, "orchestrator", ["--role", "orchestrator"], metrics_dir
            )
        running.append(start_role(args.binary, common, "CS", ["--role", "cs"], metrics_dir))
        for path_id in ("S1", "S2", "T1", "T2"):
            running.append(
                start_role(
                    args.binary,
                    common,
                    path_id,
                    ["--role", "path", "--path-id", path_id],
                    metrics_dir,
                )
            )
        clients = [
            start_role(
                args.binary,
                common,
                f"client_{client:03d}",
                ["--role", "client", "--client-index", str(client)],
                metrics_dir,
            )
            for client in range(args.clients)
        ]
        for client in clients:
            roles.append(collect_role(client, args.role_timeout))
        for role in running[1:]:  # Paths relay only after all client uploads arrive.
            roles.append(collect_role(role, args.role_timeout))
        roles.append(collect_role(running[0], args.role_timeout))  # CS completes after four path relays.
        if orchestrator is not None:
            roles.append(collect_role(orchestrator, args.role_timeout))
    except Exception as error:
        failure = {"type": type(error).__name__, "message": str(error)}
        all_running = [*running, *([] if orchestrator is None else [orchestrator])]
        for role in all_running:
            if role.process.poll() is None:
                role.process.kill()
                stdout, stderr = role.process.communicate()
                role.metrics_path.with_suffix(".stdout.txt").write_text(stdout, encoding="utf-8")
                role.metrics_path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")

    setup_ids = {role.get("setup_id") for role in roles if role.get("setup_id")}
    public_a_digests = {role.get("public_a_sha256") for role in roles if role.get("public_a_sha256")}
    prefixes = [role["initial_prng_prefix_digest"] for role in roles if role.get("initial_prng_prefix_digest")]
    duplicate_prefixes = len(prefixes) - len(set(prefixes))
    clients = [role for role in roles if str(role.get("role_id", "")).startswith("client_")]
    paths = [role for role in roles if role.get("role_id") in {"S1", "S2", "T1", "T2"}]
    cs = next((role for role in roles if role.get("role_id") == "CS"), {})
    orchestrator_metrics = next((role for role in roles if role.get("role_id") == "orchestrator"), {})
    client_sent = sum(int(role.get("client_upload_bytes", 0)) for role in clients)
    path_received = sum(int(role.get("received_client_bytes", 0)) for role in paths)
    path_sent = sum(int(role.get("relay_bytes", 0)) for role in paths)
    cs_received = int(cs.get("shuffle_to_cs_relay_payload_bytes", 0))
    total_application_bytes = client_sent + path_sent

    checks = {
        "all_expected_role_metrics_present": len(roles) == args.clients + (7 if args.control_barrier else 6),
        "all_roles_pass": all(role.get("status") == "PASS" for role in roles),
        "all_protocol_roles_use_counted_tcp": all(
            role.get("transport") == "counted_tcp_preflight"
            for role in roles
            if role.get("role_id") not in {"setup", "orchestrator"}
        ),
        "shared_setup_id": len(setup_ids) == 1,
        "shared_public_a_digest": len(public_a_digests) == 1,
        "client_public_a_samples_zero": all(role.get("public_a_samples") == 0 for role in clients),
        "setup_public_a_samples_one": any(
            role.get("role_id") == "setup" and role.get("public_a_samples") == 1 for role in roles
        ),
        "duplicate_initial_prng_prefixes_zero": duplicate_prefixes == 0,
        "client_to_path_socket_byte_conservation": client_sent == path_received,
        "path_to_cs_socket_byte_conservation": path_sent == cs_received,
        "encoded_plaintext_mismatch_count_zero": cs.get("encoded_plaintext_mismatch_count") == 0,
        "direct_client_to_cs_bytes_zero": cs.get("direct_client_to_cs_bytes") == 0,
        "control_barrier_all_roles_ready": (
            not args.control_barrier or orchestrator_metrics.get("all_roles_ready") is True
        ),
        "control_round_release_observed": (
            not args.control_barrier or orchestrator_metrics.get("round_release_observed") is True
        ),
        "control_cs_complete_after_correctness": (
            not args.control_barrier
            or (
                orchestrator_metrics.get("cs_complete_after_release") is True
                and cs.get("cs_complete_sent_after_correctness") is True
            )
        ),
        "control_plane_excluded_from_protocol_bytes": all(
            role.get("control_plane_included_in_protocol_bytes") is False
            for role in roles
            if role.get("role_id") not in {"setup"}
        ) if args.control_barrier else True,
    }
    passed = failure is None and all(checks.values())
    summary = {
        "schema": "route_a_v8_distributed_tcp_preflight_summary_v1",
        "status": "PASS" if passed else "FAIL",
        "formal": False,
        "formal_evaluation_authorized": False,
        "scope": (
            "independent_process_OpenFHE_core_with_counted_local_TCP_and_control_barrier"
            if args.control_barrier
            else "independent_process_OpenFHE_core_with_counted_local_TCP_no_control_barrier"
        ),
        "run_id": args.run_id,
        "parameters": {
            "clients": args.clients,
            "dimension": args.dimension,
            "noise": args.noise,
            "variant": args.variant,
            "application_seed": args.seed,
            "base_port": args.base_port,
            "control_barrier": args.control_barrier,
        },
        "binary": {"path": str(args.binary), "sha256": sha256_file(args.binary)},
        "role_count": len(roles),
        "randomness_consuming_role_count": len(prefixes),
        "duplicate_initial_prng_prefixes": duplicate_prefixes,
        "setup_id": next(iter(setup_ids), None),
        "public_a_sha256": next(iter(public_a_digests), None),
        "communication_bytes": {
            "client_to_paths_sender": client_sent,
            "paths_receiver": path_received,
            "paths_to_cs_sender": path_sent,
            "cs_receiver": cs_received,
            "total_application_protocol_bytes": total_application_bytes,
            "shuffle_to_cs_relay_payload_bytes": path_sent,
            "control_plane_bytes_sent_by_orchestrator": int(orchestrator_metrics.get("control_bytes_sent", 0)),
            "control_plane_bytes_received_by_orchestrator": int(orchestrator_metrics.get("control_bytes_received", 0)),
            "control_plane_included_in_total_application_protocol_bytes": False,
        },
        "orchestrator": {
            "all_roles_ready": orchestrator_metrics.get("all_roles_ready"),
            "round_release_observed": orchestrator_metrics.get("round_release_observed"),
            "cs_complete_after_release": orchestrator_metrics.get("cs_complete_after_release"),
            "steady_state_protocol_round_ms": orchestrator_metrics.get("steady_state_protocol_round_ms"),
        },
        "correctness": {
            "encoded_plaintext_diff_linf": cs.get("encoded_plaintext_diff_linf"),
            "encoded_plaintext_mismatch_count": cs.get("encoded_plaintext_mismatch_count"),
        },
        "checks": checks,
        "failure": failure,
        "remaining_gate": (
            "Docker_client_to_WSL_server_topology_then_P3_release_manifest"
            if args.control_barrier
            else "add_READY_ROUND_RELEASE_CS_COMPLETE_control_barrier_then_Docker_client_topology"
        ),
    }
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
