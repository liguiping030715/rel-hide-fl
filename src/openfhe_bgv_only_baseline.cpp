#include "openfhe.h"
#include "cryptocontext-ser.h"
#include "ciphertext-ser.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace lbcrypto;

using Clock = std::chrono::steady_clock;

namespace {

struct Options {
    size_t clients = 10;
    size_t dimension = 784;
    size_t ringDim = 16384;
    uint64_t seed = 2024;
    uint64_t plaintextModulus = 536903681;
};

double ms(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}

uint64_t parse_u64(const std::string& s) {
    return static_cast<uint64_t>(std::stoull(s));
}

Options parse(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        std::string a(argv[i]);
        auto need = [&](const char* name) -> std::string {
            if (i + 1 >= argc)
                throw std::runtime_error(std::string("missing value for ") + name);
            return std::string(argv[++i]);
        };
        if (a == "--clients") o.clients = parse_u64(need("--clients"));
        else if (a == "--dimension") o.dimension = parse_u64(need("--dimension"));
        else if (a == "--ring-dim") o.ringDim = parse_u64(need("--ring-dim"));
        else if (a == "--seed") o.seed = parse_u64(need("--seed"));
        else if (a == "--plaintext-modulus") o.plaintextModulus = parse_u64(need("--plaintext-modulus"));
        else throw std::runtime_error("unknown argument: " + a);
    }
    if (o.dimension > o.ringDim)
        throw std::runtime_error("dimension must not exceed ring dimension");
    return o;
}

int64_t mod_positive(int64_t x, uint64_t mod) {
    const int64_t m = static_cast<int64_t>(mod);
    int64_t r = x % m;
    if (r < 0) r += m;
    return r;
}

int64_t centered_lift(int64_t x, uint64_t mod) {
    const int64_t half = static_cast<int64_t>(mod / 2);
    const int64_t m = static_cast<int64_t>(mod);
    return (x > half) ? (x - m) : x;
}

size_t serialized_ciphertext_bytes(const Ciphertext<DCRTPoly>& ct) {
    std::ostringstream os(std::ios::binary);
    Serial::Serialize(ct, os, SerType::BINARY);
    return os.str().size();
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const auto tTotal0 = Clock::now();
        const Options opt = parse(argc, argv);

        const auto tSetup0 = Clock::now();
        CCParams<CryptoContextBGVRNS> parameters;
        parameters.SetPlaintextModulus(opt.plaintextModulus);
        parameters.SetMultiplicativeDepth(1);
        parameters.SetSecurityLevel(HEStd_128_classic);
        parameters.SetRingDim(static_cast<uint32_t>(opt.ringDim));
        parameters.SetBatchSize(static_cast<uint32_t>(opt.ringDim));
        auto cc = GenCryptoContext(parameters);
        cc->Enable(PKE);
        cc->Enable(KEYSWITCH);
        cc->Enable(LEVELEDSHE);
        auto keys = cc->KeyGen();
        const auto tSetup1 = Clock::now();

        const size_t batchSlots = cc->GetEncodingParams()->GetBatchSize();
        const size_t chunks = (opt.dimension + batchSlots - 1) / batchSlots;
        std::vector<Ciphertext<DCRTPoly>> aggregate(chunks);
        std::vector<int64_t> target(opt.dimension, 0);
        size_t ciphertextBytes = 0;

        std::mt19937_64 rng(opt.seed);
        std::uniform_int_distribution<int64_t> msgDist(-1000, 1000);

        const auto tClient0 = Clock::now();
        double encodeMs = 0.0;
        double encryptMs = 0.0;
        double evalAddMs = 0.0;
        for (size_t c = 0; c < opt.clients; ++c) {
            std::vector<int64_t> msg(opt.dimension, 0);
            for (size_t i = 0; i < opt.dimension; ++i) {
                msg[i] = msgDist(rng);
                target[i] += msg[i];
            }
            for (size_t chunk = 0; chunk < chunks; ++chunk) {
                std::vector<int64_t> packed(batchSlots, 0);
                const size_t start = chunk * batchSlots;
                const size_t end = std::min(start + batchSlots, opt.dimension);
                for (size_t i = start; i < end; ++i)
                    packed[i - start] = mod_positive(msg[i], opt.plaintextModulus);

                const auto tEncode0 = Clock::now();
                Plaintext pt = cc->MakePackedPlaintext(packed);
                const auto tEncode1 = Clock::now();
                encodeMs += ms(tEncode0, tEncode1);

                const auto tEncrypt0 = Clock::now();
                auto ct = cc->Encrypt(keys.publicKey, pt);
                const auto tEncrypt1 = Clock::now();
                encryptMs += ms(tEncrypt0, tEncrypt1);
                ciphertextBytes += serialized_ciphertext_bytes(ct);

                const auto tEval0 = Clock::now();
                if (c == 0)
                    aggregate[chunk] = ct;
                else
                    aggregate[chunk] = cc->EvalAdd(aggregate[chunk], ct);
                const auto tEval1 = Clock::now();
                evalAddMs += ms(tEval0, tEval1);
            }
        }
        const auto tClient1 = Clock::now();

        const auto tDecrypt0 = Clock::now();
        std::vector<int64_t> recovered(opt.dimension, 0);
        for (size_t chunk = 0; chunk < chunks; ++chunk) {
            Plaintext result;
            cc->Decrypt(keys.secretKey, aggregate[chunk], &result);
            result->SetLength(batchSlots);
            auto values = result->GetPackedValue();
            const size_t start = chunk * batchSlots;
            const size_t end = std::min(start + batchSlots, opt.dimension);
            for (size_t i = start; i < end; ++i) {
                recovered[i] = centered_lift(
                    mod_positive(values[i - start], opt.plaintextModulus),
                    opt.plaintextModulus);
            }
        }
        const auto tDecrypt1 = Clock::now();

        int64_t linf = 0;
        size_t mismatches = 0;
        for (size_t i = 0; i < opt.dimension; ++i) {
            const int64_t diff = recovered[i] - target[i];
            linf = std::max<int64_t>(linf, std::llabs(diff));
            if (diff != 0) ++mismatches;
        }

        const auto tTotal1 = Clock::now();
        const bool pass = (mismatches == 0);
        std::cout << "{\n";
        std::cout << "  \"schema\": \"openfhe_native_bgv_only_baseline_v1\",\n";
        std::cout << "  \"variant\": \"openfhe_bgv_only\",\n";
        std::cout << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n";
        std::cout << "  \"clients\": " << opt.clients << ",\n";
        std::cout << "  \"dimension\": " << opt.dimension << ",\n";
        std::cout << "  \"ring_dim_requested\": " << opt.ringDim << ",\n";
        std::cout << "  \"batch_slots\": " << batchSlots << ",\n";
        std::cout << "  \"chunks\": " << chunks << ",\n";
        std::cout << "  \"plaintext_modulus\": " << opt.plaintextModulus << ",\n";
        std::cout << "  \"q_domain_diff_linf\": " << linf << ",\n";
        std::cout << "  \"q_domain_mismatch_count\": " << mismatches << ",\n";
        std::cout << "  \"total_wire_bytes\": " << ciphertextBytes << ",\n";
        std::cout << "  \"runtime_ms\": {\"setup\": " << ms(tSetup0, tSetup1)
                  << ", \"client_generation\": " << ms(tClient0, tClient1)
                  << ", \"encode\": " << encodeMs
                  << ", \"encrypt\": " << encryptMs
                  << ", \"eval_add\": " << evalAddMs
                  << ", \"decrypt_decode\": " << ms(tDecrypt0, tDecrypt1)
                  << ", \"total\": " << ms(tTotal0, tTotal1) << "}\n";
        std::cout << "}\n";
        return pass ? 0 : 1;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 2;
    }
}
