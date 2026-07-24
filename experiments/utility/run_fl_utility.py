import argparse
import csv
import gzip
import hashlib
import json
import pickle
import shlex
import struct
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_idx_images(path: Path) -> np.ndarray:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        magic, n, rows, cols = struct.unpack(">IIII", f.read(16))
        if magic != 2051:
            raise ValueError(f"bad image magic: {magic}")
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return data.reshape(n, rows * cols).astype(np.float32) / 255.0


def read_idx_labels(path: Path) -> np.ndarray:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        magic, n = struct.unpack(">II", f.read(8))
        if magic != 2049:
            raise ValueError(f"bad label magic: {magic}")
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return data.astype(np.int64)


def load_mnist(data_root: Path):
    raw = data_root / "MNIST" / "raw"
    x_train = read_idx_images(raw / "train-images-idx3-ubyte")
    y_train = read_idx_labels(raw / "train-labels-idx1-ubyte")
    x_test = read_idx_images(raw / "t10k-images-idx3-ubyte")
    y_test = read_idx_labels(raw / "t10k-labels-idx1-ubyte")
    return x_train, y_train, x_test, y_test


def load_cifar10(data_root: Path):
    root = data_root / "cifar-10-batches-py"
    xs, ys = [], []
    for i in range(1, 6):
        with open(root / f"data_batch_{i}", "rb") as f:
            batch = pickle.load(f, encoding="latin1")
        xs.append(batch["data"].astype(np.float32) / 255.0)
        ys.extend(batch["labels"])
    with open(root / "test_batch", "rb") as f:
        test = pickle.load(f, encoding="latin1")
    return np.vstack(xs), np.array(ys, dtype=np.int64), test["data"].astype(np.float32) / 255.0, np.array(test["labels"], dtype=np.int64)


def one_hot(y, classes=10):
    out = np.zeros((len(y), classes), dtype=np.float32)
    out[np.arange(len(y)), y] = 1.0
    return out


def softmax_logits(x, w, b):
    z = x @ w + b
    z -= z.max(axis=1, keepdims=True)
    ez = np.exp(z)
    return ez / ez.sum(axis=1, keepdims=True)


def loss_acc(x, y, w, b):
    p = softmax_logits(x, w, b)
    loss = -np.log(np.maximum(p[np.arange(len(y)), y], 1e-12)).mean()
    acc = (p.argmax(axis=1) == y).mean()
    return float(loss), float(acc)


def gradient(x, y, w, b):
    p = softmax_logits(x, w, b)
    p -= one_hot(y, w.shape[1])
    p /= len(y)
    gw = x.T @ p
    gb = p.sum(axis=0)
    return np.concatenate([gw.reshape(-1), gb]).astype(np.float64)


def apply_grad(theta, grad, lr, d, classes=10):
    theta -= lr * grad
    w = theta[: d * classes].reshape(d, classes)
    b = theta[d * classes :]
    return w, b


def client_splits(n, clients, rng):
    idx = rng.permutation(n)
    return np.array_split(idx, clients)


def route_a_numpy_aggregate(int_grads, ring_dim):
    summed = np.sum(np.stack(int_grads, axis=0), axis=0, dtype=np.int64)
    profile_capacity = 2 * ring_dim
    blocks = int(np.ceil(len(summed) / profile_capacity))
    recovered = np.zeros_like(summed)
    block_mismatches = 0
    for block in range(blocks):
        start = block * profile_capacity
        end = min(start + profile_capacity, len(summed))
        padded = np.zeros(profile_capacity, dtype=np.int64)
        padded[: end - start] = summed[start:end]
        recovered[start:end] = padded[: end - start]
        if not np.array_equal(recovered[start:end], summed[start:end]):
            block_mismatches += 1
    return recovered, blocks, block_mismatches


def extract_json_object(text: str):
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("Route A binary did not emit a JSON object")
    return json.loads(text[start : end + 1])


def windows_path_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive[:1].lower()
    if not drive:
        raise RuntimeError(f"cannot convert path without drive to WSL path: {resolved}")
    rest = str(resolved)[2:].replace("\\", "/")
    return f"/mnt/{drive}{rest}"


def run_wsl_bash_script(cmd: str, distro: str):
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8", newline="\n") as f:
        script_path = Path(f.name)
        f.write("#!/usr/bin/env bash\nset -e\n")
        f.write(cmd)
        f.write("\n")
    try:
        proc = subprocess.run(
            ["wsl", "-d", distro, "bash", windows_path_to_wsl(script_path)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    finally:
        script_path.unlink(missing_ok=True)
    return proc


def route_a_protocol_aggregate(int_grads, ring_dim, args, case_dir: Path, dataset_name: str, rnd: int):
    summed = np.sum(np.stack(int_grads, axis=0), axis=0, dtype=np.int64)
    profile_capacity = 2 * ring_dim
    blocks = int(np.ceil(len(summed) / profile_capacity))
    recovered = np.zeros_like(summed)
    block_mismatches = 0
    route_logs = []
    inputs_dir = case_dir / "route_a_inputs"
    raw_dir = case_dir / "route_a_raw"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    for block in range(blocks):
        start = block * profile_capacity
        end = min(start + profile_capacity, len(summed))
        block_dim = end - start
        input_path = inputs_dir / f"{dataset_name}_round{rnd:03d}_block{block:02d}.csv"
        lines = []
        for grad in int_grads:
            row = grad[start:end].astype(np.int64).tolist()
            lines.append(",".join(str(int(x)) for x in row))
        csv_text = "\n".join(lines) + "\n"
        input_path.write_text(csv_text, encoding="utf-8")
        input_wsl = windows_path_to_wsl(input_path)
        seed = int(args.seed + 100000 * rnd + block)
        cmd = (
            f"cd {shlex.quote(args.binary_dir)} && "
            "LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:$LD_LIBRARY_PATH "
            "./openfhe_dcrtpoly_wire_integration "
            f"--clients {args.clients} --dimension {block_dim} --ring-dim {ring_dim} "
            f"--plaintext-modulus {args.plaintext_modulus} --noise {args.route_noise} "
            f"--seed {seed} --k 2 --k0 2 --messages-file {shlex.quote(input_wsl)} --emit-recovered true"
        )
        proc = run_wsl_bash_script(cmd, args.wsl_distro)
        raw_path = raw_dir / f"{dataset_name}_round{rnd:03d}_block{block:02d}.raw.json"
        stderr_path = raw_dir / f"{dataset_name}_round{rnd:03d}_block{block:02d}.stderr.txt"
        raw_path.write_text(proc.stdout, encoding="utf-8")
        stderr_path.write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"Route A binary failed for round {rnd} block {block}: {proc.stderr[-500:]}")
        raw = extract_json_object(proc.stdout)
        if raw.get("status") != "PASS":
            raise RuntimeError(f"Route A binary returned FAIL for round {rnd} block {block}")
        block_recovered = np.array(raw["recovered_plaintext"], dtype=np.int64)
        if len(block_recovered) != block_dim:
            raise RuntimeError("Route A recovered vector length mismatch")
        recovered[start:end] = block_recovered
        if not np.array_equal(block_recovered, summed[start:end]):
            block_mismatches += 1
        route_logs.append({
            "round": rnd,
            "block": block,
            "dimension": block_dim,
            "raw_result_file": str(raw_path),
            "stderr_file": str(stderr_path),
            "encoded_plaintext_diff_linf": raw.get("encoded_plaintext_diff_linf"),
            "encoded_plaintext_mismatch_count": raw.get("encoded_plaintext_mismatch_count"),
            "total_wire_bytes": raw.get("total_wire_bytes"),
            "runtime_ms": raw.get("runtime_ms", {}).get("total"),
        })

        if not args.keep_route_inputs:
            for path in inputs_dir.glob("*.csv"):
                path.unlink()
    return recovered, blocks, block_mismatches, route_logs


def run_dataset(args, dataset_name, x_train, y_train, x_test, y_test, out_dir: Path):
    rng = np.random.default_rng(args.seed)
    train_idx = rng.choice(len(x_train), size=min(args.train_subset, len(x_train)), replace=False)
    test_idx = rng.choice(len(x_test), size=min(args.test_subset, len(x_test)), replace=False)
    x_train = x_train[train_idx].astype(np.float32)
    y_train = y_train[train_idx]
    x_test = x_test[test_idx].astype(np.float32)
    y_test = y_test[test_idx]

    d, classes = x_train.shape[1], 10
    splits = client_splits(len(x_train), args.clients, rng)
    theta_fp = np.zeros(d * classes + classes, dtype=np.float64)
    theta_q = np.zeros_like(theta_fp)
    theta_route = np.zeros_like(theta_fp)
    rows = []
    route_invocations = []

    max_route_quantized_diff = 0
    total_route_block_mismatches = 0
    max_q_vs_fp_linf = 0.0
    route_quantized_equal_all_rounds = True

    for rnd in range(1, args.rounds + 1):
        w_fp = theta_fp[: d * classes].reshape(d, classes)
        b_fp = theta_fp[d * classes :]
        fp_grads = [gradient(x_train[idx], y_train[idx], w_fp, b_fp) for idx in splits]
        fp_avg = np.mean(np.stack(fp_grads, axis=0), axis=0)
        w_fp, b_fp = apply_grad(theta_fp, fp_avg, args.lr, d, classes)

        w_q = theta_q[: d * classes].reshape(d, classes)
        b_q = theta_q[d * classes :]
        q_float_grads = [gradient(x_train[idx], y_train[idx], w_q, b_q) for idx in splits]
        int_grads = [np.rint(g * args.quant_scale).astype(np.int64) for g in q_float_grads]
        q_sum = np.sum(np.stack(int_grads, axis=0), axis=0, dtype=np.int64)
        q_avg = q_sum.astype(np.float64) / (args.quant_scale * args.clients)
        w_q, b_q = apply_grad(theta_q, q_avg, args.lr, d, classes)

        case_dir = out_dir / f"{dataset_name}_seed{args.seed}"
        if args.route_mode == "numpy":
            recovered, blocks, block_mismatches = route_a_numpy_aggregate(int_grads, args.ring_dim)
            route_logs = []
        else:
            recovered, blocks, block_mismatches, route_logs = route_a_protocol_aggregate(
                int_grads, args.ring_dim, args, case_dir, dataset_name, rnd
            )
        route_invocations.extend(route_logs)
        route_quantized_diff = int(np.max(np.abs(recovered - q_sum))) if len(q_sum) else 0
        max_route_quantized_diff = max(max_route_quantized_diff, route_quantized_diff)
        total_route_block_mismatches += block_mismatches
        if route_quantized_diff != 0 or block_mismatches != 0:
            route_quantized_equal_all_rounds = False
        route_avg = recovered.astype(np.float64) / (args.quant_scale * args.clients)
        w_route, b_route = apply_grad(theta_route, route_avg, args.lr, d, classes)

        fp_loss, fp_acc = loss_acc(x_test, y_test, w_fp, b_fp)
        q_loss, q_acc = loss_acc(x_test, y_test, w_q, b_q)
        route_loss, route_acc = loss_acc(x_test, y_test, w_route, b_route)
        theta_q_route_diff = float(np.max(np.abs(theta_q - theta_route)))
        max_q_vs_fp_linf = max(max_q_vs_fp_linf, float(np.max(np.abs(theta_q - theta_fp))))

        rows.append({
            "dataset": dataset_name,
            "seed": args.seed,
            "round": rnd,
            "clients": args.clients,
            "train_subset": len(x_train),
            "test_subset": len(x_test),
            "gradient_dimension": len(theta_fp),
            "route_a_blocks": blocks,
            "route_quantized_diff_linf": route_quantized_diff,
            "route_block_mismatches": block_mismatches,
            "theta_quantized_route_linf": theta_q_route_diff,
            "plain_loss": fp_loss,
            "plain_acc": fp_acc,
            "quantized_loss": q_loss,
            "quantized_acc": q_acc,
            "route_loss": route_loss,
            "route_acc": route_acc,
        })

    return {
        "dataset": dataset_name,
        "seed": args.seed,
        "clients": args.clients,
        "rounds": args.rounds,
        "train_subset": len(x_train),
        "test_subset": len(x_test),
        "input_dimension": d,
        "gradient_dimension": d * classes + classes,
        "ring_dim": args.ring_dim,
        "profile_capacity": 2 * args.ring_dim,
        "route_a_blocks": int(np.ceil((d * classes + classes) / (2 * args.ring_dim))),
        "quant_scale": args.quant_scale,
        "lr": args.lr,
        "status": "PASS" if route_quantized_equal_all_rounds else "FAIL",
        "route_mode": args.route_mode,
        "route_a_binary": "openfhe_dcrtpoly_wire_integration" if args.route_mode == "protocol" else None,
        "route_a_invocation_count": len(route_invocations),
        "max_route_quantized_diff_linf": max_route_quantized_diff,
        "total_route_block_mismatches": total_route_block_mismatches,
        "max_theta_quantized_route_linf": max(float(r["theta_quantized_route_linf"]) for r in rows),
        "max_theta_quantized_plain_linf": max_q_vs_fp_linf,
        "final": rows[-1],
        "rows": rows,
        "route_invocations": route_invocations,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["mnist", "cifar10", "both"], default="both")
    ap.add_argument("--data-root", default="sn-article-template/data")
    ap.add_argument("--out-dir", default="sn-article-template/experiments/openfhe_splitpath_host_clients/results/formal/fl_utility")
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train-subset", type=int, default=10000)
    ap.add_argument("--test-subset", type=int, default=2000)
    ap.add_argument("--quant-scale", type=float, default=1_000.0)
    ap.add_argument("--lr", type=float, default=0.5)
    ap.add_argument("--ring-dim", type=int, default=16384)
    ap.add_argument("--route-mode", choices=["protocol", "numpy"], default="protocol")
    ap.add_argument("--route-noise", default="dgg32")
    ap.add_argument("--plaintext-modulus", default="2199023288321")
    ap.add_argument("--binary-dir", default="/home/liguiping/openfhe_splitpath_build_v8_rc1")
    ap.add_argument("--wsl-distro", default="Ubuntu")
    ap.add_argument("--release-manifest")
    ap.add_argument("--keep-route-inputs", action="store_true")
    ap.add_argument("--formal", action="store_true")
    args = ap.parse_args()

    release_binding = None
    if args.formal:
        if args.route_mode != "protocol":
            raise RuntimeError("formal utility requires --route-mode protocol")
        if not args.release_manifest:
            raise RuntimeError("formal utility requires --release-manifest")
        release_path = Path(args.release_manifest)
        release = json.loads(release_path.read_text(encoding="utf-8"))
        if release.get("release", {}).get("formal_evaluation_authorized") is not True:
            raise RuntimeError("release manifest does not authorize formal evaluation")
        expected_dir = release.get("implementation", {}).get("wsl_binary_dir")
        if expected_dir != args.binary_dir:
            raise RuntimeError("utility binary directory does not match release manifest")
        deployed_hash = subprocess.check_output(
            [
                "wsl", "-d", args.wsl_distro, "--", "sha256sum",
                f"{args.binary_dir}/openfhe_dcrtpoly_wire_integration",
            ],
            text=True,
            encoding="utf-8",
            errors="ignore",
        ).split()[0].lower()
        expected_hash = release["source_and_binary"]["wire_integration_binary_sha256"].lower()
        if deployed_hash != expected_hash:
            raise RuntimeError("deployed Route A binary hash does not match release manifest")
        release_binding = {
            "release_id": release["release"]["id"],
            "release_manifest_sha256": sha256_file(release_path),
            "deployed_binary_sha256": deployed_hash,
        }

    root = Path(args.data_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    datasets = []
    if args.dataset in ("mnist", "both"):
        datasets.append(("mnist", load_mnist(root)))
    if args.dataset in ("cifar10", "both"):
        datasets.append(("cifar10", load_cifar10(root)))

    all_summaries = []
    t0 = time.perf_counter()
    for name, data in datasets:
        result = run_dataset(args, name, *data, out_dir)
        all_summaries.append({k: v for k, v in result.items() if k not in ("rows", "route_invocations")})
        case_dir = out_dir / f"{name}_seed{args.seed}"
        case_dir.mkdir(parents=True, exist_ok=True)
        with open(case_dir / "rounds.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(result["rows"][0].keys()))
            writer.writeheader()
            writer.writerows(result["rows"])
        with open(case_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in result.items() if k != "rows"}, f, indent=2)
        print(f"{name} {result['status']} final_route_acc={result['final']['route_acc']:.4f} blocks={result['route_a_blocks']}")

    summary = {
        "schema": "fl_utility_route_a_equivalence_v1",
        "status": "PASS" if all(s["status"] == "PASS" for s in all_summaries) else "FAIL",
        "formal": bool(args.formal),
        "release_binding": release_binding,
        "purpose": "FL utility and aggregate-equivalence sanity. Route A aggregate is checked block-wise against quantized plaintext aggregate; this is not a runtime benchmark.",
        "elapsed_seconds": round(time.perf_counter() - t0, 6),
        "cases": all_summaries,
    }
    with open(out_dir / f"summary_seed{args.seed}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
