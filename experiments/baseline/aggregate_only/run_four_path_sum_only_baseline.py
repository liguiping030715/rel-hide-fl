#!/usr/bin/env python3
import csv
import json
import os
import subprocess
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
OUT_DIR = ROOT / "results/formal/baselines/four_path_sum_only_d784"
RAW_DIR = OUT_DIR / "raw"
BUILD_DIR = Path("/home/liguiping/openfhe_splitpath_build")
BINARY = BUILD_DIR / "openfhe_dcrtpoly_wire_integration"


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"/home/liguiping/openfhe-install/lib:{env.get('LD_LIBRARY_PATH', '')}"

    clients_list = [10, 20, 30, 40, 50]
    seeds = [2024, 2025, 2026]
    repetitions = range(1, 6)
    rows = []
    started = time.time()
    total = len(clients_list) * len(seeds) * len(list(repetitions))
    finished = 0

    for clients in clients_list:
        for seed in seeds:
            for rep in range(1, 6):
                case_id = f"four_path_sum_only_c{clients}_d784_s{seed}_r{rep:03d}"
                raw_path = RAW_DIR / f"{case_id}.raw.json"
                stderr_path = RAW_DIR / f"{case_id}.stderr.txt"
                sample_path = OUT_DIR / f"{case_id}.json"
                cmd = [
                    str(BINARY),
                    "--variant", "four_path_sum_only",
                    "--clients", str(clients),
                    "--dimension", "784",
                    "--ring-dim", "16384",
                    "--plaintext-modulus", "2199023288321",
                    "--noise", "dgg32",
                    "--seed", str(seed),
                    "--k", "2",
                    "--k0", "2",
                ]
                t0 = time.time()
                proc = subprocess.run(
                    cmd,
                    cwd=str(BUILD_DIR),
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                elapsed = time.time() - t0
                raw_path.write_text(proc.stdout.strip(), encoding="utf-8")
                stderr_path.write_text(proc.stderr, encoding="utf-8")
                status = "PASS"
                error = None
                raw = None
                try:
                    if proc.returncode != 0:
                        raise RuntimeError(f"binary exited with code {proc.returncode}")
                    raw = json.loads(proc.stdout)
                    if (
                        raw.get("status") != "PASS"
                        or int(raw.get("encoded_plaintext_diff_linf", -1)) != 0
                        or int(raw.get("encoded_plaintext_mismatch_count", -1)) != 0
                    ):
                        raise RuntimeError("required correctness checks failed")
                except Exception as exc:
                    status = "FAIL"
                    error = str(exc)

                runtime = raw.get("runtime_ms") if raw else {}
                client_upload = raw.get("client_upload_bytes") if raw else None
                path_to_cs = raw.get("path_to_cs_bytes") if raw else None
                total_payload = raw.get("total_payload_bytes") if raw else None
                sample = {
                    "schema": "controlled_baseline_sample_v1",
                    "formal": True,
                    "experiment": {
                        "type": "controlled_baseline",
                        "variant": "four_path_sum_only",
                        "case_id": case_id,
                    },
                    "parameters": {
                        "clients": clients,
                        "dimension": 784,
                        "ring_dim": 16384,
                        "plaintext_modulus": "2199023288321",
                        "seed": seed,
                        "repetition": rep,
                        "k": 2,
                        "k0": 2,
                        "noise": "dgg32",
                    },
                    "correctness": {
                        "encoded_plaintext_diff_linf": raw.get("encoded_plaintext_diff_linf") if raw else None,
                        "encoded_plaintext_mismatch_count": raw.get("encoded_plaintext_mismatch_count") if raw else None,
                        "q_domain_diff_linf": raw.get("q_domain_diff_linf") if raw else None,
                        "q_domain_mismatch_count": raw.get("q_domain_mismatch_count") if raw else None,
                        "status_pass": status == "PASS",
                    },
                    "runtime_ms": runtime if raw else None,
                    "communication": {
                        "client_upload_bytes": client_upload,
                        "path_to_cs_bytes": path_to_cs,
                        "total_bytes": total_payload,
                        "legacy_total_wire_bytes": raw.get("total_wire_bytes") if raw else None,
                        "scope": "four-path aggregate-only DCRTPoly key/body shares; one local aggregate record per path",
                    },
                    "material": {
                        "formula": raw.get("material_formula") if raw else None,
                        "public_a_sampler": raw.get("public_a_sampler") if raw else None,
                        "secret_sampler": raw.get("secret_sampler") if raw else None,
                        "error_sampler": raw.get("error_sampler") if raw else None,
                    },
                    "raw_result_file": rel(raw_path),
                    "stderr_file": rel(stderr_path),
                    "wrapper_elapsed_seconds": round(elapsed, 6),
                    "status": status,
                    "error_message": error,
                }
                write_json(sample_path, sample)
                rows.append({
                    "case_id": case_id,
                    "variant": "four_path_sum_only",
                    "clients": clients,
                    "dimension": 784,
                    "seed": seed,
                    "repetition": rep,
                    "status": status,
                    "encoded_plaintext_diff_linf": sample["correctness"]["encoded_plaintext_diff_linf"],
                    "encoded_plaintext_mismatch_count": sample["correctness"]["encoded_plaintext_mismatch_count"],
                    "q_domain_diff_linf": sample["correctness"]["q_domain_diff_linf"],
                    "q_domain_mismatch_count": sample["correctness"]["q_domain_mismatch_count"],
                    "total_ms": runtime.get("total") if raw else None,
                    "client_upload_bytes": client_upload,
                    "path_to_cs_bytes": path_to_cs,
                    "total_bytes": total_payload,
                    "legacy_total_wire_bytes": raw.get("total_wire_bytes") if raw else None,
                    "wrapper_elapsed_seconds": round(elapsed, 6),
                    "result_file": rel(sample_path),
                })
                finished += 1
                write_json(OUT_DIR / "progress.json", {
                    "schema": "controlled_baseline_progress_v1",
                    "total_cases": total,
                    "finished": finished,
                    "failed": sum(1 for r in rows if r["status"] != "PASS"),
                    "current": case_id,
                    "elapsed_hours": round((time.time() - started) / 3600.0, 6),
                    "formal": True,
                })
                print(f"{case_id} {status} elapsed={elapsed:.3f}s", flush=True)
                if status != "PASS":
                    raise RuntimeError(f"controlled baseline case failed: {case_id} ({error})")

    csv_path = OUT_DIR / "controlled_baselines_matrix.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    write_json(OUT_DIR / "summary.json", {
        "schema": "controlled_baseline_summary_v1",
        "variant": "four_path_sum_only",
        "status": "PASS",
        "total_cases": total,
        "passed": len(rows),
        "failed": 0,
        "csv": rel(csv_path),
        "elapsed_hours": round((time.time() - started) / 3600.0, 6),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
