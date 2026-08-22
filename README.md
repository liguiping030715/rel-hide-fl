# Cross-Material Unlinkability in Secure Aggregation for Federated Learning: An RLWE-Based Material-Separation Paradigm

This repository contains the artifact code for the paper:

**Cross-Material Unlinkability in Secure Aggregation for Federated Learning: An RLWE-Based Material-Separation Paradigm**

The artifact is organized around the paper rather than as a general-purpose software library. It exposes the three layers that the paper relies on:

- the finite-group APBR-SplitMix compiler;
- the RLWE/OpenFHE DCRTPoly instantiation;
- the paper evaluation and reproduction scripts for correctness, scalability, baselines, ablation and FL trajectory preservation.

## Quick Start

Run the repository-level artifact smoke test first. This check does not require OpenFHE; it verifies that the paper-to-code map, scripts, expected-output samples and repository hygiene are intact.

```bash
python scripts/run_artifact_smoke_test.py
```

Expected output:

```text
[PASS] required artifact paths
[PASS] Python script syntax
[PASS] no forbidden generated outputs
[PASS] expected output samples
[PASS] finite-group APBR-SplitMix invariants
[PASS] paper-to-code map targets
ARTIFACT_SMOKE_TEST=PASS
```

The full protocol runners require a WSL/OpenFHE build compatible with the paper environment. See `ARTIFACT.md` for supported claims, expected runtime and full reproduction commands.

The same smoke test is also run by the GitHub Actions workflow in `.github/workflows/artifact-smoke.yml`.

## Repository Layout

```text
rel-hide-fl/
├── ARTIFACT.md               # Artifact claims, workflow and expected outputs
├── paper/                    # Paper-facing notes and parameter summaries
├── src/
│   ├── rlwe/                 # OpenFHE DCRTPoly material, randomness and RLWE checks
│   ├── rlwe/packing/         # IntCRT/PolySubR documentation and code pointers
│   ├── material_separation/  # Materialization/share/recovery mapping notes
│   ├── apbr_splitmix/        # Full APBR-SplitMix split-path implementation
│   ├── entities/             # Client/path/central-server role mapping notes
│   └── utils/                # Shared utility notes
├── experiments/
│   ├── correctness/          # Distributed TCP and Docker/WSL preflight runners
│   ├── performance/          # Scalability/topology runners
│   ├── baseline/             # Plain, BGV-only, Shamir proxy and aggregate-only baselines
│   ├── ablation/             # Component ablation configurations and notes
│   ├── utility/              # MNIST/CIFAR trajectory-preservation runner
│   └── configs/              # YAML experiment parameters
├── scripts/                  # Release manifests, certificates and matrix launchers
├── figures/                  # Scripts that regenerate paper figures and tables
├── data/                     # Local datasets and caches; ignored by Git
├── results/
│   ├── expected/             # Small expected summaries for sanity checking
│   └── sample/               # Small smoke-test output examples
└── docker/                   # Containerized artifact-smoke-test files
```

## Paper-to-Code Map

| Paper component | Section / role | Artifact location |
|---|---|---|
| Material-separation framework | Section 3 | `src/material_separation/README.md` |
| Finite-group APBR-SplitMix invariant test | Section 5 compiler | `experiments/correctness/test_finite_group_compiler.py` |
| RLWE DCRTPoly material generation and randomness checks | Section 4 / Section 6 profile | `src/rlwe/` |
| IntCRT and PolySubR profile checks | Section 4 / correctness certificate | `src/rlwe/packing/`, `scripts/build_polysubr_idempotent_profile.py` |
| APBR refresh, fragmentation, dummy padding, permutation and aggregate recovery | APBR-SplitMix construction | `src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp` |
| Client / S1 / S2 / T1 / T2 / CS role mapping | System model | `src/entities/README.md` and `src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp` |
| OpenFHE BGV-only reference | Evaluation baseline | `experiments/baseline/bgv_only/` |
| Shamir-shuffle proxy reference | Evaluation baseline | `experiments/baseline/shamir_shuffle_proxy/` |
| Four-path aggregate-only baseline | Evaluation baseline | `experiments/baseline/aggregate_only/` |
| FL trajectory preservation | Evaluation utility sanity check | `experiments/utility/run_fl_utility.py` |
| Figure and table generation | Evaluation artifacts | `figures/plot_evaluation_figures_v8.py` |

The current C++ protocol implementation is intentionally kept as one auditable runner rather than split into several independently evolving protocol implementations. The module directories document which paper component each part of the runner realizes.

## Reproduce Paper Results

Generated outputs are written under `results/` and are ignored by Git by default, except for small expected-output samples.

```powershell
# Correctness / topology preflights
python experiments/correctness/run_v8_distributed_tcp_preflight.py
python experiments/correctness/run_v8_docker_wsl_topology_preflight.py

# Formal scalability matrix
powershell -ExecutionPolicy Bypass -File scripts/run_formal_scalability_matrix.ps1

# Multi-block dimension scaling
powershell -ExecutionPolicy Bypass -File scripts/run_multiblock_scaling_matrix.ps1

# Controlled baselines
powershell -ExecutionPolicy Bypass -File scripts/run_controlled_baselines.ps1

# Ablations
powershell -ExecutionPolicy Bypass -File scripts/run_ablation_matrix.ps1

# FL utility / trajectory preservation
python experiments/utility/run_fl_utility.py --route-mode protocol

# Regenerate paper tables and figures from result CSV files
python figures/plot_evaluation_figures_v8.py
```

## Docker Smoke Test

The container smoke test checks repository integrity and Python syntax. It does not build OpenFHE or run the full protocol matrix.

```bash
docker build -f docker/Dockerfile -t rel-hide-fl-smoke .
docker run --rm rel-hide-fl-smoke
```

## Protocol Scope

The implementation follows the paper's shared-\(\boldsymbol a\) RLWE instantiation:

```text
b_i = a * sk_i + t * e_i + iota(m_i)
```

Setup samples one public DCRTPoly \(\boldsymbol a\) and binds it to the run profile. Each client samples its own secret and error material, separates key-side and ciphertext-body material, sends additive shares to \(S_1,S_2,T_1,T_2\), and the path servers execute APBR-SplitMix before central aggregate recovery.

Only additive homomorphic aggregation is used. This artifact does not claim WAN latency, production deployment measurements, malicious security, dropout recovery, network anonymity or concrete 128-bit post-quantum security bits.

## Dependencies

Python dependencies:

```bash
pip install -r requirements.txt
```

C++ components require OpenFHE development headers/libraries compatible with the version used in the paper experiments. The Docker/WSL topology preflights assume a Windows host with WSL2 and optional Docker clients.

## Results and Data Policy

Large generated outputs, raw logs, datasets, binary builds and JSON manifests are excluded from the source repository. Recreate them with the scripts above, or attach frozen provenance bundles as release assets when needed for artifact review.
