# Docker / WSL deployment

The paper validates a local Docker-client to WSL-server topology for protocol correctness and role isolation. Formal runtime numbers are controlled single-process WSL measurements and should not be interpreted as WAN or production deployment latency.

The included `Dockerfile` runs the repository-level artifact smoke test. It is intentionally lightweight and does not build OpenFHE or execute the full formal matrices.
