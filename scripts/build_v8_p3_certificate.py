#!/usr/bin/env python3
"""Build the v8 correctness, sampler, and implementation-conformance certificate."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).absolute().parent.parent
SOURCE = ROOT / "src" / "apbr_splitmix" / "openfhe_dcrtpoly_wire_integration.cpp"
RANDOMNESS = ROOT / "src" / "rlwe" / "v8_randomness.h"
VENDOR = ROOT / "spec" / "vendor_bindings" / "openfhe_1.2.3"
CORE_PREFLIGHT = ROOT / "results" / "preflight" / "v8_rc1_openfhe_core" / "c10_d784_dgg32.json"
CONTROL_PREFLIGHT = (
    ROOT
    / "results"
    / "preflight"
    / "v8_rc1_distributed_tcp_barrier"
    / "c10_d784_dgg32"
    / "run_summary.json"
)
DOCKER_PREFLIGHT = (
    ROOT
    / "results"
    / "preflight"
    / "v8_rc1_docker_wsl"
    / "c10_d784_dgg32"
    / "run_summary.json"
)
OUTPUT = ROOT / "results" / "certificates" / "v8_rc1" / "p3_certificate.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def centered_mod(value: int, modulus: int) -> int:
    residue = value % modulus
    return residue - modulus if residue > modulus // 2 else residue


def crt_two_centered(z1: int, z2: int, m1: int, m2: int, inverse: int) -> int:
    value = (z1 % m1) + m1 * (((z2 % m2) - (z1 % m1)) * inverse % m2)
    return centered_mod(value, m1 * m2)


def compute_bx(bz: int, m1: int, m2: int) -> Tuple[int, Dict[str, int]]:
    inverse = pow(m1, -1, m2)
    maximum = -1
    witness = {"z1": 0, "z2": 0, "centered_crt_value": 0}
    for z1 in range(-bz, bz + 1):
        for z2 in range(-bz, bz + 1):
            value = crt_two_centered(z1, z2, m1, m2, inverse)
            if abs(value) > maximum:
                maximum = abs(value)
                witness = {"z1": z1, "z2": z2, "centered_crt_value": value}
    return maximum, witness


def is_prime_64(value: int) -> bool:
    if value < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for prime in small_primes:
        if value % prime == 0:
            return value == prime
    d = value - 1
    s = 0
    while d % 2 == 0:
        s += 1
        d //= 2
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        if base % value == 0:
            continue
        x = pow(base, d, value)
        if x in (1, value - 1):
            continue
        for _ in range(s - 1):
            x = (x * x) % value
            if x == value - 1:
                break
        else:
            return False
    return True


def require_fragments(text: str, fragments: List[str]) -> Dict[str, bool]:
    return {fragment: fragment in text for fragment in fragments}


def main() -> int:
    n = 16384
    t = 2199023288321
    cmax = 50
    s_qnt = 1000
    gmax = 1.0
    bz = math.floor(s_qnt * gmax + 0.5)
    m1, m2 = 131071, 131101
    error_sigma = 3.2
    tail_multiplier = 12.00610553538285
    error_support = math.ceil(error_sigma * tail_multiplier)

    core = load_json(CORE_PREFLIGHT)
    control = load_json(CONTROL_PREFLIGHT)
    docker = load_json(DOCKER_PREFLIGHT)
    q0, q1 = [int(value) for value in core["ciphertext_tower_moduli"]]
    q_eff = q0 * q1
    bx, bx_witness = compute_bx(bz, m1, m2)

    t_prime = is_prime_64(t)
    factors_t_minus_one = [2, 5, 53, 157, 1613]
    primitive_generator = 3
    primitive_generator_check = all(
        pow(primitive_generator, (t - 1) // factor, t) != 1 for factor in factors_t_minus_one
    )
    omega = pow(primitive_generator, (t - 1) // (2 * n), t)
    root_checks = {
        "omega_to_N_equals_minus_one": pow(omega, n, t) == t - 1,
        "omega_to_2N_equals_one": pow(omega, 2 * n, t) == 1,
    }

    coordinate_lhs = 2 * cmax * bz
    coordinate_rhs = min(m1, m2)
    component_lhs = cmax * bx
    component_rhs = t // 2
    canonical_coefficient_bound = (t - 1) // 2
    aggregate_error_bound = cmax * error_support
    ciphertext_lhs = cmax * canonical_coefficient_bound + t * aggregate_error_bound
    ciphertext_rhs = q_eff // 2

    source_text = SOURCE.read_text(encoding="utf-8")
    randomness_text = RANDOMNESS.read_text(encoding="utf-8")
    dgg_impl_path = VENDOR / "discretegaussiangenerator-impl.h"
    dgg_impl = dgg_impl_path.read_text(encoding="utf-8")
    blake_path = VENDOR / "blake2engine.cpp"
    blake_source = blake_path.read_text(encoding="utf-8")

    implementation_fragments = require_fragments(
        source_text,
        [
            "DCRTPoly::DugType dug",
            "DCRTPoly::TugType tug",
            "DCRTPoly::DggType dgg(3.2)",
            "publicA * secret + error * NativeInteger(opt.plaintextModulus) + message",
            "ntt_in_place(evals, rootN, plaintextModulus, true)",
            "ntt_in_place(evals, rootN, plaintextModulus, false)",
            "serialize_poly",
            "deserialize_poly",
            "process_path",
            "CONTROL_READY",
            "CONTROL_RELEASE",
            "CONTROL_CS_COMPLETE",
        ],
    )
    randomness_fragments = require_fragments(
        randomness_text,
        ["UniformBelowFrom", "FisherYatesFrom", "threshold = static_cast<uint64_t>(-bound) % bound"],
    )
    forbidden_randomness = {
        "std_mt19937_in_protocol_source": "std::mt19937" in source_text,
        "std_normal_distribution_in_protocol_source": "std::normal_distribution" in source_text,
        "std_shuffle_in_protocol_source": "std::shuffle" in source_text,
    }
    active_fixed_seed_define = bool(
        re.search(r"^\s*#\s*define\s+FIXED_SEED\b", blake_source, flags=re.MULTILINE)
    )
    dgg_source_checks = {
        "tail_multiplier_literal_present": "12.00610553538285" in dgg_impl,
        "finite_support_uses_ceil_sigma_times_tail": (
            "std::ceil(m_std * M)" in dgg_impl and "FindInVector(m_vals, tmp)" in dgg_impl
        ),
        "computed_support": [-error_support, error_support],
    }

    profile = {
        "N": n,
        "t": str(t),
        "cmax": cmax,
        "S_qnt": s_qnt,
        "G_max": gmax,
        "B_z": bz,
        "intcrt_moduli": [m1, m2],
        "intcrt_group_size": 2,
        "Q_eff": str(q_eff),
        "tower_moduli": [str(q0), str(q1)],
        "error_sigma": error_sigma,
        "error_support": [-error_support, error_support],
        "secret_distribution": "OpenFHE centered ternary DCRTPoly::TugType",
        "public_a_distribution": "OpenFHE uniform DCRTPoly::DugType",
        "packing": "true polynomial-CRT idempotent via inverse/forward negacyclic NTT",
        "k": 2,
        "k0": 2,
    }
    checks = {
        "plaintext_modulus_prime": t_prime,
        "two_N_divides_t_minus_one": (t - 1) % (2 * n) == 0,
        "primitive_generator_check": primitive_generator_check,
        **root_checks,
        "linear_factors_pairwise_comaximal": t_prime and root_checks["omega_to_2N_equals_one"],
        "coordinate_no_wrap": {
            "lhs_2_cmax_Bz": coordinate_lhs,
            "rhs_min_intcrt_modulus": coordinate_rhs,
            "pass": coordinate_lhs < coordinate_rhs,
        },
        "component_integer_lift": {
            "B_X": bx,
            "witness": bx_witness,
            "lhs_cmax_BX": component_lhs,
            "rhs_t_over_2": component_rhs,
            "pass": component_lhs < component_rhs,
        },
        "plaintext_coefficient_canonicality": {
            "certified_centered_bound": canonical_coefficient_bound,
            "unreduced_idempotent_row_sums_are_diagnostic_only": True,
            "pass": True,
        },
        "ciphertext_centered_lift": {
            "aggregate_error_bound": aggregate_error_bound,
            "lhs": ciphertext_lhs,
            "rhs_Q_eff_over_2": ciphertext_rhs,
            "pass": ciphertext_lhs < ciphertext_rhs,
        },
        "implementation_fragments": implementation_fragments,
        "randomness_fragments": randomness_fragments,
        "forbidden_randomness_absent": not any(forbidden_randomness.values()),
        "openfhe_fixed_seed_define_active": active_fixed_seed_define,
        "dgg_source": dgg_source_checks,
        "single_process_true_idempotent_preflight": core.get("status") == "PASS",
        "control_barrier_preflight": control.get("status") == "PASS",
        "docker_wsl_topology_preflight": docker.get("status") == "PASS",
        "all_preflight_encoded_mismatches_zero": all(
            item.get("correctness", {}).get("encoded_plaintext_mismatch_count", 0) == 0
            for item in (control, docker)
        ) and core.get("encoded_plaintext_mismatch_count") == 0,
    }
    pass_flags = [
        t_prime,
        (t - 1) % (2 * n) == 0,
        primitive_generator_check,
        all(root_checks.values()),
        coordinate_lhs < coordinate_rhs,
        component_lhs < component_rhs,
        ciphertext_lhs < ciphertext_rhs,
        all(implementation_fragments.values()),
        all(randomness_fragments.values()),
        not any(forbidden_randomness.values()),
        not active_fixed_seed_define,
        dgg_source_checks["tail_multiplier_literal_present"],
        dgg_source_checks["finite_support_uses_ceil_sigma_times_tail"],
        checks["single_process_true_idempotent_preflight"],
        checks["control_barrier_preflight"],
        checks["docker_wsl_topology_preflight"],
        checks["all_preflight_encoded_mismatches_zero"],
    ]
    passed = all(pass_flags)

    vendor_hashes = {
        path.name: sha256_file(path)
        for path in sorted(VENDOR.iterdir())
        if path.is_file()
    }
    certificate = {
        "schema": "route_a_v8_p3_certificate_v1",
        "status": "PASS_V8_P3_PARAMETER_SAMPLER_IMPLEMENTATION_CERTIFICATE" if passed else "FAIL",
        "certificate_scope": "correctness, sampler binding, and implementation conformance",
        "concrete_security_bits_certified": False,
        "at_least_128_bit_pq_claim_certified": False,
        "profile": profile,
        "profile_canonical_sha256": canonical_digest(profile),
        "checks": checks,
        "source_binding": {
            "protocol_source_sha256": sha256_file(SOURCE),
            "randomness_header_sha256": sha256_file(RANDOMNESS),
            "openfhe_version": "1.2.3",
            "openfhe_source_repository_metadata": "release source directory without .git metadata",
            "openfhe_vendor_snapshot_hashes": vendor_hashes,
            "core_preflight_sha256": sha256_file(CORE_PREFLIGHT),
            "control_preflight_sha256": sha256_file(CONTROL_PREFLIGHT),
            "docker_wsl_preflight_sha256": sha256_file(DOCKER_PREFLIGHT),
        },
        "sampler_interpretation": {
            "correctness": "Finite Peikert table support yields deterministic |e| <= 39 for sigma=3.2.",
            "rlwe_estimator_normalized_sigma": 3.2,
            "estimator_rerun_required": False,
            "reason": "The conservative route makes no exact concrete-security-bit claim; this certificate does not upgrade that claim.",
        },
        "limitations": [
            "This is not a concrete RLWE security certificate.",
            "The OpenFHE release source snapshot is hash-bound because the local source tree has no Git metadata.",
            "Formal Evaluation remains unauthorized until the v8 release manifest binds this certificate, the binary, launchers, image and parameters."
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": certificate["status"], "output": str(OUTPUT)}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
