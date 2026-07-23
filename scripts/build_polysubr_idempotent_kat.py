#!/usr/bin/env python3
"""Independent KATs for the linear-factor idempotent PolySubR profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import sympy as sp


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def inv(a: int, m: int) -> int:
    return pow(a % m, -1, m)


def ntt(a: list[int], root: int, mod: int, invert: bool) -> list[int]:
    a = [x % mod for x in a]
    n = len(a)
    if n == 0 or n & (n - 1):
        raise ValueError("NTT length must be a power of two")
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    wroot = inv(root, mod) if invert else root
    length = 2
    while length <= n:
        wlen = pow(wroot, n // length, mod)
        for i in range(0, n, length):
            w = 1
            half = length // 2
            for j in range(half):
                u = a[i + j]
                v = a[i + j + half] * w % mod
                a[i + j] = (u + v) % mod
                a[i + j + half] = (u - v) % mod
                w = w * wlen % mod
        length <<= 1
    if invert:
        inv_n = inv(n, mod)
        a = [x * inv_n % mod for x in a]
    return a


def encode_idempotent(values: list[int], omega: int, mod: int) -> list[int]:
    n = len(values)
    root_n = omega * omega % mod
    coeffs = ntt(values, root_n, mod, invert=True)
    omega_inv = inv(omega, mod)
    twist = 1
    out = []
    for x in coeffs:
        out.append(x * twist % mod)
        twist = twist * omega_inv % mod
    return out


def decode_idempotent(coeffs: list[int], omega: int, mod: int) -> list[int]:
    n = len(coeffs)
    root_n = omega * omega % mod
    twist = 1
    values = []
    for x in coeffs:
        values.append(x * twist % mod)
        twist = twist * omega % mod
    return ntt(values, root_n, mod, invert=False)


def direct_encode(values: list[int], omega: int, mod: int) -> list[int]:
    n = len(values)
    inv_n = inv(n, mod)
    out = [0] * n
    for g, x in enumerate(values):
        if x % mod == 0:
            continue
        root = pow(omega, 2 * g + 1, mod)
        root_inv = inv(root, mod)
        power = 1
        for j in range(n):
            out[j] = (out[j] + x * inv_n * power) % mod
            power = power * root_inv % mod
    return out


def deterministic_vector(n: int, mod: int) -> list[int]:
    return [((i * i * 17 + 31 * i + 7) % 2001 - 1000) % mod for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=16384)
    ap.add_argument("--t", type=int, default=2199023288321)
    ap.add_argument("--out", type=Path, default=EXP_ROOT / "results" / "formal" / "correctness_profile" / "polysubr_idempotent_kat.json")
    args = ap.parse_args()

    gen = 3
    omega = pow(gen, (args.t - 1) // (2 * args.N), args.t)
    root_n = omega * omega % args.t

    checks: dict[str, object] = {}
    checks["t_primality_check"] = "PASS" if sp.isprime(args.t) else "FAIL"
    checks["primitive_2N_root_exact_order"] = "PASS" if pow(omega, args.N, args.t) == args.t - 1 and pow(omega, 2 * args.N, args.t) == 1 else "FAIL"
    checks["psi_pow_N_equals_minus_one"] = "PASS" if pow(omega, args.N, args.t) == args.t - 1 else "FAIL"
    checks["root_N_order"] = "PASS" if pow(root_n, args.N, args.t) == 1 and pow(root_n, args.N // 2, args.t) != 1 else "FAIL"

    vectors = {
        "all_zero": [0] * args.N,
        "m0_is_one": [1] + [0] * (args.N - 1),
        "deterministic_random": deterministic_vector(args.N, args.t),
    }
    roundtrip = {}
    for name, vec in vectors.items():
        decoded = decode_idempotent(encode_idempotent(vec, omega, args.t), omega, args.t)
        roundtrip[name] = {
            "result": "PASS" if decoded == [x % args.t for x in vec] else "FAIL",
            "encoded_sha256": sha256_bytes(",".join(map(str, encode_idempotent(vec, omega, args.t))).encode()),
        }
    checks["ntt_inverse_roundtrip"] = roundtrip

    projection_results = {}
    for g in [0, 1, 392, args.N - 1]:
        vec = [0] * args.N
        vec[g] = 1
        decoded = decode_idempotent(encode_idempotent(vec, omega, args.t), omega, args.t)
        ok = all((decoded[i] == (1 if i == g else 0)) for i in range(args.N))
        projection_results[str(g)] = "PASS" if ok else "FAIL"
    checks["idempotent_projection_sampled"] = projection_results

    small_n = 16
    small_omega = pow(gen, (args.t - 1) // (2 * small_n), args.t)
    small_vec = deterministic_vector(small_n, args.t)
    direct = direct_encode(small_vec, small_omega, args.t)
    fast = encode_idempotent(small_vec, small_omega, args.t)
    checks["small_ring_direct_idempotent_reference"] = {
        "N": small_n,
        "factor_product_equals_xN_plus_1": "PASS" if pow(small_omega, small_n, args.t) == args.t - 1 else "FAIL",
        "direct_encode_equals_ntt_encode": "PASS" if direct == fast else "FAIL",
        "decode_direct_encode_roundtrip": "PASS" if decode_idempotent(direct, small_omega, args.t) == [x % args.t for x in small_vec] else "FAIL",
    }

    def all_pass(x: object, key: str = "") -> bool:
        if isinstance(x, str):
            return x == "PASS" if key in {"result", "status"} or x in {"PASS", "FAIL"} else True
        if isinstance(x, dict):
            return all(all_pass(v, str(k)) for k, v in x.items())
        return True

    status = "PASS" if all(all_pass(v, str(k)) for k, v in checks.items()) else "FAIL"
    report = {
        "schema": "polysubr_idempotent_kat_v1",
        "status": status,
        "profile": {
            "N": args.N,
            "t": args.t,
            "generator": gen,
            "omega_2N": str(omega),
            "transform_domain": "plaintext_modulus_t",
            "encode_transform": "inverse_negacyclic_ntt_mod_t",
            "decode_transform": "forward_negacyclic_ntt_mod_t",
            "openfhe_setformat_used_as_packing": False,
        },
        "checks": checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": status, "out": str(args.out)}, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
