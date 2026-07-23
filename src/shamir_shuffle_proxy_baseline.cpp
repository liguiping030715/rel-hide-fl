#include "openfhe.h"
#include "v8_randomness.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

using namespace lbcrypto;
using Clock = std::chrono::steady_clock;

namespace {

struct Options {
    size_t clients = 10;
    size_t dimension = 784;
    uint64_t plaintextModulus = 2199023288321ULL;
    uint64_t seed = 2024;
};

Options parse(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg(argv[i]);
        auto next = [&]() -> std::string {
            if (++i >= argc)
                throw std::runtime_error("missing value for " + arg);
            return argv[i];
        };
        if (arg == "--clients") options.clients = std::stoull(next());
        else if (arg == "--dimension") options.dimension = std::stoull(next());
        else if (arg == "--plaintext-modulus") options.plaintextModulus = std::stoull(next());
        else if (arg == "--seed") options.seed = std::stoull(next());
        else throw std::runtime_error("unknown argument: " + arg);
    }
    if (options.clients == 0 || options.clients > 50)
        throw std::runtime_error("clients must be in [1,50]");
    if (options.dimension == 0)
        throw std::runtime_error("dimension must be positive");
    return options;
}

uint64_t splitmix64(uint64_t value) {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

int64_t workload_coordinate(uint64_t seed, size_t client, size_t coordinate) {
    const uint64_t mixed = splitmix64(seed ^ (0x100000001b3ULL * (client + 1)) ^ coordinate);
    return static_cast<int64_t>(mixed % 2001ULL) - 1000;
}

uint64_t positive_mod(__int128 value, uint64_t modulus) {
    __int128 residue = value % static_cast<__int128>(modulus);
    if (residue < 0)
        residue += modulus;
    return static_cast<uint64_t>(residue);
}

int64_t centered_mod(uint64_t value, uint64_t modulus) {
    return value > modulus / 2
               ? static_cast<int64_t>(static_cast<__int128>(value) - static_cast<__int128>(modulus))
               : static_cast<int64_t>(value);
}

uint64_t add_mod(uint64_t left, uint64_t right, uint64_t modulus) {
    return positive_mod(static_cast<__int128>(left) + right, modulus);
}

uint64_t sub_mod(uint64_t left, uint64_t right, uint64_t modulus) {
    return positive_mod(static_cast<__int128>(left) - right, modulus);
}

void append_u64(std::vector<uint8_t>& output, uint64_t value) {
    for (int byte = 0; byte < 8; ++byte)
        output.push_back(static_cast<uint8_t>((value >> (8 * byte)) & 0xff));
}

uint64_t read_u64(const std::vector<uint8_t>& input, size_t& offset) {
    if (offset + 8 > input.size())
        throw std::runtime_error("truncated Shamir proxy record");
    uint64_t value = 0;
    for (int byte = 0; byte < 8; ++byte)
        value |= static_cast<uint64_t>(input[offset++]) << (8 * byte);
    return value;
}

std::vector<uint8_t> serialize_record(const std::vector<uint64_t>& shares, uint64_t modulus) {
    std::vector<uint8_t> output;
    output.reserve(24 + 8 * shares.size());
    append_u64(output, 0x53484d5250525831ULL);  // SHMRPRX1
    append_u64(output, modulus);
    append_u64(output, shares.size());
    for (uint64_t share : shares)
        append_u64(output, share);
    return output;
}

std::vector<uint64_t> deserialize_record(const std::vector<uint8_t>& input,
                                         uint64_t modulus,
                                         size_t dimension) {
    size_t offset = 0;
    if (read_u64(input, offset) != 0x53484d5250525831ULL)
        throw std::runtime_error("Shamir proxy record magic mismatch");
    if (read_u64(input, offset) != modulus)
        throw std::runtime_error("Shamir proxy modulus mismatch");
    if (read_u64(input, offset) != dimension)
        throw std::runtime_error("Shamir proxy dimension mismatch");
    std::vector<uint64_t> output(dimension);
    for (size_t index = 0; index < dimension; ++index) {
        output[index] = read_u64(input, offset);
        if (output[index] >= modulus)
            throw std::runtime_error("noncanonical Shamir proxy residue");
    }
    if (offset != input.size())
        throw std::runtime_error("trailing bytes in Shamir proxy record");
    return output;
}

double milliseconds(Clock::time_point start, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

}  // namespace

int main(int argc, char** argv) {
    try {
        PseudoRandomNumberGenerator::InitPRNGEngine();
        const Options options = parse(argc, argv);
        const auto totalStart = Clock::now();
        const auto setupStart = totalStart;
        const auto setupEnd = Clock::now();

        const auto materialStart = Clock::now();
        std::vector<std::vector<uint8_t>> shufflerOne;
        std::vector<std::vector<uint8_t>> shufflerTwo;
        std::vector<int64_t> target(options.dimension, 0);
        shufflerOne.reserve(options.clients);
        shufflerTwo.reserve(options.clients);
        for (size_t client = 0; client < options.clients; ++client) {
            std::vector<uint64_t> y1(options.dimension);
            std::vector<uint64_t> y2(options.dimension);
            for (size_t coordinate = 0; coordinate < options.dimension; ++coordinate) {
                const int64_t message = workload_coordinate(options.seed, client, coordinate);
                target[coordinate] += message;
                const uint64_t secret = positive_mod(message, options.plaintextModulus);
                const uint64_t slope = routea::v8::UniformBelow(options.plaintextModulus);
                y1[coordinate] = add_mod(secret, slope, options.plaintextModulus);
                y2[coordinate] = add_mod(
                    secret,
                    positive_mod(static_cast<__int128>(2) * slope, options.plaintextModulus),
                    options.plaintextModulus);
            }
            shufflerOne.push_back(serialize_record(y1, options.plaintextModulus));
            shufflerTwo.push_back(serialize_record(y2, options.plaintextModulus));
        }
        const auto materialEnd = Clock::now();

        const auto pathStart = Clock::now();
        routea::v8::FisherYates(shufflerOne);
        routea::v8::FisherYates(shufflerTwo);
        const auto pathEnd = Clock::now();

        const auto recoveryStart = Clock::now();
        std::vector<uint64_t> sumY1(options.dimension, 0);
        std::vector<uint64_t> sumY2(options.dimension, 0);
        bool roundtrip = true;
        auto add_records = [&](const std::vector<std::vector<uint8_t>>& records,
                               std::vector<uint64_t>& sum) {
            for (const auto& record : records) {
                const auto shares = deserialize_record(record, options.plaintextModulus, options.dimension);
                roundtrip = roundtrip && serialize_record(shares, options.plaintextModulus) == record;
                for (size_t coordinate = 0; coordinate < options.dimension; ++coordinate)
                    sum[coordinate] = add_mod(sum[coordinate], shares[coordinate], options.plaintextModulus);
            }
        };
        add_records(shufflerOne, sumY1);
        add_records(shufflerTwo, sumY2);

        int64_t differenceLinf = 0;
        size_t mismatches = 0;
        for (size_t coordinate = 0; coordinate < options.dimension; ++coordinate) {
            const uint64_t recoveredResidue = sub_mod(
                positive_mod(static_cast<__int128>(2) * sumY1[coordinate], options.plaintextModulus),
                sumY2[coordinate],
                options.plaintextModulus);
            const int64_t recovered = centered_mod(recoveredResidue, options.plaintextModulus);
            const int64_t difference = recovered - target[coordinate];
            differenceLinf = std::max<int64_t>(differenceLinf, std::llabs(difference));
            if (difference != 0)
                ++mismatches;
        }
        const auto recoveryEnd = Clock::now();
        size_t pathOneBytes = 0;
        size_t pathTwoBytes = 0;
        for (const auto& record : shufflerOne) pathOneBytes += record.size();
        for (const auto& record : shufflerTwo) pathTwoBytes += record.size();
        const size_t uploadBytes = pathOneBytes + pathTwoBytes;
        const size_t relayBytes = uploadBytes;
        const auto totalEnd = Clock::now();
        const bool pass = roundtrip && mismatches == 0;

        std::cout << "{\n"
                  << "  \"schema\": \"route_a_shamir_shuffle_proxy_v8_v1\",\n"
                  << "  \"variant\": \"shamir_shuffle_proxy\",\n"
                  << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n"
                  << "  \"clients\": " << options.clients << ",\n"
                  << "  \"dimension\": " << options.dimension << ",\n"
                  << "  \"plaintext_modulus\": " << options.plaintextModulus << ",\n"
                  << "  \"share_scheme\": \"degree-one Shamir sharing at x=1,2 over Z_t\",\n"
                  << "  \"reconstruction\": \"2*sum(y_at_1)-sum(y_at_2) mod t\",\n"
                  << "  \"shufflers\": 2,\n"
                  << "  \"permutation_algorithm\": \"Fisher-Yates with rejection-sampled UniformBelow\",\n"
                  << "  \"randomness\": \"OpenFHE process-local PRNG\",\n"
                  << "  \"faithful_UFL_reimplementation\": false,\n"
                  << "  \"wire_serialization_roundtrip\": " << (roundtrip ? "true" : "false") << ",\n"
                  << "  \"encoded_plaintext_diff_linf\": " << differenceLinf << ",\n"
                  << "  \"encoded_plaintext_mismatch_count\": " << mismatches << ",\n"
                  << "  \"client_upload_bytes\": " << uploadBytes << ",\n"
                  << "  \"path_to_cs_bytes\": " << relayBytes << ",\n"
                  << "  \"total_payload_bytes\": " << (uploadBytes + relayBytes) << ",\n"
                  << "  \"runtime_ms\": {\"setup\": " << milliseconds(setupStart, setupEnd)
                  << ", \"material_generation\": " << milliseconds(materialStart, materialEnd)
                  << ", \"sharing\": " << milliseconds(materialStart, materialEnd)
                  << ", \"path_processing\": " << milliseconds(pathStart, pathEnd)
                  << ", \"cs_recovery\": " << milliseconds(recoveryStart, recoveryEnd)
                  << ", \"total\": " << milliseconds(totalStart, totalEnd) << "}\n"
                  << "}\n";
        return pass ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "ERROR: " << error.what() << '\n';
        return 1;
    }
}
