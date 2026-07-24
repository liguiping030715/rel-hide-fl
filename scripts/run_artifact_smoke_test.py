from __future__ import annotations

import py_compile
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = [
    "README.md",
    "ARTIFACT.md",
    "requirements.txt",
    "environment.yml",
    "src/rlwe/v8_randomness.h",
    "src/rlwe/openfhe_dcrtpoly_material_smoke.cpp",
    "src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp",
    "src/material_separation/README.md",
    "src/entities/README.md",
    "experiments/configs/default.yaml",
    "experiments/baseline/bgv_only/openfhe_bgv_only_baseline.cpp",
    "experiments/baseline/shamir_shuffle_proxy/shamir_shuffle_proxy_baseline.cpp",
    "experiments/baseline/aggregate_only/run_four_path_sum_only_baseline.py",
    "experiments/utility/run_fl_utility.py",
    "figures/plot_evaluation_figures_v8.py",
    "results/expected/correctness_summary.csv",
    "results/expected/baseline_c30_summary.csv",
    "results/sample/artifact_smoke_output.txt",
]

FORBIDDEN_EXTENSIONS = {".json", ".log", ".pkl", ".npz", ".exe", ".dll", ".obj", ".o"}


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def check_required_paths() -> None:
    missing = [p for p in REQUIRED_PATHS if not (ROOT / p).exists()]
    if missing:
        fail("missing required paths: " + ", ".join(missing))
    print("[PASS] required artifact paths")


def check_python_syntax() -> None:
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        py_compile.compile(str(path), doraise=True)
    print("[PASS] Python script syntax")


def check_forbidden_outputs() -> None:
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("results/expected/") or rel.startswith("results/sample/"):
            continue
        if path.suffix.lower() in FORBIDDEN_EXTENSIONS:
            offenders.append(rel)
    if offenders:
        fail("forbidden generated outputs present: " + ", ".join(offenders[:20]))
    print("[PASS] no forbidden generated outputs")


def check_expected_outputs() -> None:
    correctness = (ROOT / "results/expected/correctness_summary.csv").read_text(encoding="utf-8")
    baseline = (ROOT / "results/expected/baseline_c30_summary.csv").read_text(encoding="utf-8")
    sample = (ROOT / "results/sample/artifact_smoke_output.txt").read_text(encoding="utf-8")
    required_tokens = [
        "encoded_plaintext_mismatches",
        "Full APBR-SplitMix",
        "ARTIFACT_SMOKE_TEST=PASS",
    ]
    combined = "\n".join([correctness, baseline, sample])
    missing = [token for token in required_tokens if token not in combined]
    if missing:
        fail("expected output samples missing tokens: " + ", ".join(missing))
    print("[PASS] expected output samples")


def check_paper_to_code_map_targets() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = [
        "src/material_separation/README.md",
        "src/rlwe/",
        "src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp",
        "experiments/baseline/bgv_only/",
        "experiments/baseline/aggregate_only/",
        "figures/plot_evaluation_figures_v8.py",
    ]
    missing = [target for target in targets if target not in readme]
    if missing:
        fail("README paper-to-code map missing targets: " + ", ".join(missing))
    print("[PASS] paper-to-code map targets")


def main() -> int:
    check_required_paths()
    check_python_syntax()
    check_forbidden_outputs()
    check_expected_outputs()
    check_paper_to_code_map_targets()
    print("ARTIFACT_SMOKE_TEST=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
