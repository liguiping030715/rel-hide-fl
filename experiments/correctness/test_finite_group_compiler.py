from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from typing import List


MODULUS = 104729


@dataclass(frozen=True)
class SplitMixTrace:
    inputs: List[int]
    refresh_masks: List[int]
    refreshed: List[int]
    real_fragments_by_client: List[List[int]]
    dummy_records: List[int]
    shuffled_output: List[int]


def mod_sum(values: List[int]) -> int:
    return sum(values) % MODULUS


def sample_zero_sum_vector(rng: random.Random, count: int) -> List[int]:
    if count == 0:
        return []
    free = [rng.randrange(MODULUS) for _ in range(count - 1)]
    return [*free, (-mod_sum(free)) % MODULUS]


def split_fixed_sum(rng: random.Random, value: int, k: int) -> List[int]:
    if k < 1:
        raise ValueError("k must be positive")
    free = [rng.randrange(MODULUS) for _ in range(k - 1)]
    return [*free, (value - mod_sum(free)) % MODULUS]


def apbr_splitmix(inputs: List[int], k: int, k0: int, seed: int) -> SplitMixTrace:
    rng = random.Random(seed)
    masks = sample_zero_sum_vector(rng, len(inputs))
    refreshed = [(x + rho) % MODULUS for x, rho in zip(inputs, masks)]
    real_by_client = [split_fixed_sum(rng, y, k) for y in refreshed]
    dummy = sample_zero_sum_vector(rng, k0)
    output = [frag for group in real_by_client for frag in group] + dummy
    rng.shuffle(output)
    return SplitMixTrace(inputs, masks, refreshed, real_by_client, dummy, output)


def assert_equal(name: str, got: int, expected: int) -> None:
    if got != expected:
        raise AssertionError(f"{name}: got {got}, expected {expected}")


def run_case(clients: int, k: int, k0: int, seed: int) -> None:
    rng = random.Random(seed + 1_000_003)
    inputs = [rng.randrange(MODULUS) for _ in range(clients)]
    trace = apbr_splitmix(inputs, k, k0, seed)

    assert_equal("refresh masks zero sum", mod_sum(trace.refresh_masks), 0)
    assert_equal("refreshed sum preserved", mod_sum(trace.refreshed), mod_sum(trace.inputs))

    for idx, (frags, refreshed) in enumerate(zip(trace.real_fragments_by_client, trace.refreshed)):
        assert_equal(f"client {idx} fragment reconstruction", mod_sum(frags), refreshed)

    assert_equal("dummy zero sum", mod_sum(trace.dummy_records), 0)
    assert_equal("output sum preserved", mod_sum(trace.shuffled_output), mod_sum(trace.inputs))
    assert_equal("output record count", len(trace.shuffled_output), clients * k + k0)


def main() -> int:
    cases = [
        (2, 2, 1, 2024),
        (5, 2, 2, 2025),
        (10, 3, 0, 2026),
        (50, 2, 2, 2027),
    ]
    for case in cases:
        run_case(*case)
    print("[PASS] finite-group APBR-SplitMix invariants")
    print("FINITE_GROUP_COMPILER_SMOKE=PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FAIL] finite-group APBR-SplitMix invariants: {exc}")
        sys.exit(1)
