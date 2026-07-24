# IntCRT and PolySubR packing

The paper uses IntCRT and true idempotent PolySubR packing in the evaluated OpenFHE DCRTPoly profile. The current released runner keeps the packing implementation inside:

```text
src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp
```

The certificate helper scripts under `scripts/` check the parameter bounds, idempotent profile and round-trip properties used in the manuscript.
