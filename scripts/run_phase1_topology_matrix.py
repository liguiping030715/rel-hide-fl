#!/usr/bin/env python3
"""Run a host-client split-path topology correctness matrix.

This matrix exercises the TCP split-path/APBR/fragment/dummy/permutation/recovery
runner. It is still marked non-formal until OpenFHE material generation is wired
into the same role boundary.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path


def parse_int_list(value: str) -> list[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", default="5,10")
    parser.add_argument("--dimensions", default="784,3072")
    parser.add_argument("--seeds", default="2024")
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--k0", type=int, default=2)
    parser.add_argument("--base-port", type=int, default=43000)
    parser.add_argument("--out-dir", default="results/preflight/phase1_topology_pilot")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    case_script = root / "scripts" / "host_client_preflight.py"
    out_dir = root / args.out_dir
    cases_dir = out_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    clients = parse_int_list(args.clients)
    dimensions = parse_int_list(args.dimensions)
    seeds = parse_int_list(args.seeds)

    rows: list[dict] = []
    case_index = 0
    started = time.time()

    for c in clients:
        for d in dimensions:
            for seed in seeds:
                case_id = f"c{c}_d{d}_s{seed}"
                out = cases_dir / f"{case_id}.json"
                port = args.base_port + case_index * 10
                cmd = [
                    args.python,
                    str(case_script),
                    "--clients", str(c),
                    "--dimension", str(d),
                    "--k", str(args.k),
                    "--k0", str(args.k0),
                    "--seed", str(seed),
                    "--base-port", str(port),
                    "--out", str(out),
                ]
                t0 = time.time()
                proc = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True)
                elapsed = time.time() - t0
                if out.exists():
                    data = json.loads(out.read_text(encoding="utf-8"))
                else:
                    data = {
                        "status": "FAIL",
                        "correctness": {"max_abs_error_quantized": None, "mismatch_count": None},
                        "transport": {
                            "client_to_path_conservation": False,
                            "path_to_cs_conservation": False,
                            "total_protocol_bytes": None,
                        },
                    }
                row = {
                    "case_id": case_id,
                    "clients": c,
                    "dimension": d,
                    "seed": seed,
                    "k": args.k,
                    "k0": args.k0,
                    "status": data.get("status"),
                    "max_abs_error_quantized": data.get("correctness", {}).get("max_abs_error_quantized"),
                    "mismatch_count": data.get("correctness", {}).get("mismatch_count"),
                    "client_to_path_conservation": data.get("transport", {}).get("client_to_path_conservation"),
                    "path_to_cs_conservation": data.get("transport", {}).get("path_to_cs_conservation"),
                    "total_protocol_bytes": data.get("transport", {}).get("total_protocol_bytes"),
                    "elapsed_seconds": round(elapsed, 6),
                    "result_file": str(out.relative_to(root)),
                    "stdout": proc.stdout.strip(),
                    "stderr": proc.stderr.strip(),
                }
                rows.append(row)
                print(f"{case_id}: {row['status']} elapsed={elapsed:.3f}s")
                case_index += 1
                if proc.returncode != 0:
                    print(proc.stdout)
                    print(proc.stderr, file=sys.stderr)

    csv_path = out_dir / "phase1_topology_matrix.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    all_pass = all(
        r["status"] == "PASS"
        and int(r["mismatch_count"]) == 0
        and str(r["client_to_path_conservation"]).lower() == "true"
        and str(r["path_to_cs_conservation"]).lower() == "true"
        for r in rows
    )
    summary = {
        "schema": "phase1_topology_matrix_summary_v1",
        "formal": False,
        "openfhe_integrated": False,
        "status": "PASS" if all_pass else "FAIL",
        "scope": "splitpath_topology_correctness_matrix_only",
        "clients": clients,
        "dimensions": dimensions,
        "seeds": seeds,
        "case_count": len(rows),
        "passed": sum(1 for r in rows if r["status"] == "PASS"),
        "failed": sum(1 for r in rows if r["status"] != "PASS"),
        "elapsed_seconds": round(time.time() - started, 6),
        "csv": str(csv_path.relative_to(root)),
        "note": "Not a formal OpenFHE split-path result until OpenFHE material generation is integrated into this runner."
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    if not all_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
