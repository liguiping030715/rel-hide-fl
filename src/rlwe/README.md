# RLWE and OpenFHE material layer

This directory contains the OpenFHE DCRTPoly material checks and randomness utilities used by the evaluated implementation.

Key paper mapping:

- shared public DCRTPoly `a`;
- client-local secret and error material;
- OpenFHE DCRTPoly arithmetic used by the split-path runner;
- process/path randomness self-tests.

The full split-path APBR-SplitMix runner lives in `src/apbr_splitmix/`.
