# APBR-SplitMix implementation

This directory contains the evaluated split-path implementation.

`openfhe_dcrtpoly_wire_integration.cpp` includes:

- shared-`a` RLWE body construction;
- key-side and ciphertext-body material separation;
- additive sharing over `S1/S2/T1/T2`;
- APBR zero-sum refresh;
- `k`-fragment splitting;
- zero-sum dummy padding;
- unbiased Fisher-Yates permutation;
- canonical wire serialization/deserialization;
- central aggregate-only recovery and encoded plaintext checking.

The implementation is intentionally not split into several independent protocol implementations, because the paper artifact needs one auditable path from protocol logic to formal evaluation.
