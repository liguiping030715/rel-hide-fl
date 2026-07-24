#!/usr/bin/env python3
"""Run the v8 multi-process OpenFHE core before TCP transport integration."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
DEFAULT_BINARY = Path("/home/liguiping/openfhe_splitpath_build/openfhe_dcrtpoly_wire_integration")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_role(binary: Path, common: list[str], role_args: list[str], metrics_path: Path) -> dict[str, Any]:
    command = [str(binary), *common, *role_args]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.with_suffix(".stdout.txt").write_text(completed.stdout, encoding="utf-8")
    metrics_path.with_suffix(".stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"role failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr[-2000:]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"role emitted invalid JSON: {metrics_path.name}: {error}") from error
    metrics_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if payload.get("status") != "PASS":
        raise RuntimeError(f"role did not pass: {metrics_path.name}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--clients", type=int, required=True)
    parser.add_argument("--dimension", type=int, required=True)
    parser.add_argument("--noise", choices=("zero", "small", "dgg32"), required=True)
    parser.add_argument("--seed", type=int, default=2024)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    if not args.binary.is_file():
        raise FileNotFoundError(args.binary)
    if args.clients < 1 or args.clients > 50:
        raise ValueError("clients must be in [1,50]")

    run_dir = args.out_dir.resolve()
    if run_dir.exists():
        raise FileExistsError(f"preflight output already exists: {run_dir}")
    work_dir = run_dir / "work"
    metrics_dir = run_dir / "metrics"
    work_dir.mkdir(parents=True)

    common = [
        "--work-dir", str(work_dir),
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
        "--release-id", "v8_RC1",
    ]

    roles: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    try:
        roles.append(run_role(args.binary, common, ["--role", "setup"], metrics_dir / "setup.json"))
        for client in range(args.clients):
            roles.append(
                run_role(
                    args.binary,
                    common,
                    ["--role", "client", "--client-index", str(client)],
                    metrics_dir / f"client_{client:03d}.json",
                )
            )
        for path_id in ("S1", "S2", "T1", "T2"):
            roles.append(
                run_role(
                    args.binary,
                    common,
                    ["--role", "path", "--path-id", path_id],
                    metrics_dir / f"{path_id}.json",
                )
            )
        cs = run_role(args.binary, common, ["--role", "cs"], metrics_dir / "CS.json")
        roles.append(cs)
    except Exception as error:  # Keep the first failed attempt and every partial role artifact.
        failure = {"type": type(error).__name__, "message": str(error)}

    setup_ids = {role.get("setup_id") for role in roles if role.get("setup_id")}
    public_a_digests = {role.get("public_a_sha256") for role in roles if role.get("public_a_sha256")}
    prefixes = [
        role["initial_prng_prefix_digest"]
        for role in roles
        if role.get("initial_prng_prefix_digest")
    ]
    duplicate_prefixes = len(prefixes) - len(set(prefixes))
    cs_payload = next((role for role in roles if role.get("role_id") == "CS"), {})
    path_payloads = [role for role in roles if role.get("role_id") in {"S1", "S2", "T1", "T2"}]
    client_payloads = [role for role in roles if str(role.get("role_id", "")).startswith("client_")]
    path_received = sum(int(role.get("received_client_bytes", 0)) for role in path_payloads)
    client_sent = sum(int(role.get("client_upload_bytes", 0)) for role in client_payloads)
    path_sent = sum(int(role.get("relay_bytes", 0)) for role in path_payloads)

    checks = {
        "all_expected_role_metrics_present": len(roles) == args.clients + 6,
        "all_roles_pass": all(role.get("status") == "PASS" for role in roles),
        "shared_setup_id": len(setup_ids) == 1,
        "shared_public_a_digest": len(public_a_digests) == 1,
        "client_public_a_samples_zero": all(role.get("public_a_samples") == 0 for role in client_payloads),
        "setup_public_a_samples_one": any(
            role.get("role_id") == "setup" and role.get("public_a_samples") == 1 for role in roles
        ),
        "duplicate_initial_prng_prefixes_zero": duplicate_prefixes == 0,
        "client_to_path_file_byte_conservation": client_sent == path_received,
        "path_to_cs_file_byte_conservation": path_sent
        == int(cs_payload.get("shuffle_to_cs_relay_payload_bytes", -1)),
        "encoded_plaintext_mismatch_count_zero": cs_payload.get("encoded_plaintext_mismatch_count") == 0,
        "direct_client_to_cs_bytes_zero": cs_payload.get("direct_client_to_cs_bytes") == 0,
    }
    passed = failure is None and all(checks.values())
    summary = {
        "schema": "route_a_v8_distributed_file_preflight_summary_v1",
        "status": "PASS" if passed else "FAIL",
        "formal": False,
        "formal_evaluation_authorized": False,
        "scope": "independent_process_OpenFHE_core_with_file_frames_not_TCP",
        "run_id": args.run_id,
        "parameters": {
            "clients": args.clients,
            "dimension": args.dimension,
            "noise": args.noise,
            "application_seed": args.seed,
        },
        "binary": {
            "path": str(args.binary),
            "sha256": sha256_file(args.binary),
        },
        "role_count": len(roles),
        "randomness_consuming_role_count": len(prefixes),
        "duplicate_initial_prng_prefixes": duplicate_prefixes,
        "setup_id": next(iter(setup_ids), None),
        "public_a_sha256": next(iter(public_a_digests), None),
        "communication_bytes": {
            "client_to_paths": client_sent,
            "paths_received": path_received,
            "paths_to_cs": path_sent,
            "cs_received_relay_payload": cs_payload.get("shuffle_to_cs_relay_payload_bytes"),
            "total_application_protocol_bytes": cs_payload.get("total_application_protocol_bytes"),
        },
        "correctness": {
            "encoded_plaintext_diff_linf": cs_payload.get("encoded_plaintext_diff_linf"),
            "encoded_plaintext_mismatch_count": cs_payload.get("encoded_plaintext_mismatch_count"),
        },
        "checks": checks,
        "failure": failure,
        "remaining_gate": "replace_file_frames_with_counted_TCP_and_add_ready_release_complete_barrier",
    }
    summary_path = run_dir / "run_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # The transient payloads are retained for audit; no automatic cleanup or retry occurs.
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
