# Material-separation framework mapping

This directory documents the mapping from the paper's abstract material-separation framework to the implementation.

| Framework interface | Implementation location |
|---|---|
| `Materialize` | `src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp` |
| `Share` | `src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp` |
| `PathProcess` | `src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp` |
| `Recover` | `src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp` |

The implementation is kept as one auditable C++ runner to avoid two diverging protocol implementations.
