#include "v8_randomness.h"

#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

class SequenceSource {
public:
    explicit SequenceSource(std::vector<uint64_t> values) : values_(std::move(values)) {}

    uint64_t operator()() {
        if (offset_ >= values_.size())
            throw std::runtime_error("deterministic source exhausted");
        return values_[offset_++];
    }

private:
    std::vector<uint64_t> values_;
    size_t offset_ = 0;
};

void Require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

}  // namespace

int main() {
    try {
        SequenceSource rejectionSource({0, 5});
        Require(routea::v8::UniformBelowFrom(3, rejectionSource) == 2,
                "UniformBelow rejection path failed");

        bool zeroRejected = false;
        try {
            SequenceSource source({0});
            (void)routea::v8::UniformBelowFrom(0, source);
        } catch (const std::runtime_error&) {
            zeroRejected = true;
        }
        Require(zeroRejected, "UniformBelow accepted a zero bound");

        std::vector<int> empty;
        SequenceSource emptySource({});
        routea::v8::FisherYatesFrom(empty, emptySource);
        Require(empty.empty(), "empty permutation changed size");

        std::vector<int> singleton{7};
        SequenceSource singletonSource({});
        routea::v8::FisherYatesFrom(singleton, singletonSource);
        Require(singleton == std::vector<int>{7}, "singleton permutation changed value");

        std::vector<int> values{1, 2, 3, 4, 5};
        SequenceSource permutationSource({3, 1, 2, 0});
        routea::v8::FisherYatesFrom(values, permutationSource);
        Require(values == std::vector<int>({5, 1, 3, 2, 4}),
                "Fisher-Yates known-answer test failed");

        std::cout << "{\n"
                  << "  \"schema\": \"route_a_v8_randomness_selftest_v1\",\n"
                  << "  \"status\": \"PASS\",\n"
                  << "  \"uniform_below_rejection_path\": true,\n"
                  << "  \"zero_bound_rejected\": true,\n"
                  << "  \"empty_permutation\": true,\n"
                  << "  \"singleton_permutation\": true,\n"
                  << "  \"fisher_yates_kat\": true,\n"
                  << "  \"modulo_bias\": false,\n"
                  << "  \"security_claim\": false\n"
                  << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
