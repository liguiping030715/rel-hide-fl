# Relation-Hiding Secure Aggregation for Federated Learning

This repository contains the experiment code for the paper:

**Relation-Hiding Secure Aggregation for Federated Learning: An RLWE-Based Material-Separation Paradigm**

The artifact implements and evaluates the APBR-SplitMix material-separation protocol described in the paper. The code is organized as a clean paper artifact rather than a full development snapshot: transient build outputs, raw logs, JSON manifests, and generated result files are intentionally excluded.

## Repository Layout

```text
rel-hide-fl/
├── src/
│   ├── crypto/      # RLWE/OpenFHE, BGV, Shamir proxy, randomness components
│   ├── protocol/    # Four-path APBR-SplitMix protocol integration
│   └── utils/       # Shared utility code placeholder
├── experiments/
│   └── configs/     # Experiment parameter files
├── data/            # Local datasets and cached tensors, ignored by Git
├── results/         # Generated experiment outputs, ignored by Git
├── figures/         # Plotting scripts and generated figures
├── docs/            # Artifact notes and additional documentation
├── scripts/         # Certificate, audit, and artifact helper scripts
├── .gitignore
├── README.md
└── requirements.txt
```

## Main Components

- `src/crypto/`: baseline and cryptographic building blocks, including OpenFHE BGV-only baseline, DCRTPoly smoke tests, Shamir-shuffle proxy baseline, and randomness self-tests.
- `src/protocol/`: full OpenFHE DCRTPoly split-path protocol integration.
- `experiments/`: Python experiment runners for utility, topology, distributed preflights, and sum-only baseline measurements.
- `scripts/`: supporting scripts for correctness certificates, provenance checks, and paper artifact generation.

## Security and Evaluation Scope

The implementation follows the paper's shared-\(a\) RLWE material construction:

```text
b_i = a * sk_i + t * e_i + iota(m_i)
```

The setup process samples and publishes one common public DCRTPoly \(a\). Each client samples its own secret and error material, splits key/body materials over the four paths, and the servers apply APBR-SplitMix before aggregate recovery.

The repository is intended to support reproducible local evaluation on a host machine with clients simulated by WSL processes or Docker containers. It does not claim WAN latency or production deployment measurements.

## Python Dependencies

Install the Python dependencies with:

```bash
pip install -r requirements.txt
```

The C++ OpenFHE components require an OpenFHE development installation compatible with the version used in the paper experiments.

## Result Files

Generated outputs should be written under `results/` and are ignored by Git by default. Paper figures should be regenerated from scripts rather than committed as raw experiment output unless a release artifact explicitly requires them.

## Notes

This repository intentionally excludes old development manifests and transient build artifacts. If a full provenance bundle is needed for archival review, generate it separately from the experiment scripts and place it outside the source tree or in a release asset.
