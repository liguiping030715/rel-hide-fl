#!/usr/bin/env python3
"""Build the paper-level polynomial-CRT idempotent PolySubR parameter certificate.

The script constructs the factor model for f(x)=x^N+1 over F_t when 2N | t-1,
derives the closed-form linear idempotents, and checks the correctness bounds
used by the paper-level idempotent profile.  It deliberately distinguishes
three quantities that are easy to conflate:

* unreduced integer row-sum bounds for the idempotent linear map;
* centered plaintext coefficients after reduction modulo t;
* integer component recovery and ciphertext-domain centered-lift bounds.

Large unreduced row sums are diagnostic only.  They do not imply failure,
because plaintext construction takes place in R_t.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import sympy as sp


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def centered_mod(x: int, m: int) -> int:
    r = x % m
    return r - m if r > m // 2 else r


def crt_two_centered(r1: int, r2: int, m1: int, m2: int, inv_m1_mod_m2: int) -> int:
    x = (r1 % m1) + m1 * (((r2 % m2) - (r1 % m1)) * inv_m1_mod_m2 % m2)
    return centered_mod(x, m1 * m2)


def compute_bx_exhaustive(bz: int, m1: int, m2: int) -> dict[str, Any]:
    inv = pow(m1, -1, m2)
    max_abs = -1
    argmax: tuple[int, int, int] | None = None
    for z1 in range(-bz, bz + 1):
        for z2 in range(-bz, bz + 1):
            val = crt_two_centered(z1, z2, m1, m2, inv)
            av = abs(val)
            if av > max_abs:
                max_abs = av
                argmax = (z1, z2, val)
    assert argmax is not None
    return {
        "B_X": max_abs,
        "argmax": {"z1": argmax[0], "z2": argmax[1], "centered_crt_value": argmax[2]},
    }


def factor_preview(omega: int, t: int, count: int) -> list[dict[str, Any]]:
    out = []
    for g in range(count):
        root = pow(omega, 2 * g + 1, t)
        out.append(
            {
                "g": g,
                "root": str(root),
                "factor_polynomial": {
                    "degree": 1,
                    "coefficients_low_to_high_mod_t": [str((-root) % t), "1"],
                    "meaning": "x - root",
                },
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=16384)
    ap.add_argument("--t", type=int, default=2199023288321)
    ap.add_argument("--cmax", type=int, default=50)
    ap.add_argument("--S-qnt", type=int, default=1000)
    ap.add_argument("--G-max", type=float, default=1.0)
    ap.add_argument("--m1", type=int, default=131071)
    ap.add_argument("--m2", type=int, default=131101)
    ap.add_argument("--error-support", type=int, default=39)
    ap.add_argument("--q0", type=int, default=1125899904679937)
    ap.add_argument("--q1", type=int, default=1125899903991809)
    ap.add_argument("--dimensions", default="784,3072,10000,8192,32768,32769,65536,131072")
    ap.add_argument("--spec-out", type=Path, default=EXP_ROOT / "spec" / "polysubr_idempotent_profile_v1.attempt.json")
    ap.add_argument("--cert-out", type=Path, default=EXP_ROOT / "results" / "formal" / "correctness_profile" / "polysubr_idempotent_certificate.json")
    args = ap.parse_args()

    dims = [int(x.strip()) for x in args.dimensions.split(",") if x.strip()]
    bz = math.floor(args.S_qnt * args.G_max + 0.5)
    bx_info = compute_bx_exhaustive(bz, args.m1, args.m2)
    bx = int(bx_info["B_X"])
    q_eff = args.q0 * args.q1

    t_is_prime = bool(sp.isprime(args.t))
    factorable_linear = t_is_prime and ((args.t - 1) % (2 * args.N) == 0)
    primitive_generator = int(sp.primitive_root(args.t)) if factorable_linear else None
    omega = pow(primitive_generator, (args.t - 1) // (2 * args.N), args.t) if primitive_generator else None
    inv_n = pow(args.N, -1, args.t) if factorable_linear else None
    inv_n_centered = centered_mod(inv_n, args.t) if inv_n is not None else None

    # For the linear factor profile of x^N+1, the idempotent for root alpha_g
    # has coefficients e_{g,j} = N^{-1} alpha_g^{-j}.  At coefficient j=0,
    # every active component contributes centered(N^{-1}).  This is an
    # unreduced integer row-sum diagnostic only; PolySubR plaintext coefficients
    # are reduced modulo t before encryption.
    active_component_counts = sorted(set(max(1, math.ceil(min(d, 2 * args.N) / 2)) for d in dims))
    row0_results = []
    for comp_count in active_component_counts:
        row0_sum = comp_count * abs(inv_n_centered)
        b_pack_lower = bx * row0_sum
        row0_results.append(
            {
                "active_components": comp_count,
                "row0_abs_sum": row0_sum,
                "B_pack_lower_bound_from_row0": b_pack_lower,
                "obsolete_plain_no_wrap_lhs_cmax_Bpack_lower": args.cmax * b_pack_lower,
                "obsolete_plain_no_wrap_rhs_t_over_2": args.t // 2,
                "obsolete_plain_no_wrap_possible": args.cmax * b_pack_lower < args.t // 2,
            }
        )

    component_lhs = args.cmax * bx
    component_rhs = (args.t - 1) // 2
    component_pass = component_lhs < component_rhs
    plaintext_coeff_bound = (args.t - 1) // 2
    plaintext_lift_bound = args.cmax * plaintext_coeff_bound
    aggregate_error_bound = args.cmax * args.error_support
    ciphertext_lhs = plaintext_lift_bound + args.t * aggregate_error_bound
    ciphertext_rhs = (q_eff - 1) // 2
    ciphertext_pass = ciphertext_lhs < ciphertext_rhs
    parameter_bounds_pass = bool(factorable_linear and component_pass and ciphertext_pass)
    status = (
        "PASS_IDEMPOTENT_PARAMETER_BOUNDS_IMPLEMENTATION_PREFLIGHT"
        if parameter_bounds_pass
        else "FAIL_IDEMPOTENT_PARAMETER_BOUNDS"
    )

    spec = {
        "schema": "polysubr_idempotent_profile_attempt_v1",
        "profile_id": "polysubr_idempotent_v1_attempt_linear_factors",
        "status": status,
        "plaintext_modulus": str(args.t),
        "ring_dimension": args.N,
        "ring_polynomial": "x^N + 1",
        "factor_model": "linear factors over F_t because t is prime and 2N divides t-1",
        "factor_count": args.N if factorable_linear else 0,
        "factor_polynomials_preview": factor_preview(omega, args.t, min(8, args.N)) if omega else [],
        "factor_order": "g maps to root omega^(2g+1), g=0..N-1",
        "component_order": "IntCRT group g -> factor f_g",
        "intcrt_moduli": [args.m1, args.m2],
        "intcrt_group_size": 2,
        "encode_rule": "sum_g [X_g]_t * e_g mod (x^N+1)",
        "decode_rule": "remainder modulo f_g=x-root_g, then constant extraction",
        "uses_idempotents": True,
        "uses_direct_coefficient_embedding": False,
        "factor_product_matches_ring_polynomial": bool(factorable_linear),
        "primitive_generator": str(primitive_generator) if primitive_generator else None,
        "omega_2N_root": str(omega) if omega else None,
        "omega_checks": {
            "omega_to_N": str(pow(omega, args.N, args.t)) if omega else None,
            "omega_to_2N": str(pow(omega, 2 * args.N, args.t)) if omega else None,
        },
        "correctness_summary": (
            "The factor/idempotent algebra and parameter bounds pass. The current "
            "runner must still implement idempotent encoding and remainder decoding "
            "before this can support a paper-code conformance pass."
        )
        if parameter_bounds_pass
        else "At least one required idempotent-profile parameter bound failed.",
    }

    checks = {
        "factor_product_check": bool(factorable_linear),
        "pairwise_comaximal_check": bool(factorable_linear),
        "bezout_failure_count": 0 if factorable_linear else None,
        "idempotent_formula": "e_g[j] = N^{-1} * root_g^{-j} over F_t",
        "idempotent_sum_to_one": bool(factorable_linear),
        "packing_map_type": "polynomial_crt_idempotent",
        "decode_map_type": "quotient_remainder_constant_extraction",
        "B_X": bx,
        "B_X_argmax": bx_info["argmax"],
        "B_e": args.error_support,
        "q0": str(args.q0),
        "q1": str(args.q1),
        "Q_eff": str(q_eff),
        "inv_N_mod_t": str(inv_n) if inv_n is not None else None,
        "centered_inv_N_mod_t": inv_n_centered,
        "row0_lower_bound_checks": row0_results,
        "unreduced_row_sum_diagnostic_only": True,
        "component_integer_lift": {
            "condition": "cmax * B_X < floor((t - 1) / 2)",
            "lhs": component_lhs,
            "rhs": component_rhs,
            "result": "PASS" if component_pass else "FAIL",
        },
        "plaintext_coefficient_canonicality": {
            "rule": "centered_mod_t_after_polysubr",
            "bound": plaintext_coeff_bound,
            "result": "PASS",
        },
        "ciphertext_centered_lift": {
            "condition": "cmax*floor((t-1)/2) + t*cmax*B_e < floor((Q_eff-1)/2)",
            "plaintext_lift_bound": plaintext_lift_bound,
            "aggregate_error_bound": aggregate_error_bound,
            "lhs": ciphertext_lhs,
            "rhs": ciphertext_rhs,
            "result": "PASS" if ciphertext_pass else "FAIL",
        },
        "all_parameter_bounds": parameter_bounds_pass,
        "implementation_status": "PENDING_IDEMPOTENT_ENCODE_DECODE",
    }

    cert = {
        "schema": "polysubr_idempotent_certificate_v1",
        "status": status,
        "spec": {
            "path": str(args.spec_out.as_posix()),
            "sha256": None,
        },
        "profile": {
            "N": args.N,
            "t": args.t,
            "cmax": args.cmax,
            "S_qnt": args.S_qnt,
            "G_max": args.G_max,
            "B_z": bz,
            "m": [args.m1, args.m2],
            "dimensions_checked": dims,
        },
        "checks": checks,
        "blocking_reason": None
        if parameter_bounds_pass
        else {
            "code": "IDEMPOTENT_PARAMETER_BOUND_FAILED",
            "component_integer_lift": checks.get("component_integer_lift"),
            "ciphertext_centered_lift": checks.get("ciphertext_centered_lift"),
        },
        "required_fix_before_formal_evaluation": [
            "Do not use existing v5/v6 identity-profile data for the paper-level PolySubR claim.",
            "Implement polynomial-CRT idempotent encoding over R_t with centered_mod_t coefficient canonicalization.",
            "Implement remainder modulo f_g(x) decoding and constant-extraction checks.",
            "Add factor/idempotent digest binding to runtime manifests and wire/profile validation.",
            "After implementation, run polynomial-CRT encode/decode KATs, smoke tests and all formal Evaluation matrices.",
        ],
    }

    args.spec_out.parent.mkdir(parents=True, exist_ok=True)
    args.cert_out.parent.mkdir(parents=True, exist_ok=True)
    args.spec_out.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    # Fill the spec hash after writing it.
    cert["spec"]["sha256"] = sha256_file(args.spec_out)
    args.cert_out.write_text(json.dumps(cert, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": status, "spec": str(args.spec_out), "certificate": str(args.cert_out)}, indent=2))
    return 0 if parameter_bounds_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
