import hashlib
import json
import platform
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT.parents[1]
DATA_ROOT = TEMPLATE_ROOT / "data"
MANIFEST_DIR = ROOT / "manifests"
RELEASE = MANIFEST_DIR / "formal_evaluation_release_v8_final.json"
RESULTS = MANIFEST_DIR / "formal_evaluation_results_v8_final.json"
OUTPUT = MANIFEST_DIR / "formal_evaluation_v8_reconciliation.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.relative_to(TEMPLATE_ROOT).as_posix()


def bind(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": relative(path),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


def canonical_digest(value) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def array_digest(array: np.ndarray) -> str:
    normalized = np.asarray(array, dtype="<i8")
    return hashlib.sha256(normalized.tobytes(order="C")).hexdigest()


def reconstruct_partitions(seed: int) -> dict:
    rng = np.random.default_rng(seed)
    train_indices = rng.choice(60000, size=10000, replace=False)
    test_indices = rng.choice(10000, size=2000, replace=False)
    client_permutation = rng.permutation(10000)
    components = {
        "train_subset_indices_sha256": array_digest(train_indices),
        "test_subset_indices_sha256": array_digest(test_indices),
        "client_partition_permutation_sha256": array_digest(client_permutation),
    }
    return {
        "seed": seed,
        **components,
        "combined_partition_index_sha256": canonical_digest(components),
    }


def main() -> None:
    release_sha = sha256(RELEASE)
    if release_sha != "4dc35423dac9696359bdc13f909d94f47f1af76f6c71dec9ace0fa2aa60ade1c":
        raise RuntimeError("Frozen v8 release manifest changed")
    if not RESULTS.is_file():
        raise FileNotFoundError(RESULTS)

    dataset_files = [
        bind(path) for path in sorted(DATA_ROOT.rglob("*")) if path.is_file()
    ]
    utility_script = ROOT / "experiments" / "utility" / "run_fl_utility.py"
    partitions = [reconstruct_partitions(seed) for seed in (2024, 2025, 2026)]

    try:
        import sklearn

        sklearn_version = sklearn.__version__
    except ImportError:
        sklearn_version = None

    manifest = {
        "schema": "formal_evaluation_v8_reconciliation_v1",
        "status": "PASS_WITH_EXPLICIT_LIMITATIONS",
        "immutable_history_policy": {
            "frozen_release_modified": False,
            "raw_formal_results_modified": False,
            "purpose": "Reconcile post-run locators, schema names, and supplemental provenance without rewriting frozen history.",
        },
        "parents": {
            "release_manifest": bind(RELEASE),
            "results_manifest": bind(RESULTS),
        },
        "multiblock_directory_reconciliation": {
            "frozen_expected_directory": "results/formal_v8/multiblock_scaling",
            "accepted_actual_directory": "results/formal_v8/multiblock_scaling_paper",
            "old_matrix_excluded": True,
            "replacement_matrix_completed": True,
            "replacement_files_bound_by_results_manifest": True,
            "numerical_results_recomputed": True,
            "affected_paper_outputs": [
                "table_multiblock_scaling",
                "fig_runtime_scaling",
                "fig_communication_scaling",
            ],
        },
        "raw_schema_errata": {
            "legacy_field": "q_domain_diff_linf",
            "actual_semantics": "encoded_plaintext_diff_linf",
            "comparison_domain": "post_key_removal_mod_t_encoded_plaintext",
            "ring_q_coefficient_difference": False,
            "raw_json_rewritten": False,
            "correct_names_used_by_paper": [
                "wire_aggregate_equals_local",
                "encoded_plaintext_diff_linf",
                "encoded_plaintext_mismatch_count",
            ],
        },
        "baseline_profile_disclosure": {
            "baseline": "native OpenFHE BGV default-profile reference",
            "full_plaintext_modulus": "2199023288321",
            "bgv_plaintext_modulus": "536903681",
            "strict_same_parameter_decomposition": False,
            "same_parameter_probe": {
                "attempted_plaintext_modulus": "2199023288321",
                "status": "UNSUPPORTED_BY_NATIVE_OPENFHE_BGVRNS_PARAMETER_GENERATION",
                "diagnostic": "OpenFHE 1.2.3 computeModuli requested unsupported moduli greater than 60 bits.",
            },
        },
        "formal_harness_randomness_boundary": {
            "security_theorem_deployment": "independently initialized process- and path-local CSPRNG streams",
            "formal_performance_harness": "single process; four logical paths consume one process-wide OpenFHE PRNG stream",
            "harness_instantiates_corruption_separated_randomness": False,
            "measurements_supported": [
                "correctness",
                "controlled local implementation cost",
                "canonical serialized payload",
            ],
            "security_claim_not_supported_by_harness": "concrete unlinkability under process-separated path corruption",
        },
        "utility_provenance": {
            "status": "INPUTS_HASHED_ENVIRONMENT_RECONSTRUCTED",
            "utility_script": bind(utility_script),
            "dataset_files": dataset_files,
            "dataset_tree_sha256": canonical_digest(dataset_files),
            "application_seeds": [2024, 2025, 2026],
            "partition_reconstruction": {
                "algorithm": "numpy.default_rng(seed); independent train/test choice without replacement; client_splits uses rng.permutation then numpy.array_split",
                "train_population": 60000,
                "train_subset": 10000,
                "test_population": 10000,
                "test_subset": 2000,
                "clients": 10,
                "index_encoding": "little-endian signed int64, C order",
                "partitions": partitions,
            },
            "model_initialization_rule": "zero-initialized multinomial logistic-regression parameter vector",
            "environment_observed_during_reconciliation": {
                "python_version": platform.python_version(),
                "numpy_version": np.__version__,
                "sklearn_version_not_used_by_utility_script": sklearn_version,
            },
            "historical_runtime_environment_captured_in_raw_results": False,
            "limitation": "Dataset and script bytes are preserved, and deterministic partition indices are reconstructed. The exact historical Python/NumPy package snapshot was not recorded by the original utility run.",
        },
        "unit_erratum": {
            "legacy_label": "MB",
            "correct_label": "MiB",
            "conversion": "bytes / 2^20",
            "numeric_values_changed": False,
        },
        "builder": bind(Path(__file__).resolve()),
    }

    OUTPUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    output_sha = sha256(OUTPUT)
    OUTPUT.with_suffix(".sha256").write_text(
        f"{output_sha}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest": relative(OUTPUT),
                "sha256": output_sha,
                "dataset_files": len(dataset_files),
                "partition_reconstructions": len(partitions),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
