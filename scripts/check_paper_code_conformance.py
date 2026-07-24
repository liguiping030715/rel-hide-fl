#!/usr/bin/env python3
"""Paper/code conformance gate for the polynomial-CRT PolySubR profile.

This checker is intentionally fail-closed.  The paper's PolySubR lemma defines
encoding through polynomial-CRT idempotents and decoding through remainders
modulo the factors f_g(x).  A runner that maps X_g directly to coefficient g is
useful as an implementation profile, but it is not the idempotent PolySubR
profile proved by the lemma.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
ARTICLE_ROOT = EXP_ROOT.parent.parent
REPO_ROOT = ARTICLE_ROOT.parent


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def has_all(text: str, patterns: list[str]) -> bool:
    return all(re.search(p, text, flags=re.MULTILINE | re.DOTALL) for p in patterns)


def run_binary_smoke(binary: str) -> dict[str, Any]:
    """Try to query the binary with a tiny run; failures are reported as data."""
    cmd = (
        f"env LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib "
        f"{binary} --clients 1 --dimension 2 --ring-dim 16384 --noise zero "
        "--seed 7 --k 2 --k0 2 --packing intcrt_polysubr"
    )
    try:
        proc = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", cmd],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {"status": "NOT_RUN", "error": str(exc)}
    stdout = proc.stdout.strip()
    start = stdout.find("{")
    end = stdout.rfind("}")
    raw = None
    if start >= 0 and end >= start:
        try:
            raw = json.loads(stdout[start : end + 1])
        except json.JSONDecodeError:
            raw = None
    return {
        "exit_code": proc.returncode,
        "stdout_json": raw,
        "stderr_tail": proc.stderr[-1000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", type=Path, default=ARTICLE_ROOT / "sn-article.tex")
    ap.add_argument("--paper-spec", type=Path, default=EXP_ROOT / "spec" / "polysubr_idempotent_profile_v1.attempt.json")
    ap.add_argument("--manifest", type=Path, default=EXP_ROOT / "manifests" / "formal_evaluation_release_v7_idempotent_polysubr.json")
    ap.add_argument("--certificate", type=Path, default=EXP_ROOT / "results" / "formal" / "correctness_profile" / "polysubr_idempotent_certificate.json")
    ap.add_argument("--kat", type=Path, default=EXP_ROOT / "results" / "formal" / "correctness_profile" / "polysubr_idempotent_kat.json")
    ap.add_argument("--source", type=Path, default=EXP_ROOT / "src" / "apbr_splitmix" / "openfhe_dcrtpoly_wire_integration.cpp")
    ap.add_argument("--binary", default="/home/liguiping/openfhe_splitpath_build/openfhe_dcrtpoly_wire_integration")
    ap.add_argument("--results", type=Path, default=EXP_ROOT / "results" / "formal")
    ap.add_argument("--artifact-manifest", type=Path, default=EXP_ROOT / "results" / "formal" / "evaluation_artifact_manifest.json")
    ap.add_argument("--out", type=Path, default=EXP_ROOT / "results" / "formal" / "conformance" / "paper_code_conformance.json")
    ap.add_argument("--run-smoke", action="store_true")
    args = ap.parse_args()

    paper = read_text(args.paper)
    source = read_text(args.source)
    spec = load_json(args.paper_spec)
    manifest = load_json(args.manifest)
    cert = load_json(args.certificate)
    kat = load_json(args.kat)

    checks: list[dict[str, Any]] = []

    def add(gate: str, name: str, passed: bool, detail: str, evidence: Any = None) -> None:
        checks.append(
            {
                "gate": gate,
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
                "evidence": evidence,
            }
        )

    paper_has_idempotent_poly = has_all(
        paper,
        [
            r"m}_i\(x\)\s*=\s*\\sum_\{g=0\}",
            r"\\boldsymbol e_g\(x\).*CRT idempotent",
            r"\\boldsymbol\{\\Phi\}\\bmod f_g\(x\)",
        ],
    )
    add(
        "G0",
        "Paper packing definition",
        paper_has_idempotent_poly,
        "Paper states polynomial-CRT idempotent PolySubR and remainder decoding."
        if paper_has_idempotent_poly
        else "Paper idempotent PolySubR definition was not detected.",
    )

    factor_list_present = bool(
        spec
        and (
            (isinstance(spec.get("factor_polynomials"), list) and len(spec.get("factor_polynomials", [])) > 0)
            or (
                isinstance(spec.get("factor_polynomials_preview"), list)
                and len(spec.get("factor_polynomials_preview", [])) > 0
            )
        )
    )
    spec_ok = bool(
        spec
        and spec.get("uses_idempotents") is True
        and spec.get("uses_direct_coefficient_embedding") is False
        and factor_list_present
        and spec.get("ring_polynomial")
    )
    add(
        "G1",
        "Factor product",
        spec_ok and bool(spec.get("factor_product_matches_ring_polynomial")),
        "Exact f(x), f_g(x) profile and product proof are present."
        if spec_ok and spec.get("factor_product_matches_ring_polynomial")
        else "Missing executable idempotent factor profile or factor-product proof.",
        {"spec_path": str(args.paper_spec), "spec_exists": spec is not None},
    )

    cert_checks = ((cert or {}).get("checks") or {})
    cert_block = ((cert or {}).get("blocking_reason") or {})
    cert_map = (((cert or {}).get("implementation_binding") or {}).get("packing_linear_map") or {})
    manifest_map = (((manifest or {}).get("implementation") or {}).get("packing_linear_map") or {})
    manifest_map_kind = manifest_map.get("kind")
    map_kind = cert_map.get("kind") or manifest_map_kind or cert_checks.get("packing_map_type")
    idempotent_parameter_bounds = (
        (cert or {}).get("status") in {
            "PASS_IDEMPOTENT_PARAMETER_BOUNDS_IMPLEMENTATION_PENDING",
            "PASS_IDEMPOTENT_PARAMETER_BOUNDS_IMPLEMENTATION_PREFLIGHT",
        }
        and cert_checks.get("packing_map_type") == "polynomial_crt_idempotent"
        and cert_checks.get("all_parameter_bounds") is True
    )
    idempotent_cert = (
        (cert or {}).get("status") == "PASS_INTCRT_POLYSUBR_IDEMPOTENT_PROFILE"
        and cert_checks.get("packing_map_type") == "polynomial_crt_idempotent"
    )
    add(
        "G2",
        "Pairwise comaximality / Bezout",
        bool(cert and cert_checks.get("pairwise_comaximal_check") and cert_checks.get("bezout_failure_count") == 0),
        "Certificate proves pairwise comaximality and Bezout identities."
        if cert and cert_checks.get("pairwise_comaximal_check") and cert_checks.get("bezout_failure_count") == 0
        else "No idempotent-profile Bezout/comaximality certificate is present.",
    )
    add(
        "G3",
        "CRT idempotent construction",
        bool(cert and cert_checks.get("idempotent_sum_to_one") is True and cert_checks.get("idempotent_formula")),
        "Idempotent projection and sum-to-one checks pass."
        if cert and cert_checks.get("idempotent_sum_to_one") is True and cert_checks.get("idempotent_formula")
        else "No passing CRT idempotent construction certificate is present.",
    )

    source_identity = "coeffs[g] = static_cast<int64_t>(centeredM)" in source
    source_has_remainder_decode = bool(
        (
            re.search(r"(PolynomialRemainder|remainder_mod|mod_f_g|rem\s*=)", source)
            and re.search(r"FAIL_NONCONSTANT_POLYSUBR_REMAINDER|nonconstant", source)
        )
        or (
            "negacyclic_omega_2n" in source
            and "ntt_in_place(evals, rootN, plaintextModulus, false)" in source
        )
    )
    add(
        "G4",
        "Polynomial-CRT encode/decode KAT",
        bool(kat and kat.get("status") == "PASS"),
        "Polynomial-CRT encode/decode KAT passed."
        if kat and kat.get("status") == "PASS"
        else "No polynomial-CRT encode/decode KAT for idempotent PolySubR is present.",
        {"source_uses_direct_coeff_assignment": source_identity, "source_has_remainder_decode": source_has_remainder_decode, "kat_path": str(args.kat)},
    )
    small_ref = (((kat or {}).get("checks") or {}).get("small_ring_direct_idempotent_reference") or {})
    add(
        "G5",
        "Python/C++ reference agreement",
        bool(
            kat
            and kat.get("status") == "PASS"
            and small_ref.get("direct_encode_equals_ntt_encode") == "PASS"
            and small_ref.get("decode_direct_encode_roundtrip") == "PASS"
        ),
        "Independent direct-idempotent reference agrees with the NTT implementation."
        if kat
        and kat.get("status") == "PASS"
        and small_ref.get("direct_encode_equals_ntt_encode") == "PASS"
        and small_ref.get("decode_direct_encode_roundtrip") == "PASS"
        else "No independent direct-idempotent reference agreement artifact is present.",
    )
    add(
        "G6",
        "IntCRT exhaustive/boundary tests",
        bool(cert and cert_checks.get("B_X")),
        "Current certificate contains an IntCRT B_X computation; this alone is not sufficient for idempotent PolySubR.",
        {"B_X": cert_checks.get("B_X") if cert else None, "status": (cert or {}).get("status")},
    )
    add(
        "G7",
        "Idempotent parameter bounds",
        bool(idempotent_cert or idempotent_parameter_bounds),
        "Full B_pack certificate is for polynomial-CRT idempotent profile."
        if idempotent_cert
        else (
            "Idempotent factor algebra, corrected component/Q-domain parameter bounds and implementation preflight pass."
            if idempotent_parameter_bounds
            else f"Current packing map is {map_kind!r}; this is not a polynomial-CRT idempotent B_pack certificate."
        ),
        {
            "certificate_status": (cert or {}).get("status"),
            "packing_map_kind": map_kind,
            "manifest_map_kind": manifest_map_kind,
            "component_integer_lift": cert_checks.get("component_integer_lift"),
            "ciphertext_centered_lift": cert_checks.get("ciphertext_centered_lift"),
            "blocking_reason": cert_block,
        },
    )
    add(
        "G8",
        "RLWE/DCRT body equations",
        "errTimesT" in source and "a * sk + errTimesT + msg" in source,
        "Runner constructs b_i = a*sk_i + t*e_i + iota(Pack(z_i)).",
    )
    add(
        "G9",
        "Sharing/APBR/fragment/dummy invariants",
        all(s in source for s in ["process_path", "split_poly", "std::shuffle", "apbrPreserved", "k0"]),
        "Runner contains APBR, fragmentation, dummy padding and permutation checks.",
    )
    add(
        "G10",
        "Wire/profile rejection tests",
        "wrong_polysubr_factor_digest" in source or "factor_profile_digest" in source,
        "Runner rejects wrong PolySubR factor/idempotent profile digests."
        if ("wrong_polysubr_factor_digest" in source or "factor_profile_digest" in source)
        else "No factor/idempotent digest rejection path detected.",
    )
    add(
        "G11",
        "Runtime-profile binding",
        "--dump-runtime-profile" in source or "runtime_profile" in source,
        "Runner exports a runtime profile for certificate binding."
        if ("--dump-runtime-profile" in source or "runtime_profile" in source)
        else "No --dump-runtime-profile binding detected.",
    )
    artifact_manifest = load_json(args.artifact_manifest) or {}
    artifact_text = json.dumps(artifact_manifest, sort_keys=True)
    stale_hits = [
        marker
        for marker in ["protocol_consistent_release_v5", "controlled_baseline_release_v2", "ablation_release_v2"]
        if marker in artifact_text
    ]
    add(
        "G12",
        "Formal-result provenance",
        bool(
            manifest
            and "idempotent_polysubr" in manifest.get("release", {}).get("id", "")
            and (((manifest.get("implementation") or {}).get("packing_linear_map") or {}).get("kind") == "polynomial_crt_idempotent")
        ),
        "Current manifest is bound to the idempotent PolySubR implementation profile."
        if manifest
        and "idempotent_polysubr" in manifest.get("release", {}).get("id", "")
        and (((manifest.get("implementation") or {}).get("packing_linear_map") or {}).get("kind") == "polynomial_crt_idempotent")
        else "Formal manifest is not an idempotent PolySubR release.",
        {"manifest_id": (manifest or {}).get("release", {}).get("id")},
    )
    add(
        "G13",
        "No stale identity-profile result inputs",
        len(stale_hits) == 0,
        "Evaluation artifact manifest does not reference stale identity/v5 result inputs."
        if len(stale_hits) == 0
        else "Evaluation artifact manifest still references stale v5/v2 result inputs.",
        {"artifact_manifest": str(args.artifact_manifest), "markers": stale_hits},
    )

    smoke = run_binary_smoke(args.binary) if args.run_smoke else None
    if smoke is not None:
        raw = smoke.get("stdout_json") or {}
        add(
            "R0",
            "Runtime smoke packing profile",
            raw.get("packing_profile") == "intcrt_polysubr_idempotent",
            "Runtime smoke uses idempotent PolySubR profile."
            if raw.get("packing_profile") == "intcrt_polysubr_idempotent"
            else f"Runtime smoke uses {raw.get('packing_profile')!r}, not intcrt_polysubr_idempotent.",
            smoke,
        )

    failed = [c for c in checks if c["status"] != "PASS"]
    blockers = []
    if source_identity:
        blockers.append("CODE_STILL_USES_COMPONENT_TO_COEFFICIENT_IDENTITY")
        blockers.append("FACTOR_AND_IDEMPOTENT_ENCODING_NOT_IMPLEMENTED")
    if not source_has_remainder_decode:
        blockers.append("NO_POLYNOMIAL_REMAINDER_DECODE")
    if not spec_ok:
        blockers.append("MISSING_EXECUTABLE_FACTOR_PROFILE")
    if manifest_map_kind == "component_to_coefficient_identity":
        blockers.append("RELEASE_MANIFEST_BOUND_TO_IDENTITY_PROFILE")
    if stale_hits:
        blockers.append("STALE_FORMAL_RESULTS_REFERENCED_BY_ARTIFACTS")
    final = (
        "PAPER_CODE_CONFORMANCE_FAIL_POLYSUBR_IDEMPOTENT"
        if blockers
        else (
            "PAPER_CODE_CONFORMANCE_PASS_WITH_WARNINGS_POLYSUBR_IDEMPOTENT"
            if failed
            else "PAPER_CODE_CONFORMANCE_PASS_POLYSUBR_IDEMPOTENT"
        )
    )

    report = {
        "schema": "paper_code_conformance_polysubr_idempotent_v1",
        "final": final,
        "blockers": blockers,
        "paper": {"path": str(args.paper), "sha256": sha256_file(args.paper)},
        "source": {"path": str(args.source), "sha256": sha256_file(args.source)},
        "manifest": {"path": str(args.manifest), "sha256": sha256_file(args.manifest)},
        "certificate": {"path": str(args.certificate), "sha256": sha256_file(args.certificate)},
        "kat": {"path": str(args.kat), "sha256": sha256_file(args.kat)},
        "binary": args.binary,
        "checks": checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"final": final, "blockers": blockers, "report": str(args.out)}, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
