# Protocol entities

The paper roles are:

- clients;
- key-share shuffle servers `S1` and `S2`;
- ciphertext-body shuffle servers `T1` and `T2`;
- central server `CS`.

The role logic is implemented inside the integrated runner:

```text
src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp
```

Distributed and Docker/WSL preflight scripts in `experiments/correctness/` validate the role topology without claiming WAN latency.
