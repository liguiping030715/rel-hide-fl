# Artifact Evaluation Guide

This artifact supports the evaluation claims of:

**Relation-Hiding Secure Aggregation for Federated Learning: An RLWE-Based Material-Separation Paradigm**

## Supported Claims

The artifact supports the following paper claims:

- implementation of the OpenFHE DCRTPoly APBR-SplitMix split-path prototype;
- exact encoded aggregate recovery under the evaluated parameter profile;
- controlled local runtime and serialized application-payload measurements;
- comparison against in-memory plain aggregation, native OpenFHE BGV, Shamir-shuffle proxy, four-path aggregate-only and synthetic shuffle references;
- component ablations for `k = 1`, without APBR and without dummy padding;
- MNIST/CIFAR-10 quantized-training trajectory preservation sanity checks.

## Claims Not Supported by Experiments

The artifact does not empirically prove:

- RLWE hardness;
- concrete 128-bit post-quantum security;
- malicious security;
- dropout recovery;
- WAN or production deployment latency;
- network anonymity, participation privacy or cross-round unlinkability.

These are either theoretical assumptions, explicitly scoped limitations or outside the evaluation target.

## Hardware and Software

The paper experiments were run on a Windows 11 host with WSL2 Ubuntu, OpenFHE 1.2.3, g++ 15.2.0 and Python tooling for aggregation and plotting scripts. Docker clients were used for topology/preflight validation; formal runtime matrices are controlled single-process WSL measurements.

## Quick Smoke Test

This test is intended for a first artifact check and does not require OpenFHE:

```bash
python scripts/run_artifact_smoke_test.py
```

Expected terminal output:

```text
[PASS] required artifact paths
[PASS] Python script syntax
[PASS] no forbidden generated outputs
[PASS] expected output samples
[PASS] paper-to-code map targets
ARTIFACT_SMOKE_TEST=PASS
```

Docker equivalent:

```bash
docker build -f docker/Dockerfile -t rel-hide-fl-smoke .
docker run --rm rel-hide-fl-smoke
```

## Full Reproduction Workflow

The full workflow requires OpenFHE and the WSL build environment described in the paper.

```powershell
python experiments/correctness/run_v8_distributed_tcp_preflight.py
python experiments/correctness/run_v8_docker_wsl_topology_preflight.py

powershell -ExecutionPolicy Bypass -File scripts/run_formal_scalability_matrix.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_multiblock_scaling_matrix.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_controlled_baselines.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_ablation_matrix.ps1

python experiments/utility/run_fl_utility.py --route-mode protocol
python figures/plot_evaluation_figures_v8.py
```

## Expected Outputs

Small expected-output samples are stored in:

```text
results/expected/
results/sample/
```

They are not raw formal logs. They are lightweight sanity references for artifact review. Raw matrices, release manifests and large provenance bundles should be regenerated locally or attached as release assets.

## Runtime Notes

The smoke test should finish in seconds. Full formal matrices may take hours depending on the OpenFHE build, WSL/Docker configuration and host CPU. Formal runtime samples are local implementation-cost measurements, not concurrent WAN round latency.

## Randomness and Nondeterminism

Application seeds control workload generation, client partitioning and model initialization. Protocol repetitions use fresh cryptographic randomness. Therefore exact runtime values may vary across machines, but correctness summaries should retain zero encoded plaintext mismatch and zero protocol aborts for the evaluated profile.

## Anonymity Note

For double-blind submission, use an anonymized mirror or supplementary artifact package rather than a personal GitHub URL.
