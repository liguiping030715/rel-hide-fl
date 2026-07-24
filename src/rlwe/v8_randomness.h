#pragma once

#include "openfhe.h"

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <utility>
#include <vector>

namespace routea::v8 {

inline uint64_t OpenFhePrngU64() {
    PRNG& prng = lbcrypto::PseudoRandomNumberGenerator::GetPRNG();
    return (static_cast<uint64_t>(prng()) << 32) | static_cast<uint64_t>(prng());
}

template <typename NextU64>
uint64_t UniformBelowFrom(uint64_t bound, NextU64&& nextU64) {
    if (bound == 0)
        throw std::runtime_error("UniformBelow requires a positive bound");

    const uint64_t threshold = static_cast<uint64_t>(-bound) % bound;
    for (;;) {
        const uint64_t value = nextU64();
        if (value >= threshold)
            return value % bound;
    }
}

inline uint64_t UniformBelow(uint64_t bound) {
    return UniformBelowFrom(bound, [] { return OpenFhePrngU64(); });
}

template <typename T, typename NextU64>
void FisherYatesFrom(std::vector<T>& values, NextU64&& nextU64) {
    for (size_t i = values.size(); i > 1; --i) {
        const size_t j = static_cast<size_t>(UniformBelowFrom(i, nextU64));
        std::swap(values[i - 1], values[j]);
    }
}

template <typename T>
void FisherYates(std::vector<T>& values) {
    FisherYatesFrom(values, [] { return OpenFhePrngU64(); });
}

}  // namespace routea::v8
