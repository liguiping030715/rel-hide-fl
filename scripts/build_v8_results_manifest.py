import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "manifests" / "formal_evaluation_release_v8_final.json"
ARTIFACTS = ROOT / "results" / "formal_v8" / "paper_artifacts" / "evaluation_artifact_manifest.json"
OUTPUT = ROOT / "manifests" / "formal_evaluation_results_v8_final.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def bind(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": rel(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def require_summary(path: Path, expected: int) -> dict:
    summary = load_json(path)
    if summary.get("status") != "PASS" or summary.get("formal") is not True:
        raise RuntimeError(f"Formal summary did not pass: {path}")
    if int(summary.get("sample_count", -1)) != expected:
        raise RuntimeError(f"Unexpected sample count in {path}")
    if int(summary.get("passed", -1)) != expected or int(summary.get("failed", -1)) != 0:
        raise RuntimeError(f"Incomplete formal matrix: {path}")
    if float(summary.get("max_encoded_plaintext_diff_linf", 0)) != 0:
        raise RuntimeError(f"Nonzero encoded plaintext difference: {path}")
    if float(summary.get("total_encoded_plaintext_mismatch_count", 0)) != 0:
        raise RuntimeError(f"Encoded plaintext mismatch: {path}")
    return summary


def bind_tree(directory: Path, patterns=("*.json", "*.csv", "*.txt")) -> list:
    paths = []
    for pattern in patterns:
        paths.extend(directory.rglob(pattern))
    return [bind(path) for path in sorted(set(paths)) if path.is_file()]


def main() -> None:
    release = load_json(RELEASE)
    release_sha = sha256(RELEASE)
    expected_release_sha = "4dc35423dac9696359bdc13f909d94f47f1af76f6c71dec9ace0fa2aa60ade1c"
    if release_sha != expected_release_sha:
        raise RuntimeError("Frozen v8 release manifest hash changed")
    if release.get("release", {}).get("formal_evaluation_authorized") is not True:
        raise RuntimeError("Release does not authorize formal evaluation")

    certificate_path = ROOT / "results/certificates/v8_rc1/p3_certificate.json"
    certificate = load_json(certificate_path)
    if certificate.get("status") != "PASS_V8_P3_PARAMETER_SAMPLER_IMPLEMENTATION_CERTIFICATE":
        raise RuntimeError("v8 correctness certificate did not pass")

    matrix_specs = {
        "scalability": (ROOT / "results/formal_v8/scalability/retry1", 225),
        "multiblock": (ROOT / "results/formal_v8/multiblock_scaling_paper", 225),
        "controlled_baselines": (ROOT / "results/formal_v8/controlled_baselines", 450),
        "ablations": (ROOT / "results/formal_v8/ablations", 60),
    }
    matrices = {}
    for name, (directory, expected) in matrix_specs.items():
        summary_path = directory / "summary.json"
        require_summary(summary_path, expected)
        matrices[name] = {
            "expected_samples": expected,
            "summary": bind(summary_path),
            "files": bind_tree(directory),
        }

    utility_runs = []
    for seed in (2024, 2025, 2026):
        directory = ROOT / f"results/formal_v8/fl_utility/seed{seed}"
        summary_path = directory / "summary.json"
        summary = load_json(summary_path)
        if summary.get("status") != "PASS" or summary.get("formal") is not True:
            raise RuntimeError(f"Utility seed {seed} did not pass")
        if len(summary.get("cases", [])) != 2:
            raise RuntimeError(f"Utility seed {seed} does not contain both datasets")
        for case in summary["cases"]:
            if case.get("status") != "PASS":
                raise RuntimeError(f"Utility case failed for seed {seed}")
            if int(case.get("max_route_quantized_diff_linf", -1)) != 0:
                raise RuntimeError(f"Utility aggregate difference for seed {seed}")
            if int(case.get("total_route_block_mismatches", -1)) != 0:
                raise RuntimeError(f"Utility block mismatch for seed {seed}")
            if case.get("route_mode") != "protocol":
                raise RuntimeError(f"Utility seed {seed} did not use protocol mode")
        utility_runs.append({
            "seed": seed,
            "summary": bind(summary_path),
            "files": bind_tree(directory),
        })

    artifact_manifest = load_json(ARTIFACTS)
    if artifact_manifest.get("status") != "PASS":
        raise RuntimeError("Evaluation artifact generation did not pass")
    if artifact_manifest.get("release_manifest_sha256", "").lower() != release_sha:
        raise RuntimeError("Artifact manifest is not bound to the frozen release")

    manifest = {
        "schema": "formal_evaluation_results_manifest_v8",
        "status": "PASS",
        "release_id": "formal_evaluation_release_v8_final",
        "parent_release_manifest": bind(RELEASE),
        "manifest_builder": bind(Path(__file__).resolve()),
        "correctness_certificate": {
            "json": bind(certificate_path),
            "paper_table": bind(ROOT / "results/certificates/v8_rc1/profile_certificate_table.tex"),
        },
        "security_claim_mode": "conservative",
        "exact_concrete_security_bits_claimed": False,
        "at_least_128_bit_pq_claimed": False,
        "formal_results": {
            "matrices": matrices,
            "fl_utility": {
                "datasets": ["MNIST", "CIFAR-10"],
                "seeds": [2024, 2025, 2026],
                "case_count": 6,
                "route_mode": "protocol",
                "runs": utility_runs,
            },
        },
        "paper_artifacts": {
            "manifest": bind(ARTIFACTS),
            "files": bind_tree(ARTIFACTS.parent, patterns=("*.json", "*.csv", "*.md", "*.tex", "*.png", "*.pdf")),
        },
        "exclusions": {
            "pre_v8_results": "not part of this evidence chain",
            "results/formal_v8/scalability_parent_failure": "first WSL-launch failure retained outside retry1",
            "results/formal_v8/multiblock_scaling": "extra dimension matrix; not used by the paper",
            "docker_wsl_preflights": "functional topology validation only; not performance samples",
        },
        "claims_supported": {
            "formal_aggregate_correctness": True,
            "local_runner_scalability": True,
            "serialized_application_payload": True,
            "component_ablation": True,
            "quantized_training_trajectory_equivalence": True,
            "wan_performance": False,
            "malicious_shuffler_detection": False,
            "concrete_128_bit_pq_security": False,
        },
    }
    OUTPUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    digest = sha256(OUTPUT)
    OUTPUT.with_suffix(".sha256").write_text(
        f"{digest}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "PASS",
        "manifest": rel(OUTPUT),
        "sha256": digest,
        "matrix_samples": 225 + 225 + 450 + 60,
        "utility_cases": 6,
    }, indent=2))


if __name__ == "__main__":
    main()
