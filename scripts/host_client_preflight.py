#!/usr/bin/env python3
"""Minimal TCP split-path integration/preflight.

This verifies the host-client data flow and the APBR -> fragmentation -> dummy
-> permutation -> aggregate recovery path over a simple modular representation.
The default material provider is an explicit shared-a RLWE-style construction:

    b_i = a * sk_i + e_i + m_i (mod q)

where the central server only sees aggregate key/body material after the four
split paths. This is an integration step toward the OpenFHE-backed provider;
the script itself does not construct OpenFHE DCRTPoly objects.

It is not a formal OpenFHE benchmark by itself. Formal results must use the
OpenFHE-backed executable once the same role boundary is connected to OpenFHE.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple


Q = (1 << 61) - 1
PATHS = ("S1", "S2", "T1", "T2")


def add_vec(a: List[int], b: List[int]) -> List[int]:
    return [(x + y) % Q for x, y in zip(a, b)]


def sub_vec(a: List[int], b: List[int]) -> List[int]:
    return [(x - y) % Q for x, y in zip(a, b)]


def zero_vec(d: int) -> List[int]:
    return [0] * d


def rand_vec(rng: random.Random, d: int) -> List[int]:
    return [rng.randrange(Q) for _ in range(d)]


def centered(x: int) -> int:
    return x - Q if x > Q // 2 else x


def send_frame(sock: socket.socket, obj: dict) -> int:
    payload = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    frame = struct.pack("!I", len(payload)) + payload
    sock.sendall(frame)
    return len(frame)


def recvn(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("unexpected EOF")
        out.extend(chunk)
    return bytes(out)


def recv_frame(sock: socket.socket) -> Tuple[dict, int]:
    header = recvn(sock, 4)
    (n,) = struct.unpack("!I", header)
    payload = recvn(sock, n)
    return json.loads(payload.decode("utf-8")), 4 + n


def listen_socket(port: int) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen()
    return srv


def split_k(value: List[int], k: int, rng: random.Random) -> List[List[int]]:
    if k <= 0:
        raise ValueError("k must be positive")
    pieces: List[List[int]] = []
    partial = zero_vec(len(value))
    for _ in range(k - 1):
        piece = rand_vec(rng, len(value))
        pieces.append(piece)
        partial = add_vec(partial, piece)
    pieces.append(sub_vec(value, partial))
    return pieces


def apply_apbr(records: List[List[int]], rng: random.Random) -> Tuple[List[List[int]], bool]:
    d = len(records[0])
    masks: List[List[int]] = []
    mask_sum = zero_vec(d)
    for _ in range(len(records) - 1):
        mask = rand_vec(rng, d)
        masks.append(mask)
        mask_sum = add_vec(mask_sum, mask)
    masks.append(sub_vec(zero_vec(d), mask_sum))
    refreshed = [add_vec(record, mask) for record, mask in zip(records, masks)]
    preserved = sum_vectors(refreshed) == sum_vectors(records)
    return refreshed, preserved


def sum_vectors(vectors: List[List[int]]) -> List[int]:
    if not vectors:
        return []
    acc = zero_vec(len(vectors[0]))
    for vec in vectors:
        acc = add_vec(acc, vec)
    return acc


def path_server(path: str, port: int, cs_port: int, clients: int, k: int, k0: int,
                seed: int, metrics: Dict[str, dict]) -> None:
    rng = random.Random(seed)
    recv_bytes = 0
    send_bytes = 0
    records: List[List[int]] = []
    srv = listen_socket(port)
    try:
        for _ in range(clients):
            conn, _ = srv.accept()
            with conn:
                msg, n = recv_frame(conn)
                recv_bytes += n
                if msg.get("path") != path:
                    raise ValueError(f"wrong path for {path}: {msg.get('path')}")
                records.append(msg["vector"])
    finally:
        srv.close()

    refreshed, apbr_preserved = apply_apbr(records, rng)
    fragments: List[dict] = []
    for client_index, value in enumerate(refreshed):
        for piece in split_k(value, k, rng):
            fragments.append({"kind": "real", "client": client_index, "v": piece})

    dummy_pieces = split_k(zero_vec(len(records[0])), k0, rng) if k0 > 0 else []
    for piece in dummy_pieces:
        fragments.append({"kind": "dummy", "client": -1, "v": piece})

    rng.shuffle(fragments)

    relay = {
        "path": path,
        "fragments": fragments,
        "real_fragment_count": clients * k,
        "dummy_fragment_count": k0,
        "apbr_sum_preserved": apbr_preserved,
    }
    with socket.create_connection(("127.0.0.1", cs_port), timeout=30) as sock:
        send_bytes += send_frame(sock, relay)

    metrics[path] = {
        "recv_bytes": recv_bytes,
        "send_bytes": send_bytes,
        "records_received": len(records),
        "real_fragment_count": clients * k,
        "dummy_fragment_count": k0,
        "total_fragment_count": clients * k + k0,
        "apbr_sum_preserved": apbr_preserved,
    }


def cs_server(port: int, expected_target: List[int], message_target: List[int], a: List[int],
              metrics: Dict[str, dict]) -> None:
    recv_bytes = 0
    matrices: Dict[str, List[List[int]]] = {}
    structure: Dict[str, dict] = {}
    srv = listen_socket(port)
    try:
        for _ in PATHS:
            conn, _ = srv.accept()
            with conn:
                msg, n = recv_frame(conn)
                recv_bytes += n
                path = msg["path"]
                matrices[path] = [frag["v"] for frag in msg["fragments"]]
                structure[path] = {
                    "real_fragment_count": msg["real_fragment_count"],
                    "dummy_fragment_count": msg["dummy_fragment_count"],
                    "total_fragment_count": len(msg["fragments"]),
                    "apbr_sum_preserved": msg["apbr_sum_preserved"],
                }
    finally:
        srv.close()

    sk_agg = add_vec(sum_vectors(matrices["S1"]), sum_vectors(matrices["S2"]))
    body_agg = add_vec(sum_vectors(matrices["T1"]), sum_vectors(matrices["T2"]))
    recovered = [(b - (aa * s) % Q) % Q for aa, s, b in zip(a, sk_agg, body_agg)]
    recovered_centered = [centered(x) for x in recovered]

    q_domain_errors = [x - y for x, y in zip(recovered_centered, expected_target)]
    message_domain_errors = [x - y for x, y in zip(recovered_centered, message_target)]
    errors = [abs(x) for x in q_domain_errors]
    metrics["CS"] = {
        "recv_bytes": recv_bytes,
        "structure": structure,
        "max_abs_error_quantized": max(errors) if errors else 0,
        "mismatch_count": sum(1 for e in errors if e != 0),
        "material_check": {
            "comparison_domain": "q_domain_message_plus_error",
            "q_domain_diff_linf": max(abs(x) for x in q_domain_errors) if q_domain_errors else 0,
            "message_domain_diff_linf": max(abs(x) for x in message_domain_errors) if message_domain_errors else 0,
            "q_domain_diff_preview": q_domain_errors[:8],
            "message_domain_diff_preview": message_domain_errors[:8],
            "target_message_preview": message_target[:8],
            "target_q_domain_preview": expected_target[:8],
            "recovered_preview": recovered_centered[:8],
            "note": "The modular integration runner recovers m+e exactly before plaintext-modulus cancellation. Formal BGV-style decoding must reduce the aggregate error modulo t."
        },
    }


def sample_error(rng: random.Random, noise: str) -> int:
    if noise == "zero":
        return 0
    if noise in ("dgg32", "openfhe_dgg32"):
        return int(round(rng.gauss(0.0, 3.2)))
    if noise == "small":
        return rng.randint(-1, 1)
    raise ValueError(f"unsupported noise mode: {noise}")


def make_client_vectors(clients: int, dimension: int, seed: int, noise: str) -> Tuple[List[int], List[List[int]], List[List[int]], List[int], List[List[int]]]:
    rng = random.Random(seed)
    a = [rng.randrange(1, Q) for _ in range(dimension)]
    messages = [[rng.randint(-1000, 1000) for _ in range(dimension)] for _ in range(clients)]
    target = [sum(messages[i][j] for i in range(clients)) for j in range(dimension)]
    sk = [[rng.randint(-2, 2) % Q for _ in range(dimension)] for _ in range(clients)]
    errors = [[sample_error(rng, noise) for _ in range(dimension)] for _ in range(clients)]
    body = [[((a[j] * sk[i][j]) + errors[i][j] + messages[i][j]) % Q for j in range(dimension)] for i in range(clients)]
    return a, sk, body, target, errors


def client_thread(client_id: int, ports: Dict[str, int], sk: List[int], body: List[int],
                  seed: int, metrics: Dict[str, dict]) -> None:
    rng = random.Random(seed)
    sk1 = rand_vec(rng, len(sk))
    sk2 = sub_vec(sk, sk1)
    b1 = rand_vec(rng, len(body))
    b2 = sub_vec(body, b1)
    payloads = {
        "S1": sk1,
        "S2": sk2,
        "T1": b1,
        "T2": b2,
    }
    sent = {}
    for path, vec in payloads.items():
        with socket.create_connection(("127.0.0.1", ports[path]), timeout=30) as sock:
            sent[path] = send_frame(sock, {"client": client_id, "path": path, "vector": vec})
    metrics[f"client_{client_id:03d}"] = {"send_bytes": sent}


def run_case(args: argparse.Namespace) -> dict:
    base_port = args.base_port
    ports = {"S1": base_port, "S2": base_port + 1, "T1": base_port + 2, "T2": base_port + 3}
    cs_port = base_port + 4
    metrics: Dict[str, dict] = {}
    a, sk, body, target, errors = make_client_vectors(args.clients, args.dimension, args.seed, args.noise)
    aggregate_error = [sum(errors[i][j] for i in range(args.clients)) for j in range(args.dimension)]
    # In BGV-style recovery the aggregate error cancels modulo t. This modular
    # preflight uses q-domain integer recovery, so subtract the sampled aggregate
    # error from the recovered value for exact message comparison.
    q_domain_target = [target[j] + aggregate_error[j] for j in range(args.dimension)]

    threads: List[threading.Thread] = []
    cs = threading.Thread(target=cs_server, args=(cs_port, q_domain_target, target, a, metrics), daemon=True)
    cs.start()
    threads.append(cs)

    for idx, path in enumerate(PATHS):
        t = threading.Thread(
            target=path_server,
            args=(path, ports[path], cs_port, args.clients, args.k, args.k0, args.seed + 100 + idx, metrics),
            daemon=True,
        )
        t.start()
        threads.append(t)

    time.sleep(0.2)
    client_threads = []
    for i in range(args.clients):
        t = threading.Thread(
            target=client_thread,
            args=(i, ports, sk[i], body[i], args.seed + 1000 + i, metrics),
            daemon=True,
        )
        t.start()
        client_threads.append(t)

    for t in client_threads:
        t.join(timeout=60)
    for t in threads:
        t.join(timeout=60)

    client_to_path = sum(
        sum(v for v in metrics[f"client_{i:03d}"]["send_bytes"].values())
        for i in range(args.clients)
    )
    path_to_cs = sum(metrics[p]["send_bytes"] for p in PATHS)
    path_recv = sum(metrics[p]["recv_bytes"] for p in PATHS)
    cs_recv = metrics["CS"]["recv_bytes"]
    summary = {
        "schema": "host_client_tcp_preflight_v1",
        "formal": False,
        "openfhe_integrated": False,
        "material_provider": "shared_a_rlwe_style_modular",
        "status": "PASS" if metrics["CS"]["mismatch_count"] == 0 else "FAIL",
        "case": {
            "clients": args.clients,
            "dimension": args.dimension,
            "k": args.k,
            "k0": args.k0,
            "seed": args.seed,
            "noise": args.noise,
        },
        "rlwe_material": {
            "shared_a": True,
            "body_formula": "b_i = a*sk_i + e_i + m_i mod q",
            "secret_distribution": "coefficient-wise uniform over {-2,-1,0,1,2}",
            "error_mode": args.noise,
            "aggregate_error_linf": max(abs(x) for x in aggregate_error) if aggregate_error else 0,
            "openfhe_dcrtpoly_objects": False
        },
        "correctness": metrics["CS"],
        "transport": {
            "client_to_path_sender_bytes": client_to_path,
            "path_server_receiver_bytes": path_recv,
            "path_to_cs_sender_bytes": path_to_cs,
            "cs_receiver_bytes": cs_recv,
            "client_to_path_conservation": client_to_path == path_recv,
            "path_to_cs_conservation": path_to_cs == cs_recv,
            "total_protocol_bytes": client_to_path + path_to_cs,
            "direct_client_to_cs_bytes": 0,
        },
        "paths": {p: metrics[p] for p in PATHS},
        "note": "Protocol-topology preflight only; not a formal OpenFHE benchmark.",
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=2)
    parser.add_argument("--dimension", type=int, default=16)
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--k0", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2024)
    parser.add_argument("--noise", default="zero", choices=["zero", "small", "dgg32", "openfhe_dgg32"])
    parser.add_argument("--base-port", type=int, default=42000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    summary = run_case(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": summary["status"], "out": str(args.out)}, sort_keys=True))
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
