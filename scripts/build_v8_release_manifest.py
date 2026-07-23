#!/usr/bin/env python3
"""Freeze the v8 formal Evaluation release after all preflight gates pass."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).absolute().parent.parent
MANIFEST = ROOT / "manifests" / "formal_evaluation_release_v8_final.json"
MANIFEST_SHA = ROOT / "manifests" / "formal_evaluation_release_v8_final.sha256"
WSL_BINARY_DIR = "/home/liguiping/openfhe_splitpath_build_v8_rc1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def deployed_sha256(filename: str) -> str:
    output = subprocess.check_output(
        ["wsl", "-d", "Ubuntu", "--", "sha256sum", f"{WSL_BINARY_DIR}/{filename}"],
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    return output.split()[0].lower()


def docker_image_id(image: str) -> str:
    return subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        text=True,
        encoding="utf-8",
        errors="ignore",
    ).strip()


def bind(relative: str) -> Dict[str, str]:
    path = ROOT / relative
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": relative.replace("\\", "/"), "sha256": sha256_file(path)}


def main() -> int:
    if MANIFEST.exists() or MANIFEST_SHA.exists():
        raise FileExistsError("v8 release manifest already exists; never overwrite a frozen release")

    p3_path = ROOT / "results" / "certificates" / "v8_rc1" / "p3_certificate.json"
    control_path = ROOT / "manifests" / "route_a_v8_control_barrier_preflight_pass_v1.json"
    topology_path = ROOT / "manifests" / "route_a_v8_docker_wsl_topology_preflight_pass_v1.json"
    p3 = load_json(p3_path)
    control = load_json(control_path)
    topology = load_json(topology_path)
    if p3.get("status") != "PASS_V8_P3_PARAMETER_SAMPLER_IMPLEMENTATION_CERTIFICATE":
        raise RuntimeError("P3 certificate is not PASS")
    if control.get("status") != "PASS" or topology.get("status") != "PASS":
        raise RuntimeError("control or Docker-WSL preflight is not PASS")

    binaries = {
        "wire_integration": bind("build/openfhe_dcrtpoly_wire_integration"),
        "bgv_only": bind("build/openfhe_bgv_only_baseline"),
        "shamir_proxy": bind("build/shamir_shuffle_proxy_baseline"),
        "randomness_selftest": bind("build/route_a_v8_randomness_selftest"),
    }
    deployed = {
        "wire_integration": deployed_sha256("openfhe_dcrtpoly_wire_integration"),
        "bgv_only": deployed_sha256("openfhe_bgv_only_baseline"),
        "shamir_proxy": deployed_sha256("shamir_shuffle_proxy_baseline"),
        "randomness_selftest": deployed_sha256("route_a_v8_randomness_selftest"),
    }
    for key, value in binaries.items():
        if value["sha256"] != deployed[key]:
            raise RuntimeError(f"workspace/deployed binary mismatch: {key}")

    sources = {
        "wire_integration_cpp": bind("src/openfhe_dcrtpoly_wire_integration.cpp"),
        "randomness_header": bind("src/v8_randomness.h"),
        "bgv_only_cpp": bind("src/openfhe_bgv_only_baseline.cpp"),
        "shamir_proxy_cpp": bind("src/shamir_shuffle_proxy_baseline.cpp"),
        "cmake": bind("CMakeLists.txt"),
    }
    scripts = {
        "formal_scalability": bind("scripts/run_formal_scalability_matrix.ps1"),
        "multiblock_scaling": bind("scripts/run_multiblock_scaling_matrix.ps1"),
        "controlled_baselines": bind("scripts/run_controlled_baselines.ps1"),
        "ablations": bind("scripts/run_ablation_matrix.ps1"),
        "fl_utility": bind("scripts/run_fl_utility.py"),
        "artifact_generation": bind("scripts/generate_evaluation_artifacts.py"),
        "p3_certificate_builder": bind("scripts/build_v8_p3_certificate.py"),
        "tcp_preflight": bind("scripts/run_v8_distributed_tcp_preflight.py"),
        "docker_wsl_preflight": bind("scripts/run_v8_docker_wsl_topology_preflight.py"),
    }

    shamir_source = (ROOT / sources["shamir_proxy_cpp"]["path"]).read_text(encoding="utf-8")
    bgv_source = (ROOT / sources["bgv_only_cpp"]["path"]).read_text(encoding="utf-8")
    if "degree-one Shamir sharing at x=1,2" not in shamir_source:
        raise RuntimeError("Shamir proxy source does not declare the frozen degree-one scheme")
    for marker in ("GenCryptoContext", "CCParams<CryptoContextBGVRNS>", "Encrypt", "EvalAdd", "Decrypt"):
        if marker not in bgv_source:
            raise RuntimeError(f"BGV-only source missing native OpenFHE marker: {marker}")

    image = "route-a-v8-client:rc1"
    image_id = docker_image_id(image)
    expected_image_id = "sha256:8c789f72840193edc1520f8f8d723d52446a4043c2e02c7d361633f9d22cd22a"
    if image_id != expected_image_id:
        raise RuntimeError("Docker client image digest changed")

    planned_outputs = {
        "scalability": "results/formal_v8/scalability",
        "multiblock": "results/formal_v8/multiblock_scaling",
        "controlled_baselines": "results/formal_v8/controlled_baselines",
        "ablations": "results/formal_v8/ablations",
        "fl_utility": "results/formal_v8/fl_utility",
    }
    for relative in planned_outputs.values():
        if (ROOT / relative).exists():
            raise FileExistsError(f"formal v8 output already exists: {relative}")

    p3_profile = p3["profile"]
    manifest: Dict[str, Any] = {
        "schema": "formal_evaluation_release_manifest_v8",
        "release": {
            "id": "formal_evaluation_release_v8_final",
            "status": "FROZEN_FOR_FORMAL_EVALUATION",
            "formal_evaluation_authorized": True,
            "formal_results_from_prior_releases_allowed": False,
            "security_claim_mode": "conservative",
            "exact_concrete_security_bits_claimed": False,
            "at_least_128_bit_pq_claimed": False,
        },
        "implementation": {
            "backend": "OpenFHE 1.2.3 low-level DCRTPoly plus native BGVRNS baseline",
            "wsl_binary_dir": WSL_BINARY_DIR,
            "docker_client_image": image,
            "docker_client_image_id": image_id,
            "shared_public_a": "sampled once by Setup with DCRTPoly::DugType and bound into setup_id",
            "rlwe_body_formula": "b_i = a*sk_i + t*e_i + iota_t_to_q(Pack_pp(z_i))",
            "secret_sampler": "DCRTPoly::TugType centered ternary",
            "error_sampler": "DCRTPoly::DggType(3.2), Peikert finite support [-39,39]",
            "packing": "true polynomial-CRT idempotent via inverse/forward negacyclic NTT",
            "permutation": "Fisher-Yates with rejection-sampled UniformBelow",
        },
        "source_and_binary": {
            "wire_integration_cpp": sources["wire_integration_cpp"]["path"],
            "wire_integration_cpp_sha256": sources["wire_integration_cpp"]["sha256"],
            "wire_integration_binary": binaries["wire_integration"]["path"],
            "wire_integration_binary_sha256": binaries["wire_integration"]["sha256"],
            "bgv_only_binary": binaries["bgv_only"]["path"],
            "bgv_only_binary_sha256": binaries["bgv_only"]["sha256"],
            "shamir_proxy_binary": binaries["shamir_proxy"]["path"],
            "shamir_proxy_binary_sha256": binaries["shamir_proxy"]["sha256"],
            "deployed_binary_sha256": deployed,
            "sources": sources,
            "scripts": scripts,
        },
        "profile": {
            "N": p3_profile["N"],
            "t": p3_profile["t"],
            "Q_eff": p3_profile["Q_eff"],
            "tower_moduli": p3_profile["tower_moduli"],
            "intcrt_moduli": p3_profile["intcrt_moduli"],
            "error_sigma": p3_profile["error_sigma"],
            "error_support": p3_profile["error_support"],
            "max_clients": p3_profile["cmax"],
            "profile_capacity": 32768,
            "k": p3_profile["k"],
            "k0": p3_profile["k0"],
            "profile_canonical_sha256": p3["profile_canonical_sha256"],
        },
        "gates": {
            "P3": {"path": str(p3_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256_file(p3_path), "status": p3["status"]},
            "control_barrier": {"path": str(control_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256_file(control_path), "status": control["status"]},
            "docker_wsl_topology": {"path": str(topology_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256_file(topology_path), "status": topology["status"]},
        },
        "formal_evaluation_plan": {
            "one_block_scalability": {
                "clients": [5, 10, 20, 30, 50],
                "dimensions": [784, 3072, 10000],
                "seeds": [2024, 2025, 2026],
                "repetitions": 5,
                "samples": 225,
            },
            "multiblock_scaling": {
                "clients": [10, 30, 50],
                "dimensions": [8192, 32768, 32769, 65536, 131072],
                "seeds": [2024, 2025, 2026],
                "repetitions": 5,
                "samples": 225,
            },
            "controlled_baselines": [
                "plain_aggregate",
                "degree_one_shamir_shuffle_proxy",
                "openfhe_native_bgv_only",
                "four_path_sum_only",
                "shuffle_only",
                "full_protocol",
            ],
            "ablations": ["full", "no_dummy", "no_apbr", "k1"],
            "fl_utility": {
                "datasets": ["MNIST", "CIFAR-10"],
                "route_mode": "protocol",
                "interpretation": "quantized-trajectory equivalence sanity check",
            },
            "output_directories": planned_outputs,
            "stale_results_policy": "exclude every pre-v8 result from v8 tables and figures",
        },
        "post_run_requirement": {
            "results_manifest": "manifests/formal_evaluation_results_v8_final.json",
            "chain": "release -> raw runs -> aggregate CSV -> paper tables/figures",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_hash = sha256_file(MANIFEST)
    MANIFEST_SHA.write_text(f"{manifest_hash}  {MANIFEST.name}\n", encoding="ascii")
    print(json.dumps({"status": manifest["release"]["status"], "manifest_sha256": manifest_hash}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
