# APBR-SplitMix OpenFHE Artifact

This repository contains the implementation and reproducibility artifact for
**Relation-Hiding Secure Aggregation for Federated Learning: An RLWE-Based
Material-Separation Paradigm**.

The prototype uses OpenFHE 1.2.3 low-level `DCRTPoly` arithmetic for the shared-
`a` RLWE construction and OpenFHE's native BGVRNS API for the controlled BGV-only
baseline. The evaluated protocol path is:

```text
Quantization -> IntCRT -> idempotent PolySubR packing
-> b_i = a * sk_i + t * e_i + iota(m_i)
-> two additive key/body shares
-> S1/S2/T1/T2 APBR-SplitMix processing
-> canonical wire serialization
-> aggregate-only central recovery and decoding
```

## Artifact Scope

The repository publishes:

- C++ protocol and baseline implementations in `src/`;
- PowerShell and Python experiment runners in `scripts/`;
- frozen protocol and wire specifications in `spec/`;
- experiment configurations in `configs/`;
- release, result, and reconciliation manifests in `manifests/`;
- correctness certificates in `results/certificates/v8_rc1/`;
- aggregate CSV tables and paper figures in
  `results/formal_v8/paper_artifacts/`;
- compact matrix summaries used to regenerate the reported tables and figures.

Build products, downloaded datasets, transient per-connection payloads, and
historical pre-v8 results are intentionally excluded. MNIST and CIFAR-10 are
downloaded from their public sources by the utility runner.

## Evaluated Profile

```yaml
OpenFHE: 1.2.3
ring_dimension_N: 16384
plaintext_modulus_t: 2199023288321
intcrt_moduli: [131071, 131101]
secret_sampler: OpenFHE centered ternary DCRTPoly::TugType
error_sampler: OpenFHE DCRTPoly::DggType(3.2)
error_support: [-39, 39]
public_a: one setup-bound DCRTPoly::DugType sample shared by all clients
k: 2
k0: 2
packing: polynomial-CRT idempotent PolySubR
security_claim_mode: conservative
exact_128_bit_pq_claim: false
```

The artifact does not claim a verified concrete 128-bit post-quantum estimate.
It supports the paper's conservative RLWE-based post-quantum design claim and
the machine-checked correctness bounds recorded in the certificate.

## Build

The recorded environment is Ubuntu/WSL with OpenFHE installed under
`/home/liguiping/openfhe-install`.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

The main targets are:

```text
openfhe_dcrtpoly_wire_integration
openfhe_dcrtpoly_material_smoke
route_a_v8_randomness_selftest
openfhe_bgv_only_baseline
shamir_shuffle_proxy_baseline
```

## Reproduction Order

Run the gates before collecting formal data:

```text
1. scripts/run_preflight.ps1
2. scripts/run_formal_sanity.ps1
3. scripts/run_formal_scalability_matrix.ps1
4. scripts/run_multiblock_scaling_matrix.ps1
5. scripts/run_controlled_baselines.ps1
6. scripts/run_ablation_matrix.ps1
7. scripts/run_fl_utility.py --route-mode protocol --formal
8. scripts/generate_evaluation_artifacts_v8.py
```

Runner options and exact matrices are frozen in
`manifests/formal_evaluation_release_v8_final.json`.

## Provenance

The final evidence chain is:

```text
formal_evaluation_release_v8_final.json
  -> formal_evaluation_results_v8_final.json
  -> formal_evaluation_v8_reconciliation.json
  -> results/formal_v8/paper_artifacts/
```

Frozen SHA-256 values:

```text
release manifest:        4dc35423dac9696359bdc13f909d94f47f1af76f6c71dec9ace0fa2aa60ade1c
results manifest:        8d7c292b4e8faccb6afb23b77668d7f9ba57b5cf7565ad8d020bcb9038dc4704
reconciliation artifact: b63b40e3948fb730b860f3fd2b82630e6d9abe5517c338f3f616b454cdad7766
```

The reconciliation artifact records explicit limitations, including the
single-process formal performance harness, the OpenFHE native-BGV baseline's
different supported plaintext modulus, and the reconstructed utility software
environment.

## Communication Metric

Reported payload is sender-side application-level protocol payload. Transport
headers, control-plane messages, logging, receiver-side audit counters, and
operating-system overhead are excluded. Logical real/dummy decompositions are
not added a second time.

## Deployment Model

The topology preflight uses Docker clients and WSL-hosted S1/S2/T1/T2 and CS
roles. Formal performance numbers are controlled local implementation costs;
they are not WAN latency measurements. The security model assumes reliable
point-to-point communication and excludes active network attacks and traffic
analysis.
