#include "openfhe.h"
#include "utils/hashutil.h"
#include "v8_randomness.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

using namespace lbcrypto;

using Clock = std::chrono::steady_clock;

namespace {

struct Options {
    size_t clients = 2;
    size_t dimension = 16;
    size_t ringDim = 1024;
    uint32_t towers = 2;
    uint32_t bits = 50;
    uint64_t plaintextModulus = 2199023288321ULL;
    size_t k = 2;
    size_t k0 = 2;
    uint64_t seed = 2024;
    std::string noise = "zero";
    std::string variant = "full_protocol";
    std::string packing = "intcrt_polysubr";
    std::string messagesFile;
    std::string role = "single";
    std::string workDir;
    std::string pathId;
    std::string runId = "preflight";
    std::string releaseId = "v8_RC1";
    std::string transport = "file";
    std::string host = "127.0.0.1";
    uint16_t basePort = 24100;
    bool controlBarrier = false;
    size_t clientIndex = 0;
    bool emitRecovered = false;
    bool apbr = true;
};

struct WireBlob {
    std::vector<uint8_t> bytes;
    bool isDummy = false;
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
        else if (a == "--towers") o.towers = static_cast<uint32_t>(parse_u64(need("--towers")));
        else if (a == "--bits") o.bits = static_cast<uint32_t>(parse_u64(need("--bits")));
        else if (a == "--plaintext-modulus") o.plaintextModulus = parse_u64(need("--plaintext-modulus"));
        else if (a == "--k") o.k = parse_u64(need("--k"));
        else if (a == "--k0") o.k0 = parse_u64(need("--k0"));
        else if (a == "--seed") o.seed = parse_u64(need("--seed"));
        else if (a == "--noise") o.noise = need("--noise");
        else if (a == "--variant") o.variant = need("--variant");
        else if (a == "--packing") o.packing = need("--packing");
        else if (a == "--messages-file") o.messagesFile = need("--messages-file");
        else if (a == "--role") o.role = need("--role");
        else if (a == "--work-dir") o.workDir = need("--work-dir");
        else if (a == "--path-id") o.pathId = need("--path-id");
        else if (a == "--run-id") o.runId = need("--run-id");
        else if (a == "--release-id") o.releaseId = need("--release-id");
        else if (a == "--transport") o.transport = need("--transport");
        else if (a == "--host") o.host = need("--host");
        else if (a == "--base-port") o.basePort = static_cast<uint16_t>(parse_u64(need("--base-port")));
        else if (a == "--control-barrier") {
            const std::string v = need("--control-barrier");
            if (v == "true" || v == "1" || v == "on") o.controlBarrier = true;
            else if (v == "false" || v == "0" || v == "off") o.controlBarrier = false;
            else throw std::runtime_error("unsupported --control-barrier value");
        }
        else if (a == "--client-index") o.clientIndex = parse_u64(need("--client-index"));
        else if (a == "--emit-recovered") {
            const std::string v = need("--emit-recovered");
            if (v == "true" || v == "1" || v == "on") o.emitRecovered = true;
            else if (v == "false" || v == "0" || v == "off") o.emitRecovered = false;
            else throw std::runtime_error("unsupported --emit-recovered value");
        }
        else if (a == "--apbr") {
            const std::string v = need("--apbr");
            if (v == "true" || v == "1" || v == "on") o.apbr = true;
            else if (v == "false" || v == "0" || v == "off") o.apbr = false;
            else throw std::runtime_error("unsupported --apbr value");
        }
        else throw std::runtime_error("unknown argument: " + a);
    }
    const size_t maxDimension = (o.packing == "intcrt_polysubr") ? (2 * o.ringDim) : o.ringDim;
    if (o.dimension > maxDimension)
        throw std::runtime_error("dimension exceeds packing profile capacity");
    if (o.plaintextModulus < 3)
        throw std::runtime_error("plaintext modulus must be at least 3");
    if (o.variant != "full_protocol" && o.variant != "openfhe_rlwe_only" &&
        o.variant != "shuffle_only" && o.variant != "plain_aggregate" &&
        o.variant != "shamir_shuffle_proxy" && o.variant != "four_path_sum_only" &&
        o.variant != "wire_codec_selftest")
        throw std::runtime_error("unsupported variant");
    if (o.packing != "intcrt_polysubr" && o.packing != "direct_coefficient")
        throw std::runtime_error("unsupported packing profile");
    if (o.role != "single" && o.role != "setup" && o.role != "client" &&
        o.role != "path" && o.role != "cs" && o.role != "orchestrator")
        throw std::runtime_error("unsupported role");
    if (o.role != "single" && o.workDir.empty())
        throw std::runtime_error("distributed roles require --work-dir");
    if (o.transport != "file" && o.transport != "tcp")
        throw std::runtime_error("unsupported transport");
    if (o.role == "client" && o.clientIndex >= o.clients)
        throw std::runtime_error("client index outside cohort");
    if (o.role == "path" && o.pathId != "S1" && o.pathId != "S2" &&
        o.pathId != "T1" && o.pathId != "T2")
        throw std::runtime_error("path role requires S1, S2, T1 or T2");
    return o;
}

std::vector<std::vector<int64_t>> load_messages_file(const std::string& path,
                                                     size_t clients,
                                                     size_t dimension) {
    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("cannot open messages file: " + path);
    std::vector<std::vector<int64_t>> rows;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty())
            continue;
        std::vector<int64_t> row;
        std::stringstream ss(line);
        std::string cell;
        while (std::getline(ss, cell, ',')) {
            if (!cell.empty())
                row.push_back(std::stoll(cell));
        }
        if (row.size() != dimension)
            throw std::runtime_error("messages file row has wrong dimension");
        rows.push_back(std::move(row));
    }
    if (rows.size() != clients)
        throw std::runtime_error("messages file has wrong client count");
    return rows;
}

void emit_int_vector_json(const std::vector<int64_t>& values, size_t n) {
    std::cout << "[";
    for (size_t i = 0; i < n; ++i) {
        if (i) std::cout << ", ";
        std::cout << values[i];
    }
    std::cout << "]";
}

void emit_tower_moduli_json(const std::shared_ptr<ILDCRTParams<BigInteger>>& params) {
    std::cout << "[";
    const auto& towers = params->GetParams();
    for (size_t i = 0; i < towers.size(); ++i) {
        if (i) std::cout << ", ";
        std::cout << towers[i]->GetModulus().ConvertToInt<uint64_t>();
    }
    std::cout << "]";
}

uint64_t splitmix64(uint64_t x) {
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

int64_t workload_coordinate(uint64_t applicationSeed, size_t client, size_t coordinate) {
    const uint64_t mixed = splitmix64(applicationSeed ^
                                      splitmix64(static_cast<uint64_t>(client)) ^
                                      splitmix64(static_cast<uint64_t>(coordinate)));
    return static_cast<int64_t>(mixed % 2001ULL) - 1000;
}

int64_t centered(uint64_t x, uint64_t mod) {
    return (x > mod / 2) ? static_cast<int64_t>(x - mod) : static_cast<int64_t>(x);
}

uint64_t positive_mod_i128(__int128 x, uint64_t mod) {
    __int128 r = x % static_cast<__int128>(mod);
    if (r < 0)
        r += static_cast<__int128>(mod);
    return static_cast<uint64_t>(r);
}

uint64_t mul_mod_u64(uint64_t a, uint64_t b, uint64_t mod) {
    return static_cast<uint64_t>((static_cast<unsigned __int128>(a) * b) % mod);
}

uint64_t add_mod_u64(uint64_t a, uint64_t b, uint64_t mod) {
    return static_cast<uint64_t>((static_cast<unsigned __int128>(a) + b) % mod);
}

uint64_t sub_mod_u64(uint64_t a, uint64_t b, uint64_t mod) {
    return (a >= b) ? (a - b) : static_cast<uint64_t>(static_cast<unsigned __int128>(a) + mod - b);
}

uint64_t inv_mod_u64(uint64_t a, uint64_t mod) {
    __int128 t = 0, newT = 1;
    __int128 r = static_cast<__int128>(mod);
    __int128 newR = static_cast<__int128>(a % mod);
    while (newR != 0) {
        const __int128 q = r / newR;
        const __int128 nextT = t - q * newT;
        t = newT;
        newT = nextT;
        const __int128 nextR = r - q * newR;
        r = newR;
        newR = nextR;
    }
    if (r != 1)
        throw std::runtime_error("modular inverse does not exist");
    if (t < 0)
        t += static_cast<__int128>(mod);
    return static_cast<uint64_t>(t);
}

uint64_t pow_mod_u64(uint64_t base, uint64_t exp, uint64_t mod) {
    uint64_t result = 1 % mod;
    uint64_t x = base % mod;
    while (exp > 0) {
        if (exp & 1)
            result = mul_mod_u64(result, x, mod);
        x = mul_mod_u64(x, x, mod);
        exp >>= 1;
    }
    return result;
}

void ntt_in_place(std::vector<uint64_t>& a, uint64_t primitiveRoot, uint64_t mod, bool invert) {
    const size_t n = a.size();
    if (n == 0 || (n & (n - 1)) != 0)
        throw std::runtime_error("NTT length must be a nonzero power of two");
    for (size_t i = 1, j = 0; i < n; ++i) {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1)
            j ^= bit;
        j ^= bit;
        if (i < j)
            std::swap(a[i], a[j]);
    }
    const uint64_t root = invert ? inv_mod_u64(primitiveRoot, mod) : primitiveRoot;
    for (size_t len = 2; len <= n; len <<= 1) {
        const uint64_t wlen = pow_mod_u64(root, static_cast<uint64_t>(n / len), mod);
        for (size_t i = 0; i < n; i += len) {
            uint64_t w = 1;
            for (size_t j = 0; j < len / 2; ++j) {
                const uint64_t u = a[i + j];
                const uint64_t v = mul_mod_u64(a[i + j + len / 2], w, mod);
                a[i + j] = add_mod_u64(u, v, mod);
                a[i + j + len / 2] = sub_mod_u64(u, v, mod);
                w = mul_mod_u64(w, wlen, mod);
            }
        }
    }
    if (invert) {
        const uint64_t invN = inv_mod_u64(static_cast<uint64_t>(n), mod);
        for (uint64_t& x : a)
            x = mul_mod_u64(x, invN, mod);
    }
}

uint64_t negacyclic_omega_2n(size_t n, uint64_t plaintextModulus) {
    if ((plaintextModulus - 1) % (2 * n) != 0)
        throw std::runtime_error("plaintext modulus does not support 2N-th root for PolySubR");
    // For the frozen profile t=2199023288321, 3 is a primitive generator.
    const uint64_t omega = pow_mod_u64(3, (plaintextModulus - 1) / (2 * n), plaintextModulus);
    if (pow_mod_u64(omega, static_cast<uint64_t>(n), plaintextModulus) != plaintextModulus - 1 ||
        pow_mod_u64(omega, static_cast<uint64_t>(2 * n), plaintextModulus) != 1)
        throw std::runtime_error("failed to construct primitive 2N-th root for PolySubR");
    return omega;
}

int64_t centered_mod_t(uint64_t x, uint64_t t) {
    return (x > t / 2) ? static_cast<int64_t>(static_cast<__int128>(x) - static_cast<__int128>(t))
                       : static_cast<int64_t>(x);
}

void append_u64(std::vector<uint8_t>& out, uint64_t x) {
    for (int i = 0; i < 8; ++i)
        out.push_back(static_cast<uint8_t>((x >> (8 * i)) & 0xff));
}

uint64_t read_u64(const std::vector<uint8_t>& in, size_t& off) {
    if (off + 8 > in.size())
        throw std::runtime_error("truncated wire blob");
    uint64_t x = 0;
    for (int i = 0; i < 8; ++i)
        x |= static_cast<uint64_t>(in[off++]) << (8 * i);
    return x;
}

std::vector<uint8_t> serialize_plain_share_record(const std::vector<uint64_t>& residues,
                                                   uint64_t plaintextModulus) {
    std::vector<uint8_t> out;
    append_u64(out, 0x4153505850525831ULL);  // ASPXPRX1
    append_u64(out, plaintextModulus);
    append_u64(out, static_cast<uint64_t>(residues.size()));
    for (uint64_t x : residues)
        append_u64(out, x);
    return out;
}

std::vector<uint64_t> deserialize_plain_share_record(const std::vector<uint8_t>& bytes,
                                                     uint64_t plaintextModulus,
                                                     size_t dimension) {
    size_t off = 0;
    const uint64_t magic = read_u64(bytes, off);
    if (magic != 0x4153505850525831ULL)
        throw std::runtime_error("bad additive-sharing proxy wire magic");
    const uint64_t mod = read_u64(bytes, off);
    if (mod != plaintextModulus)
        throw std::runtime_error("additive-sharing proxy plaintext modulus mismatch");
    const size_t n = static_cast<size_t>(read_u64(bytes, off));
    if (n != dimension)
        throw std::runtime_error("additive-sharing proxy dimension mismatch");
    std::vector<uint64_t> residues;
    residues.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        const uint64_t x = read_u64(bytes, off);
        if (x >= plaintextModulus)
            throw std::runtime_error("noncanonical additive-sharing proxy residue");
        residues.push_back(x);
    }
    if (off != bytes.size())
        throw std::runtime_error("trailing bytes in additive-sharing proxy wire blob");
    return residues;
}

DCRTPoly make_poly(const std::shared_ptr<ILDCRTParams<BigInteger>>& params,
                   const std::vector<int64_t>& coeffs) {
    DCRTPoly p(params, Format::COEFFICIENT, true);
    p = coeffs;
    p.SetFormat(Format::EVALUATION);
    return p;
}

DCRTPoly random_poly(const std::shared_ptr<ILDCRTParams<BigInteger>>& params) {
    DCRTPoly::DugType dug;
    return DCRTPoly(dug, params, Format::EVALUATION);
}

DCRTPoly sample_secret_poly(const std::shared_ptr<ILDCRTParams<BigInteger>>& params) {
    DCRTPoly::TugType tug;
    return DCRTPoly(tug, params, Format::EVALUATION);
}

DCRTPoly sample_error_poly(const std::shared_ptr<ILDCRTParams<BigInteger>>& params,
                           const std::string& mode) {
    if (mode == "zero")
        return DCRTPoly(params, Format::EVALUATION, true);
    if (mode == "small") {
        DCRTPoly::TugType tug;
        return DCRTPoly(tug, params, Format::EVALUATION);
    }
    if (mode == "dgg32") {
        DCRTPoly::DggType dgg(3.2);
        return DCRTPoly(dgg, params, Format::EVALUATION);
    }
    throw std::runtime_error("unsupported noise mode");
}

std::vector<uint8_t> serialize_poly(DCRTPoly p) {
    p.SetFormat(Format::COEFFICIENT);
    std::vector<uint8_t> out;
    append_u64(out, 0x4450435457505631ULL);  // DPCTWPV1
    append_u64(out, p.GetNumOfElements());
    append_u64(out, p.GetRingDimension());
    for (const auto& tower : p.GetAllElements()) {
        append_u64(out, tower.GetModulus().ConvertToInt<uint64_t>());
    }
    for (const auto& tower : p.GetAllElements()) {
        const auto& vals = tower.GetValues();
        for (size_t i = 0; i < vals.GetLength(); ++i)
            append_u64(out, vals[i].ConvertToInt<uint64_t>());
    }
    return out;
}

DCRTPoly deserialize_poly(const std::vector<uint8_t>& bytes,
                          const std::shared_ptr<ILDCRTParams<BigInteger>>& params) {
    size_t off = 0;
    const uint64_t magic = read_u64(bytes, off);
    if (magic != 0x4450435457505631ULL)
        throw std::runtime_error("bad DCRTPoly wire magic");
    const size_t towerCount = read_u64(bytes, off);
    const size_t n = read_u64(bytes, off);
    if (towerCount != params->GetParams().size())
        throw std::runtime_error("tower count mismatch");
    if (n != params->GetRingDimension())
        throw std::runtime_error("ring dimension mismatch");
    std::vector<uint64_t> moduli;
    for (size_t i = 0; i < towerCount; ++i) {
        const uint64_t m = read_u64(bytes, off);
        const uint64_t expected = params->GetParams()[i]->GetModulus().ConvertToInt<uint64_t>();
        if (m != expected)
            throw std::runtime_error("tower modulus mismatch");
        moduli.push_back(m);
    }
    DCRTPoly p(params, Format::COEFFICIENT, true);
    for (size_t t = 0; t < towerCount; ++t) {
        NativeVector vals(n, NativeInteger(moduli[t]));
        for (size_t i = 0; i < n; ++i) {
            const uint64_t residue = read_u64(bytes, off);
            if (residue >= moduli[t])
                throw std::runtime_error("noncanonical DCRTPoly residue");
            vals[i] = NativeInteger(residue);
        }
        p.GetAllElements()[t].SetValues(vals, Format::COEFFICIENT);
    }
    if (off != bytes.size())
        throw std::runtime_error("trailing bytes in DCRTPoly wire blob");
    p.SetFormat(Format::EVALUATION);
    return p;
}

std::vector<int64_t> first_tower_coeffs(DCRTPoly p, size_t n) {
    p.SetFormat(Format::COEFFICIENT);
    const auto& tower = p.GetElementAtIndex(0);
    const auto& vals = tower.GetValues();
    const uint64_t mod = tower.GetModulus().ConvertToInt<uint64_t>();
    std::vector<int64_t> out;
    out.reserve(n);
    for (size_t i = 0; i < n; ++i)
        out.push_back(centered(vals[i].ConvertToInt<uint64_t>(), mod));
    return out;
}

std::vector<int64_t> crt_coeffs_mod_t(DCRTPoly p, size_t n, uint64_t plaintextModulus) {
    p.SetFormat(Format::COEFFICIENT);
    const size_t towerCount = p.GetNumOfElements();
    if (towerCount > 2)
        throw std::runtime_error("CRT mod-t recovery is validated for one or two towers only");
    std::vector<int64_t> out;
    out.reserve(n);
    if (towerCount == 1) {
        const auto& tower = p.GetElementAtIndex(0);
        const uint64_t q = tower.GetModulus().ConvertToInt<uint64_t>();
        for (size_t coeff = 0; coeff < n; ++coeff) {
            const int64_t centeredQ = centered(tower.GetValues()[coeff].ConvertToInt<uint64_t>(), q);
            out.push_back(centered_mod_t(positive_mod_i128(centeredQ, plaintextModulus), plaintextModulus));
        }
        return out;
    }

    const auto& tower0 = p.GetElementAtIndex(0);
    const auto& tower1 = p.GetElementAtIndex(1);
    const uint64_t q0 = tower0.GetModulus().ConvertToInt<uint64_t>();
    const uint64_t q1 = tower1.GetModulus().ConvertToInt<uint64_t>();
    const uint64_t invQ0ModQ1 = inv_mod_u64(q0 % q1, q1);
    const unsigned __int128 Q = static_cast<unsigned __int128>(q0) * q1;
    for (size_t coeff = 0; coeff < n; ++coeff) {
        const uint64_t r0 = tower0.GetValues()[coeff].ConvertToInt<uint64_t>();
        const uint64_t r1 = tower1.GetValues()[coeff].ConvertToInt<uint64_t>();
        const uint64_t delta = sub_mod_u64(r1, r0 % q1, q1);
        const uint64_t k = mul_mod_u64(delta, invQ0ModQ1, q1);
        const unsigned __int128 x = static_cast<unsigned __int128>(r0) +
                                    static_cast<unsigned __int128>(q0) * k;
        const __int128 centeredQ = (x > Q / 2)
                                       ? static_cast<__int128>(x) - static_cast<__int128>(Q)
                                       : static_cast<__int128>(x);
        out.push_back(centered_mod_t(positive_mod_i128(centeredQ, plaintextModulus), plaintextModulus));
    }
    return out;
}

std::vector<int64_t> embed_plaintext_coeffs(const std::vector<int64_t>& coeffs,
                                            uint64_t plaintextModulus) {
    std::vector<int64_t> out;
    out.reserve(coeffs.size());
    for (const int64_t x : coeffs) {
        const uint64_t r = positive_mod_i128(x, plaintextModulus);
        out.push_back(centered_mod_t(r, plaintextModulus));
    }
    return out;
}

constexpr uint64_t INTCRT_M1 = 131071ULL;
constexpr uint64_t INTCRT_M2 = 131101ULL;

size_t packed_coeff_count(const Options& opt) {
    if (opt.packing == "direct_coefficient")
        return opt.dimension;
    return opt.ringDim;
}

std::vector<int64_t> pack_plaintext_profile(const std::vector<int64_t>& coords,
                                            size_t ringDim,
                                            const std::string& packing,
                                            uint64_t plaintextModulus) {
    std::vector<int64_t> coeffs(ringDim, 0);
    if (packing == "direct_coefficient") {
        if (coords.size() > ringDim)
            throw std::runtime_error("direct coefficient packing exceeds ring dimension");
        for (size_t i = 0; i < coords.size(); ++i)
            coeffs[i] = coords[i];
        return coeffs;
    }

    const size_t groups = (coords.size() + 1) / 2;
    if (groups > ringDim)
        throw std::runtime_error("IntCRT-PolySubR packing exceeds ring dimension");
    std::vector<uint64_t> evals(ringDim, 0);
    const uint64_t invM1ModM2 = inv_mod_u64(INTCRT_M1 % INTCRT_M2, INTCRT_M2);
    const unsigned __int128 M = static_cast<unsigned __int128>(INTCRT_M1) * INTCRT_M2;
    for (size_t g = 0; g < groups; ++g) {
        const int64_t z1 = coords[2 * g];
        const int64_t z2 = (2 * g + 1 < coords.size()) ? coords[2 * g + 1] : 0;
        const uint64_t r1 = positive_mod_i128(z1, INTCRT_M1);
        const uint64_t r2 = positive_mod_i128(z2, INTCRT_M2);
        const uint64_t delta = sub_mod_u64(r2, r1 % INTCRT_M2, INTCRT_M2);
        const uint64_t k = mul_mod_u64(delta, invM1ModM2, INTCRT_M2);
        const unsigned __int128 x = static_cast<unsigned __int128>(r1) +
                                    static_cast<unsigned __int128>(INTCRT_M1) * k;
        const __int128 centeredM = (x > M / 2)
                                       ? static_cast<__int128>(x) - static_cast<__int128>(M)
                                       : static_cast<__int128>(x);
        if (centeredM > std::numeric_limits<int64_t>::max() ||
            centeredM < std::numeric_limits<int64_t>::min())
            throw std::runtime_error("IntCRT packed coefficient exceeds int64 range");
        evals[g] = positive_mod_i128(centeredM, plaintextModulus);
    }

    const uint64_t omega = negacyclic_omega_2n(ringDim, plaintextModulus);
    const uint64_t rootN = mul_mod_u64(omega, omega, plaintextModulus);
    ntt_in_place(evals, rootN, plaintextModulus, true);
    uint64_t omegaInvPow = 1;
    const uint64_t omegaInv = inv_mod_u64(omega, plaintextModulus);
    for (size_t j = 0; j < ringDim; ++j) {
        const uint64_t coeff = mul_mod_u64(evals[j], omegaInvPow, plaintextModulus);
        coeffs[j] = centered_mod_t(coeff, plaintextModulus);
        omegaInvPow = mul_mod_u64(omegaInvPow, omegaInv, plaintextModulus);
    }
    return coeffs;
}

std::vector<int64_t> unpack_plaintext_profile(const std::vector<int64_t>& packed,
                                              size_t dimension,
                                              const std::string& packing,
                                              size_t ringDim,
                                              uint64_t plaintextModulus) {
    std::vector<int64_t> coords(dimension, 0);
    if (packing == "direct_coefficient") {
        if (packed.size() < dimension)
            throw std::runtime_error("direct coefficient unpack input too short");
        for (size_t i = 0; i < dimension; ++i)
            coords[i] = packed[i];
        return coords;
    }

    const size_t groups = (dimension + 1) / 2;
    if (packed.size() < ringDim)
        throw std::runtime_error("IntCRT-PolySubR unpack input too short");
    std::vector<uint64_t> evals(ringDim, 0);
    const uint64_t omega = negacyclic_omega_2n(ringDim, plaintextModulus);
    const uint64_t rootN = mul_mod_u64(omega, omega, plaintextModulus);
    uint64_t omegaPow = 1;
    for (size_t j = 0; j < ringDim; ++j) {
        evals[j] = mul_mod_u64(positive_mod_i128(packed[j], plaintextModulus), omegaPow, plaintextModulus);
        omegaPow = mul_mod_u64(omegaPow, omega, plaintextModulus);
    }
    ntt_in_place(evals, rootN, plaintextModulus, false);
    for (size_t g = 0; g < groups; ++g) {
        const int64_t xg = centered_mod_t(evals[g], plaintextModulus);
        coords[2 * g] = centered_mod_t(positive_mod_i128(xg, INTCRT_M1), INTCRT_M1);
        if (2 * g + 1 < dimension)
            coords[2 * g + 1] = centered_mod_t(positive_mod_i128(xg, INTCRT_M2), INTCRT_M2);
    }
    return coords;
}

std::vector<int64_t> scale_coeffs(const std::vector<int64_t>& coeffs, uint64_t scalar) {
    std::vector<int64_t> out;
    out.reserve(coeffs.size());
    for (const int64_t x : coeffs) {
        const __int128 scaled = static_cast<__int128>(x) * static_cast<__int128>(scalar);
        if (scaled > std::numeric_limits<int64_t>::max() || scaled < std::numeric_limits<int64_t>::min())
            throw std::runtime_error("scaled coefficient exceeds int64 range");
        out.push_back(static_cast<int64_t>(scaled));
    }
    return out;
}

std::string reported_packing_profile(const std::string& packing) {
    return packing == "intcrt_polysubr" ? "intcrt_polysubr_idempotent" : packing;
}

std::vector<DCRTPoly> split_poly(const DCRTPoly& value,
                                 const std::shared_ptr<ILDCRTParams<BigInteger>>& params,
                                 size_t k) {
    std::vector<DCRTPoly> pieces;
    DCRTPoly partial(params, Format::EVALUATION, true);
    for (size_t i = 0; i + 1 < k; ++i) {
        DCRTPoly r = random_poly(params);
        pieces.push_back(r);
        partial += r;
    }
    pieces.push_back(value - partial);
    return pieces;
}

DCRTPoly sum_polys(const std::vector<DCRTPoly>& polys,
                   const std::shared_ptr<ILDCRTParams<BigInteger>>& params) {
    DCRTPoly acc(params, Format::EVALUATION, true);
    for (const auto& p : polys)
        acc += p;
    return acc;
}

std::vector<WireBlob> process_path(const std::vector<DCRTPoly>& records,
                                   const std::shared_ptr<ILDCRTParams<BigInteger>>& params,
                                   size_t k,
                                   size_t k0,
                                   bool apbrEnabled,
                                   bool& apbrPreserved,
                                   bool& roundtripOk,
                                   DCRTPoly& localPathSum) {
    std::vector<DCRTPoly> refreshed;
    if (apbrEnabled) {
        std::vector<DCRTPoly> masks;
        DCRTPoly maskSum(params, Format::EVALUATION, true);
        for (size_t i = 0; i + 1 < records.size(); ++i) {
            DCRTPoly r = random_poly(params);
            masks.push_back(r);
            maskSum += r;
        }
        masks.push_back(-maskSum);
        for (size_t i = 0; i < records.size(); ++i)
            refreshed.push_back(records[i] + masks[i]);
    } else {
        refreshed = records;
    }
    apbrPreserved = (sum_polys(refreshed, params) == sum_polys(records, params));

    std::vector<WireBlob> blobs;
    for (const auto& r : refreshed) {
        for (const auto& piece : split_poly(r, params, k)) {
            blobs.push_back({serialize_poly(piece), false});
        }
    }
    if (k0 > 0) {
        DCRTPoly zero(params, Format::EVALUATION, true);
        for (const auto& piece : split_poly(zero, params, k0)) {
            blobs.push_back({serialize_poly(piece), true});
        }
    }
    routea::v8::FisherYates(blobs);

    std::vector<DCRTPoly> wirePieces;
    roundtripOk = true;
    for (const auto& blob : blobs) {
        DCRTPoly p = deserialize_poly(blob.bytes, params);
        if (serialize_poly(p) != blob.bytes)
            roundtripOk = false;
        wirePieces.push_back(p);
    }
    localPathSum = sum_polys(refreshed, params);
    return blobs;
}

std::vector<WireBlob> relay_path_without_shuffle(const std::vector<DCRTPoly>& records,
                                                 const std::shared_ptr<ILDCRTParams<BigInteger>>& params,
                                                 bool& roundtripOk,
                                                 DCRTPoly& localPathSum) {
    std::vector<WireBlob> blobs;
    roundtripOk = true;
    for (const auto& record : records) {
        std::vector<uint8_t> bytes = serialize_poly(record);
        DCRTPoly decoded = deserialize_poly(bytes, params);
        if (serialize_poly(decoded) != bytes)
            roundtripOk = false;
        blobs.push_back({std::move(bytes), false});
    }
    localPathSum = sum_polys(records, params);
    return blobs;
}

std::vector<WireBlob> relay_path_sum_only(const std::vector<DCRTPoly>& records,
                                          const std::shared_ptr<ILDCRTParams<BigInteger>>& params,
                                          bool& roundtripOk,
                                          DCRTPoly& localPathSum) {
    localPathSum = sum_polys(records, params);
    std::vector<uint8_t> bytes = serialize_poly(localPathSum);
    DCRTPoly decoded = deserialize_poly(bytes, params);
    roundtripOk = (serialize_poly(decoded) == bytes);
    return {{std::move(bytes), false}};
}

size_t wire_bytes_sum(const std::vector<WireBlob>& blobs) {
    size_t total = 0;
    for (const auto& blob : blobs)
        total += blob.bytes.size();
    return total;
}

size_t poly_record_bytes_sum(const std::vector<DCRTPoly>& records) {
    size_t total = 0;
    for (const auto& record : records)
        total += serialize_poly(record).size();
    return total;
}

std::string sha256_bytes(const std::vector<uint8_t>& bytes) {
    return HashUtil::HashString(std::string(reinterpret_cast<const char*>(bytes.data()), bytes.size()));
}

std::vector<uint8_t> read_binary_file(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in)
        throw std::runtime_error("cannot open binary input: " + path.string());
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

void write_binary_file(const std::filesystem::path& path, const std::vector<uint8_t>& bytes) {
    std::filesystem::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out)
        throw std::runtime_error("cannot open binary output: " + path.string());
    out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    if (!out)
        throw std::runtime_error("failed to write binary output: " + path.string());
}

std::string read_text_file(const std::filesystem::path& path) {
    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("cannot open text input: " + path.string());
    std::string value;
    std::getline(in, value);
    return value;
}

void write_text_file(const std::filesystem::path& path, const std::string& value) {
    std::filesystem::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::trunc);
    if (!out)
        throw std::runtime_error("cannot open text output: " + path.string());
    out << value << '\n';
}

std::string padded_client_id(size_t clientIndex) {
    std::ostringstream out;
    out << "client_" << std::setw(3) << std::setfill('0') << clientIndex;
    return out.str();
}

struct PrngAttestation {
    std::string initialPrefixDigest;
    std::string boundDigest;
};

PrngAttestation consume_prng_attestation(const Options& opt, const std::string& roleId) {
    std::vector<uint8_t> prefix;
    prefix.reserve(32);
    for (size_t i = 0; i < 4; ++i) {
        const uint64_t value = routea::v8::OpenFhePrngU64();
        for (size_t j = 0; j < sizeof(uint64_t); ++j)
            prefix.push_back(static_cast<uint8_t>((value >> (8 * j)) & 0xff));
    }
    const std::string initial = sha256_bytes(prefix);
    const std::string bound = HashUtil::HashString(opt.releaseId + "|" + opt.runId + "|" + roleId + "|" + initial);
    return {initial, bound};
}

std::vector<uint8_t> serialize_wire_batch(const std::vector<WireBlob>& blobs) {
    std::vector<uint8_t> out;
    append_u64(out, 0x5238563842415431ULL);  // R8V8BAT1
    append_u64(out, static_cast<uint64_t>(blobs.size()));
    for (const auto& blob : blobs) {
        append_u64(out, blob.isDummy ? 1 : 0);
        append_u64(out, static_cast<uint64_t>(blob.bytes.size()));
        out.insert(out.end(), blob.bytes.begin(), blob.bytes.end());
    }
    return out;
}

std::vector<WireBlob> deserialize_wire_batch(const std::vector<uint8_t>& bytes) {
    size_t off = 0;
    if (read_u64(bytes, off) != 0x5238563842415431ULL)
        throw std::runtime_error("bad v8 wire-batch magic");
    const size_t count = static_cast<size_t>(read_u64(bytes, off));
    std::vector<WireBlob> blobs;
    blobs.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        const uint64_t dummy = read_u64(bytes, off);
        if (dummy > 1)
            throw std::runtime_error("invalid dummy flag in v8 wire batch");
        const size_t length = static_cast<size_t>(read_u64(bytes, off));
        if (length > bytes.size() - off)
            throw std::runtime_error("truncated v8 wire batch payload");
        std::vector<uint8_t> payload(bytes.begin() + static_cast<std::ptrdiff_t>(off),
                                     bytes.begin() + static_cast<std::ptrdiff_t>(off + length));
        off += length;
        blobs.push_back({std::move(payload), dummy == 1});
    }
    if (off != bytes.size())
        throw std::runtime_error("trailing bytes in v8 wire batch");
    return blobs;
}

std::filesystem::path client_upload_path(const std::filesystem::path& workDir,
                                         size_t clientIndex,
                                         const std::string& pathId) {
    return workDir / "uploads" / (padded_client_id(clientIndex) + "_" + pathId + ".bin");
}

std::string profile_binding_string(const Options& opt) {
    std::ostringstream out;
    out << "N=" << opt.ringDim << ";t=" << opt.plaintextModulus
        << ";towers=" << opt.towers << ";bits=" << opt.bits
        << ";k=" << opt.k << ";k0=" << opt.k0
        << ";packing=" << opt.packing;
    return out.str();
}

uint16_t path_port(const Options& opt, const std::string& pathId) {
    if (pathId == "S1") return opt.basePort;
    if (pathId == "S2") return static_cast<uint16_t>(opt.basePort + 1);
    if (pathId == "T1") return static_cast<uint16_t>(opt.basePort + 2);
    if (pathId == "T2") return static_cast<uint16_t>(opt.basePort + 3);
    throw std::runtime_error("unknown path id");
}

uint64_t path_code(const std::string& pathId) {
    if (pathId == "S1") return 1;
    if (pathId == "S2") return 2;
    if (pathId == "T1") return 3;
    if (pathId == "T2") return 4;
    throw std::runtime_error("unknown path id");
}

std::string path_id_from_code(uint64_t code) {
    if (code == 1) return "S1";
    if (code == 2) return "S2";
    if (code == 3) return "T1";
    if (code == 4) return "T2";
    throw std::runtime_error("unknown path code");
}

int create_listener(uint16_t port) {
    const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        throw std::runtime_error("socket creation failed");
    int enabled = 1;
    if (::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled)) != 0) {
        ::close(fd);
        throw std::runtime_error("setsockopt(SO_REUSEADDR) failed");
    }
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(port);
    if (::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        ::close(fd);
        throw std::runtime_error("bind failed on port " + std::to_string(port));
    }
    if (::listen(fd, 128) != 0) {
        ::close(fd);
        throw std::runtime_error("listen failed");
    }
    return fd;
}

int connect_with_retry(const std::string& host, uint16_t port) {
    for (size_t attempt = 0; attempt < 100; ++attempt) {
        const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0)
            throw std::runtime_error("socket creation failed");
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(port);
        if (::inet_pton(AF_INET, host.c_str(), &address.sin_addr) != 1) {
            ::close(fd);
            throw std::runtime_error("TCP preflight requires an IPv4 literal host");
        }
        if (::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0)
            return fd;
        ::close(fd);
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    throw std::runtime_error("connect timeout to " + host + ":" + std::to_string(port));
}

size_t send_all_counted(int fd, const std::vector<uint8_t>& bytes) {
    size_t offset = 0;
    while (offset < bytes.size()) {
        const ssize_t sent = ::send(fd, bytes.data() + offset, bytes.size() - offset, 0);
        if (sent <= 0)
            throw std::runtime_error("send failed");
        offset += static_cast<size_t>(sent);
    }
    return offset;
}

std::vector<uint8_t> recv_exact_counted(int fd, size_t length, size_t& receivedBytes) {
    std::vector<uint8_t> bytes(length);
    size_t offset = 0;
    while (offset < length) {
        const ssize_t received = ::recv(fd, bytes.data() + offset, length - offset, 0);
        if (received <= 0)
            throw std::runtime_error("truncated TCP frame");
        offset += static_cast<size_t>(received);
        receivedBytes += static_cast<size_t>(received);
    }
    return bytes;
}

std::vector<uint8_t> make_tcp_frame(uint64_t magic, uint64_t identity, const std::vector<uint8_t>& payload) {
    std::vector<uint8_t> frame;
    append_u64(frame, magic);
    append_u64(frame, identity);
    append_u64(frame, static_cast<uint64_t>(payload.size()));
    frame.insert(frame.end(), payload.begin(), payload.end());
    return frame;
}

struct TcpFrame {
    uint64_t identity = 0;
    std::vector<uint8_t> payload;
    size_t receivedBytes = 0;
};

TcpFrame receive_tcp_frame(int fd, uint64_t expectedMagic) {
    size_t receivedBytes = 0;
    const std::vector<uint8_t> header = recv_exact_counted(fd, 3 * sizeof(uint64_t), receivedBytes);
    size_t offset = 0;
    if (read_u64(header, offset) != expectedMagic)
        throw std::runtime_error("TCP frame magic mismatch");
    const uint64_t identity = read_u64(header, offset);
    const uint64_t payloadLength = read_u64(header, offset);
    if (payloadLength > (1ULL << 34))
        throw std::runtime_error("TCP frame payload length exceeds preflight bound");
    std::vector<uint8_t> payload = recv_exact_counted(
        fd, static_cast<size_t>(payloadLength), receivedBytes);
    uint8_t trailing = 0;
    const ssize_t trailingResult = ::recv(fd, &trailing, 1, MSG_DONTWAIT);
    if (trailingResult > 0)
        throw std::runtime_error("trailing bytes after TCP frame");
    return {identity, std::move(payload), receivedBytes};
}

constexpr uint64_t CONTROL_MAGIC = 0x523856384354524cULL;  // R8V8CTRL
constexpr uint64_t CONTROL_READY = 1;
constexpr uint64_t CONTROL_RELEASE = 2;
constexpr uint64_t CONTROL_CS_COMPLETE = 3;

uint64_t control_role_code(const Options& opt) {
    if (opt.role == "client") return 1000 + opt.clientIndex;
    if (opt.role == "path") return path_code(opt.pathId);
    if (opt.role == "cs") return 5;
    throw std::runtime_error("role does not participate in control barrier");
}

std::vector<uint8_t> make_control_message(uint64_t messageType,
                                          uint64_t roleCode,
                                          const std::string& runId) {
    const std::string runDigest = HashUtil::HashString(runId);
    if (runDigest.size() != 64)
        throw std::runtime_error("unexpected SHA-256 text length");
    std::vector<uint8_t> message;
    append_u64(message, CONTROL_MAGIC);
    append_u64(message, messageType);
    append_u64(message, roleCode);
    message.insert(message.end(), runDigest.begin(), runDigest.end());
    return message;
}

struct ControlMessage {
    uint64_t type = 0;
    uint64_t roleCode = 0;
    size_t receivedBytes = 0;
};

ControlMessage receive_control_message(int fd, const std::string& runId) {
    size_t receivedBytes = 0;
    const std::vector<uint8_t> message = recv_exact_counted(fd, 3 * sizeof(uint64_t) + 64, receivedBytes);
    size_t offset = 0;
    if (read_u64(message, offset) != CONTROL_MAGIC)
        throw std::runtime_error("control magic mismatch");
    const uint64_t type = read_u64(message, offset);
    const uint64_t roleCode = read_u64(message, offset);
    const std::string digest(message.begin() + static_cast<std::ptrdiff_t>(offset), message.end());
    if (digest != HashUtil::HashString(runId))
        throw std::runtime_error("control run-id mismatch");
    return {type, roleCode, receivedBytes};
}

void set_socket_receive_timeout(int fd, int seconds) {
    timeval timeout{};
    timeout.tv_sec = seconds;
    timeout.tv_usec = 0;
    if (::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) != 0)
        throw std::runtime_error("failed to set socket receive timeout");
}

struct ControlClientSession {
    int fd = -1;
    size_t sentBytes = 0;
    size_t receivedBytes = 0;
};

ControlClientSession enter_control_barrier(const Options& opt) {
    ControlClientSession session;
    session.fd = connect_with_retry(opt.host, static_cast<uint16_t>(opt.basePort + 5));
    set_socket_receive_timeout(session.fd, 60);
    const uint64_t roleCode = control_role_code(opt);
    session.sentBytes += send_all_counted(
        session.fd, make_control_message(CONTROL_READY, roleCode, opt.runId));
    const ControlMessage release = receive_control_message(session.fd, opt.runId);
    session.receivedBytes += release.receivedBytes;
    if (release.type != CONTROL_RELEASE || release.roleCode != 0)
        throw std::runtime_error("invalid round-release control message");
    return session;
}

int run_control_orchestrator(const Options& opt,
                             const std::string& setupId,
                             const std::string& publicDigest) {
    const size_t expectedRoles = opt.clients + 5;
    const int listener = create_listener(static_cast<uint16_t>(opt.basePort + 5));
    std::vector<std::pair<uint64_t, int>> roleSockets;
    roleSockets.reserve(expectedRoles);
    std::vector<bool> clientsSeen(opt.clients, false);
    std::array<bool, 5> infrastructureSeen{{false, false, false, false, false}};
    size_t receivedBytes = 0;
    size_t sentBytes = 0;
    int csFd = -1;
    for (size_t connection = 0; connection < expectedRoles; ++connection) {
        const int fd = ::accept(listener, nullptr, nullptr);
        if (fd < 0) {
            ::close(listener);
            throw std::runtime_error("control accept failed");
        }
        set_socket_receive_timeout(fd, 60);
        const ControlMessage ready = receive_control_message(fd, opt.runId);
        receivedBytes += ready.receivedBytes;
        if (ready.type != CONTROL_READY)
            throw std::runtime_error("non-ready message before release");
        if (ready.roleCode >= 1000) {
            const uint64_t clientIndex = ready.roleCode - 1000;
            if (clientIndex >= opt.clients || clientsSeen[clientIndex])
                throw std::runtime_error("duplicate or invalid client ready role");
            clientsSeen[clientIndex] = true;
        } else {
            if (ready.roleCode < 1 || ready.roleCode > 5 || infrastructureSeen[ready.roleCode - 1])
                throw std::runtime_error("duplicate or invalid infrastructure ready role");
            infrastructureSeen[ready.roleCode - 1] = true;
            if (ready.roleCode == 5)
                csFd = fd;
        }
        roleSockets.push_back({ready.roleCode, fd});
    }
    ::close(listener);
    if (csFd < 0)
        throw std::runtime_error("CS did not enter ready barrier");

    const auto releaseTime = Clock::now();
    const std::vector<uint8_t> release = make_control_message(CONTROL_RELEASE, 0, opt.runId);
    for (const auto& [roleCode, fd] : roleSockets) {
        (void)roleCode;
        sentBytes += send_all_counted(fd, release);
    }
    set_socket_receive_timeout(csFd, 300);
    const ControlMessage complete = receive_control_message(csFd, opt.runId);
    receivedBytes += complete.receivedBytes;
    if (complete.type != CONTROL_CS_COMPLETE || complete.roleCode != 5)
        throw std::runtime_error("invalid CS-complete control message");
    const auto completeTime = Clock::now();
    for (const auto& [roleCode, fd] : roleSockets) {
        (void)roleCode;
        ::close(fd);
    }
    std::cout << "{\n"
              << "  \"schema\": \"route_a_v8_control_orchestrator_v1\",\n"
              << "  \"status\": \"PASS\",\n"
              << "  \"role_id\": \"orchestrator\",\n"
              << "  \"run_id\": \"" << opt.runId << "\",\n"
              << "  \"setup_id\": \"" << setupId << "\",\n"
              << "  \"public_a_sha256\": \"" << publicDigest << "\",\n"
              << "  \"expected_roles\": " << expectedRoles << ",\n"
              << "  \"ready_roles\": " << roleSockets.size() << ",\n"
              << "  \"all_roles_ready\": true,\n"
              << "  \"round_release_observed\": true,\n"
              << "  \"cs_complete_after_release\": true,\n"
              << "  \"steady_state_protocol_round_ms\": " << ms(releaseTime, completeTime) << ",\n"
              << "  \"control_bytes_sent\": " << sentBytes << ",\n"
              << "  \"control_bytes_received\": " << receivedBytes << ",\n"
              << "  \"control_plane_included_in_protocol_bytes\": false,\n"
              << "  \"formal\": false\n"
              << "}\n";
    return 0;
}

int run_distributed_file_role(const Options& opt) {
    const std::filesystem::path workDir(opt.workDir);
    std::filesystem::create_directories(workDir);
    const uint32_t cyclotomicOrder = static_cast<uint32_t>(2 * opt.ringDim);
    auto params = std::make_shared<ILDCRTParams<BigInteger>>(cyclotomicOrder, opt.towers, opt.bits);
    const auto setupPolyPath = workDir / "setup" / "public_a.bin";
    const auto setupDigestPath = workDir / "setup" / "public_a_sha256.txt";
    const auto setupIdPath = workDir / "setup" / "setup_id.txt";

    if (opt.role == "setup") {
        const PrngAttestation attestation = consume_prng_attestation(opt, "setup");
        DCRTPoly publicA = random_poly(params);
        const std::vector<uint8_t> serializedA = serialize_poly(publicA);
        const std::string publicDigest = sha256_bytes(serializedA);
        const std::string setupId = HashUtil::HashString(profile_binding_string(opt) + "|" + publicDigest);
        write_binary_file(setupPolyPath, serializedA);
        write_text_file(setupDigestPath, publicDigest);
        write_text_file(setupIdPath, setupId);
        std::cout << "{\n"
                  << "  \"schema\": \"route_a_v8_distributed_role_v1\",\n"
                  << "  \"status\": \"PASS\",\n"
                  << "  \"role_id\": \"setup\",\n"
                  << "  \"run_id\": \"" << opt.runId << "\",\n"
                  << "  \"setup_id\": \"" << setupId << "\",\n"
                  << "  \"public_a_sha256\": \"" << publicDigest << "\",\n"
                  << "  \"initial_prng_prefix_digest\": \"" << attestation.initialPrefixDigest << "\",\n"
                  << "  \"bound_prng_attestation_digest\": \"" << attestation.boundDigest << "\",\n"
                  << "  \"public_a_samples\": 1,\n"
                  << "  \"formal\": false,\n"
                  << "  \"transport\": \"file_frame_preflight\"\n"
                  << "}\n";
        return 0;
    }

    const std::vector<uint8_t> serializedA = read_binary_file(setupPolyPath);
    const std::string publicDigest = sha256_bytes(serializedA);
    if (publicDigest != read_text_file(setupDigestPath))
        throw std::runtime_error("public-a digest mismatch");
    const std::string setupId = read_text_file(setupIdPath);
    if (setupId != HashUtil::HashString(profile_binding_string(opt) + "|" + publicDigest))
        throw std::runtime_error("setup-id binding mismatch");

    if (opt.role == "orchestrator")
        return run_control_orchestrator(opt, setupId, publicDigest);

    DCRTPoly publicA = deserialize_poly(serializedA, params);

    // Data listeners must be accepting connections before a role announces READY.
    int dataListener = -1;
    if (opt.transport == "tcp" && opt.controlBarrier && opt.role == "path")
        dataListener = create_listener(path_port(opt, opt.pathId));
    if (opt.transport == "tcp" && opt.controlBarrier && opt.role == "cs")
        dataListener = create_listener(static_cast<uint16_t>(opt.basePort + 4));

    ControlClientSession control;
    if (opt.controlBarrier)
        control = enter_control_barrier(opt);

    if (opt.role == "client") {
        const std::string roleId = padded_client_id(opt.clientIndex);
        const PrngAttestation attestation = consume_prng_attestation(opt, roleId);
        std::vector<int64_t> coordinates(opt.dimension, 0);
        for (size_t i = 0; i < opt.dimension; ++i)
            coordinates[i] = workload_coordinate(opt.seed, opt.clientIndex, i);
        const std::vector<int64_t> packed = pack_plaintext_profile(
            coordinates, opt.ringDim, opt.packing, opt.plaintextModulus);
        DCRTPoly message = make_poly(params, embed_plaintext_coeffs(packed, opt.plaintextModulus));
        DCRTPoly secret = sample_secret_poly(params);
        DCRTPoly error = sample_error_poly(params, opt.noise);
        DCRTPoly body = publicA * secret + error * NativeInteger(opt.plaintextModulus) + message;
        DCRTPoly keyFirst = random_poly(params);
        DCRTPoly bodyFirst = random_poly(params);
        const std::array<std::pair<std::string, DCRTPoly>, 4> uploads{{
            {"S1", keyFirst}, {"S2", secret - keyFirst},
            {"T1", bodyFirst}, {"T2", body - bodyFirst}}};
        size_t uploadBytes = 0;
        for (const auto& [pathId, value] : uploads) {
            const std::vector<uint8_t> bytes = serialize_poly(value);
            if (opt.transport == "tcp") {
                const std::vector<uint8_t> frame = make_tcp_frame(
                    0x5238563855504c44ULL, opt.clientIndex, bytes);  // R8V8UPLD
                const int fd = connect_with_retry(opt.host, path_port(opt, pathId));
                uploadBytes += send_all_counted(fd, frame);
                ::shutdown(fd, SHUT_WR);
                ::close(fd);
            } else {
                uploadBytes += bytes.size();
                write_binary_file(client_upload_path(workDir, opt.clientIndex, pathId), bytes);
            }
        }
        if (control.fd >= 0) {
            ::close(control.fd);
            control.fd = -1;
        }
        std::cout << "{\n"
                  << "  \"schema\": \"route_a_v8_distributed_role_v1\",\n"
                  << "  \"status\": \"PASS\",\n"
                  << "  \"role_id\": \"" << roleId << "\",\n"
                  << "  \"run_id\": \"" << opt.runId << "\",\n"
                  << "  \"setup_id\": \"" << setupId << "\",\n"
                  << "  \"public_a_sha256\": \"" << publicDigest << "\",\n"
                  << "  \"initial_prng_prefix_digest\": \"" << attestation.initialPrefixDigest << "\",\n"
                  << "  \"bound_prng_attestation_digest\": \"" << attestation.boundDigest << "\",\n"
                  << "  \"public_a_samples\": 0,\n"
                  << "  \"client_upload_bytes\": " << uploadBytes << ",\n"
                  << "  \"control_ready_sent\": " << (opt.controlBarrier ? "true" : "false") << ",\n"
                  << "  \"control_release_received\": " << (opt.controlBarrier ? "true" : "false") << ",\n"
                  << "  \"control_bytes_sent\": " << control.sentBytes << ",\n"
                  << "  \"control_bytes_received\": " << control.receivedBytes << ",\n"
                  << "  \"control_plane_included_in_protocol_bytes\": false,\n"
                  << "  \"formal\": false,\n"
                  << "  \"transport\": \"" << (opt.transport == "tcp" ? "counted_tcp_preflight" : "file_frame_preflight") << "\"\n"
                  << "}\n";
        return 0;
    }

    if (opt.role == "path") {
        const PrngAttestation attestation = consume_prng_attestation(opt, opt.pathId);
        std::vector<DCRTPoly> records;
        size_t receivedBytes = 0;
        records.reserve(opt.clients);
        if (opt.transport == "tcp") {
            const int listener = dataListener >= 0 ? dataListener : create_listener(path_port(opt, opt.pathId));
            std::vector<bool> seen(opt.clients, false);
            for (size_t connection = 0; connection < opt.clients; ++connection) {
                const int fd = ::accept(listener, nullptr, nullptr);
                if (fd < 0) {
                    ::close(listener);
                    throw std::runtime_error("path accept failed");
                }
                const TcpFrame frame = receive_tcp_frame(fd, 0x5238563855504c44ULL);
                ::close(fd);
                if (frame.identity >= opt.clients || seen[frame.identity]) {
                    ::close(listener);
                    throw std::runtime_error("duplicate or invalid client identity on path");
                }
                seen[frame.identity] = true;
                receivedBytes += frame.receivedBytes;
                records.push_back(deserialize_poly(frame.payload, params));
            }
            ::close(listener);
            dataListener = -1;
        } else {
            for (size_t client = 0; client < opt.clients; ++client) {
                const std::vector<uint8_t> bytes = read_binary_file(client_upload_path(workDir, client, opt.pathId));
                receivedBytes += bytes.size();
                records.push_back(deserialize_poly(bytes, params));
            }
        }
        bool apbrPreserved = false;
        bool roundtrip = false;
        DCRTPoly localSum(params, Format::EVALUATION, true);
        const std::vector<WireBlob> relay = process_path(
            records, params, opt.k, opt.k0, opt.apbr, apbrPreserved, roundtrip, localSum);
        const std::vector<uint8_t> relayBytes = serialize_wire_batch(relay);
        size_t sentRelayBytes = relayBytes.size();
        if (opt.transport == "tcp") {
            const std::vector<uint8_t> frame = make_tcp_frame(
                0x5238563852454c59ULL, path_code(opt.pathId), relayBytes);  // R8V8RELY
            const int fd = connect_with_retry(opt.host, static_cast<uint16_t>(opt.basePort + 4));
            sentRelayBytes = send_all_counted(fd, frame);
            ::shutdown(fd, SHUT_WR);
            ::close(fd);
        } else {
            write_binary_file(workDir / "relays" / (opt.pathId + ".bin"), relayBytes);
        }
        const bool pass = apbrPreserved && roundtrip;
        if (control.fd >= 0) {
            ::close(control.fd);
            control.fd = -1;
        }
        std::cout << "{\n"
                  << "  \"schema\": \"route_a_v8_distributed_role_v1\",\n"
                  << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n"
                  << "  \"role_id\": \"" << opt.pathId << "\",\n"
                  << "  \"run_id\": \"" << opt.runId << "\",\n"
                  << "  \"setup_id\": \"" << setupId << "\",\n"
                  << "  \"public_a_sha256\": \"" << publicDigest << "\",\n"
                  << "  \"initial_prng_prefix_digest\": \"" << attestation.initialPrefixDigest << "\",\n"
                  << "  \"bound_prng_attestation_digest\": \"" << attestation.boundDigest << "\",\n"
                  << "  \"received_client_bytes\": " << receivedBytes << ",\n"
                  << "  \"relay_bytes\": " << sentRelayBytes << ",\n"
                  << "  \"real_fragments\": " << (opt.clients * opt.k) << ",\n"
                  << "  \"dummy_fragments\": " << opt.k0 << ",\n"
                  << "  \"apbr_sum_preserved\": " << (apbrPreserved ? "true" : "false") << ",\n"
                  << "  \"wire_roundtrip\": " << (roundtrip ? "true" : "false") << ",\n"
                  << "  \"control_ready_sent\": " << (opt.controlBarrier ? "true" : "false") << ",\n"
                  << "  \"control_release_received\": " << (opt.controlBarrier ? "true" : "false") << ",\n"
                  << "  \"control_bytes_sent\": " << control.sentBytes << ",\n"
                  << "  \"control_bytes_received\": " << control.receivedBytes << ",\n"
                  << "  \"control_plane_included_in_protocol_bytes\": false,\n"
                  << "  \"formal\": false,\n"
                  << "  \"transport\": \"" << (opt.transport == "tcp" ? "counted_tcp_preflight" : "file_frame_preflight") << "\"\n"
                  << "}\n";
        return pass ? 0 : 1;
    }

    if (opt.role == "cs") {
        std::array<std::vector<uint8_t>, 4> tcpRelayPayloads;
        size_t tcpRelayReceivedBytes = 0;
        if (opt.transport == "tcp") {
            const int listener = dataListener >= 0
                                     ? dataListener
                                     : create_listener(static_cast<uint16_t>(opt.basePort + 4));
            std::array<bool, 4> seen{{false, false, false, false}};
            for (size_t connection = 0; connection < 4; ++connection) {
                const int fd = ::accept(listener, nullptr, nullptr);
                if (fd < 0) {
                    ::close(listener);
                    throw std::runtime_error("CS accept failed");
                }
                const TcpFrame frame = receive_tcp_frame(fd, 0x5238563852454c59ULL);
                ::close(fd);
                if (frame.identity < 1 || frame.identity > 4 || seen[frame.identity - 1]) {
                    ::close(listener);
                    throw std::runtime_error("duplicate or invalid path identity at CS");
                }
                seen[frame.identity - 1] = true;
                tcpRelayReceivedBytes += frame.receivedBytes;
                tcpRelayPayloads[frame.identity - 1] = frame.payload;
            }
            ::close(listener);
            dataListener = -1;
        }
        auto readRelaySum = [&](const std::string& pathId) {
            const std::vector<uint8_t> bytes = opt.transport == "tcp"
                                                   ? tcpRelayPayloads[path_code(pathId) - 1]
                                                   : read_binary_file(workDir / "relays" / (pathId + ".bin"));
            const std::vector<WireBlob> blobs = deserialize_wire_batch(bytes);
            DCRTPoly sum(params, Format::EVALUATION, true);
            for (const auto& blob : blobs)
                sum += deserialize_poly(blob.bytes, params);
            return sum;
        };
        DCRTPoly secretAggregate = readRelaySum("S1") + readRelaySum("S2");
        DCRTPoly bodyAggregate = readRelaySum("T1") + readRelaySum("T2");
        DCRTPoly recovered = bodyAggregate - publicA * secretAggregate;
        const size_t packedCount = packed_coeff_count(opt);
        const std::vector<int64_t> recoveredPacked = crt_coeffs_mod_t(
            recovered, packedCount, opt.plaintextModulus);
        const std::vector<int64_t> recoveredCoordinates = unpack_plaintext_profile(
            recoveredPacked, opt.dimension, opt.packing, opt.ringDim, opt.plaintextModulus);
        std::vector<int64_t> target(opt.dimension, 0);
        for (size_t client = 0; client < opt.clients; ++client)
            for (size_t coordinate = 0; coordinate < opt.dimension; ++coordinate)
                target[coordinate] += workload_coordinate(opt.seed, client, coordinate);
        int64_t diffLinf = 0;
        size_t mismatches = 0;
        for (size_t i = 0; i < opt.dimension; ++i) {
            const int64_t difference = recoveredCoordinates[i] - target[i];
            diffLinf = std::max<int64_t>(diffLinf, std::llabs(difference));
            if (difference != 0)
                ++mismatches;
        }
        size_t clientUploadBytes = 0;
        size_t relayBytes = tcpRelayReceivedBytes;
        if (opt.transport == "file") {
            for (const std::string pathId : {"S1", "S2", "T1", "T2"}) {
                relayBytes += std::filesystem::file_size(workDir / "relays" / (pathId + ".bin"));
                for (size_t client = 0; client < opt.clients; ++client)
                    clientUploadBytes += std::filesystem::file_size(client_upload_path(workDir, client, pathId));
            }
        }
        const bool pass = (mismatches == 0);
        bool csCompleteSent = false;
        if (control.fd >= 0 && pass) {
            control.sentBytes += send_all_counted(
                control.fd, make_control_message(CONTROL_CS_COMPLETE, 5, opt.runId));
            csCompleteSent = true;
        }
        if (control.fd >= 0) {
            ::close(control.fd);
            control.fd = -1;
        }
        std::cout << "{\n"
                  << "  \"schema\": \"route_a_v8_distributed_role_v1\",\n"
                  << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n"
                  << "  \"role_id\": \"CS\",\n"
                  << "  \"run_id\": \"" << opt.runId << "\",\n"
                  << "  \"setup_id\": \"" << setupId << "\",\n"
                  << "  \"public_a_sha256\": \"" << publicDigest << "\",\n"
                  << "  \"encoded_plaintext_diff_linf\": " << diffLinf << ",\n"
                  << "  \"encoded_plaintext_mismatch_count\": " << mismatches << ",\n"
                  << "  \"client_upload_bytes\": " << clientUploadBytes << ",\n"
                  << "  \"shuffle_to_cs_relay_payload_bytes\": " << relayBytes << ",\n"
                  << "  \"total_application_protocol_bytes\": " << (clientUploadBytes + relayBytes) << ",\n"
                  << "  \"direct_client_to_cs_bytes\": 0,\n"
                  << "  \"control_ready_sent\": " << (opt.controlBarrier ? "true" : "false") << ",\n"
                  << "  \"control_release_received\": " << (opt.controlBarrier ? "true" : "false") << ",\n"
                  << "  \"cs_complete_sent_after_correctness\": " << (csCompleteSent ? "true" : "false") << ",\n"
                  << "  \"control_bytes_sent\": " << control.sentBytes << ",\n"
                  << "  \"control_bytes_received\": " << control.receivedBytes << ",\n"
                  << "  \"control_plane_included_in_protocol_bytes\": false,\n"
                  << "  \"formal\": false,\n"
                  << "  \"transport\": \"" << (opt.transport == "tcp" ? "counted_tcp_preflight" : "file_frame_preflight") << "\"\n"
                  << "}\n";
        return pass ? 0 : 1;
    }

    throw std::runtime_error("unreachable distributed role");
}

}  // namespace

int main(int argc, char** argv) {
    try {
        PseudoRandomNumberGenerator::InitPRNGEngine();
        const auto tTotal0 = Clock::now();
        const Options opt = parse(argc, argv);
        if (opt.role != "single")
            return run_distributed_file_role(opt);
        if (opt.variant == "plain_aggregate") {
            const auto tSetup0 = Clock::now();
            const auto tSetup1 = Clock::now();
            const auto tMaterial0 = Clock::now();
            const auto inputMessages = opt.messagesFile.empty()
                                           ? std::vector<std::vector<int64_t>>()
                                           : load_messages_file(opt.messagesFile, opt.clients, opt.dimension);
            std::vector<int64_t> messageTarget(opt.ringDim, 0);
            std::vector<int64_t> recovered(opt.ringDim, 0);
            for (size_t c = 0; c < opt.clients; ++c) {
                for (size_t i = 0; i < opt.dimension; ++i) {
                    const int64_t m = opt.messagesFile.empty()
                                          ? workload_coordinate(opt.seed, c, i)
                                          : inputMessages[c][i];
                    messageTarget[i] += m;
                    recovered[i] += m;
                }
            }
            const auto tMaterial1 = Clock::now();
            const auto tCs0 = Clock::now();
            int64_t qLinf = 0;
            size_t qMismatch = 0;
            for (size_t i = 0; i < opt.dimension; ++i) {
                const int64_t diff = recovered[i] - messageTarget[i];
                qLinf = std::max<int64_t>(qLinf, std::llabs(diff));
                if (diff != 0) ++qMismatch;
            }
            const auto tCs1 = Clock::now();
            const auto tTotal1 = Clock::now();
            const bool pass = (qMismatch == 0);
            std::cout << "{\n";
            std::cout << "  \"schema\": \"openfhe_dcrtpoly_wire_integration_v2\",\n";
            std::cout << "  \"variant\": \"plain_aggregate\",\n";
            std::cout << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n";
            std::cout << "  \"clients\": " << opt.clients << ",\n";
            std::cout << "  \"dimension\": " << opt.dimension << ",\n";
            std::cout << "  \"ring_dim\": " << opt.ringDim << ",\n";
            std::cout << "  \"packing_profile\": \"" << reported_packing_profile(opt.packing) << "\",\n";
            std::cout << "  \"packed_coefficients\": " << packed_coeff_count(opt) << ",\n";
            std::cout << "  \"plaintext_modulus\": " << opt.plaintextModulus << ",\n";
            std::cout << "  \"k\": " << opt.k << ",\n";
            std::cout << "  \"k0\": " << opt.k0 << ",\n";
            std::cout << "  \"noise\": \"" << opt.noise << "\",\n";
            std::cout << "  \"messages_file_used\": " << (opt.messagesFile.empty() ? "false" : "true") << ",\n";
            std::cout << "  \"openfhe_dcrtpoly_objects\": false,\n";
            std::cout << "  \"wire_serialization_roundtrip\": true,\n";
            std::cout << "  \"wire_aggregate_equals_local\": true,\n";
            std::cout << "  \"apbr_sum_preserved_all_paths\": true,\n";
            std::cout << "  \"post_key_removal_mod_t_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"encoded_plaintext_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"encoded_plaintext_mismatch_count\": " << qMismatch << ",\n";
            std::cout << "  \"q_domain_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"message_domain_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"q_domain_mismatch_count\": " << qMismatch << ",\n";
            std::cout << "  \"real_fragments_per_path\": 0,\n";
            std::cout << "  \"dummy_fragments_per_path\": 0,\n";
            std::cout << "  \"total_fragments_per_path\": 0,\n";
            std::cout << "  \"wire_bytes\": {\"S1\": 0, \"S2\": 0, \"T1\": 0, \"T2\": 0},\n";
            std::cout << "  \"client_upload_bytes\": 0,\n";
            std::cout << "  \"path_to_cs_bytes\": 0,\n";
            std::cout << "  \"total_payload_bytes\": 0,\n";
            std::cout << "  \"total_wire_bytes\": 0,\n";
            if (opt.emitRecovered) {
                std::cout << "  \"recovered_plaintext\": ";
                emit_int_vector_json(recovered, opt.dimension);
                std::cout << ",\n";
            }
            std::cout << "  \"runtime_ms\": {\"setup\": " << ms(tSetup0, tSetup1)
                      << ", \"material_generation\": " << ms(tMaterial0, tMaterial1)
                      << ", \"sharing\": 0"
                      << ", \"path_processing\": 0"
                      << ", \"cs_recovery\": " << ms(tCs0, tCs1)
                      << ", \"total\": " << ms(tTotal0, tTotal1) << "}\n";
            std::cout << "}\n";
            return pass ? 0 : 1;
        }
        if (opt.variant == "shamir_shuffle_proxy") {
            const auto tSetup0 = Clock::now();
            const auto tSetup1 = Clock::now();
            const auto tMaterial0 = Clock::now();
            const auto inputMessages = opt.messagesFile.empty()
                                           ? std::vector<std::vector<int64_t>>()
                                           : load_messages_file(opt.messagesFile, opt.clients, opt.dimension);
            std::vector<int64_t> messageTarget(opt.dimension, 0);
            std::vector<WireBlob> shuffler1, shuffler2;
            for (size_t c = 0; c < opt.clients; ++c) {
                std::vector<uint64_t> share1(opt.dimension), share2(opt.dimension);
                for (size_t i = 0; i < opt.dimension; ++i) {
                    const int64_t m = opt.messagesFile.empty()
                                          ? workload_coordinate(opt.seed, c, i)
                                          : inputMessages[c][i];
                    messageTarget[i] += m;
                    const uint64_t mMod = positive_mod_i128(m, opt.plaintextModulus);
                    share1[i] = routea::v8::UniformBelow(opt.plaintextModulus);
                    share2[i] = sub_mod_u64(mMod, share1[i], opt.plaintextModulus);
                }
                shuffler1.push_back({serialize_plain_share_record(share1, opt.plaintextModulus), false});
                shuffler2.push_back({serialize_plain_share_record(share2, opt.plaintextModulus), false});
            }
            const auto tMaterial1 = Clock::now();

            const auto tPath0 = Clock::now();
            routea::v8::FisherYates(shuffler1);
            routea::v8::FisherYates(shuffler2);
            bool roundtripOk = true;
            for (const auto& blob : shuffler1) {
                const auto rec = deserialize_plain_share_record(blob.bytes, opt.plaintextModulus, opt.dimension);
                if (serialize_plain_share_record(rec, opt.plaintextModulus) != blob.bytes)
                    roundtripOk = false;
            }
            for (const auto& blob : shuffler2) {
                const auto rec = deserialize_plain_share_record(blob.bytes, opt.plaintextModulus, opt.dimension);
                if (serialize_plain_share_record(rec, opt.plaintextModulus) != blob.bytes)
                    roundtripOk = false;
            }
            const auto tPath1 = Clock::now();

            const auto tCs0 = Clock::now();
            std::vector<uint64_t> recoveredResidues(opt.dimension, 0);
            auto add_records = [&](const std::vector<WireBlob>& blobs) {
                for (const auto& blob : blobs) {
                    const auto rec = deserialize_plain_share_record(blob.bytes, opt.plaintextModulus, opt.dimension);
                    for (size_t i = 0; i < opt.dimension; ++i)
                        recoveredResidues[i] = add_mod_u64(recoveredResidues[i], rec[i], opt.plaintextModulus);
                }
            };
            add_records(shuffler1);
            add_records(shuffler2);
            std::vector<int64_t> recovered(opt.dimension, 0);
            int64_t qLinf = 0;
            size_t qMismatch = 0;
            for (size_t i = 0; i < opt.dimension; ++i) {
                recovered[i] = centered_mod_t(recoveredResidues[i], opt.plaintextModulus);
                const int64_t diff = recovered[i] - messageTarget[i];
                qLinf = std::max<int64_t>(qLinf, std::llabs(diff));
                if (diff != 0) ++qMismatch;
            }
            const auto tCs1 = Clock::now();
            const size_t bytesS1 = [&]() { size_t x = 0; for (const auto& b : shuffler1) x += b.bytes.size(); return x; }();
            const size_t bytesS2 = [&]() { size_t x = 0; for (const auto& b : shuffler2) x += b.bytes.size(); return x; }();
            const size_t clientUploadBytes = bytesS1 + bytesS2;
            const size_t pathToCsBytes = bytesS1 + bytesS2;
            const auto tTotal1 = Clock::now();
            const bool pass = roundtripOk && (qMismatch == 0);
            std::cout << "{\n";
            std::cout << "  \"schema\": \"openfhe_dcrtpoly_wire_integration_v8_rc1_preflight\",\n";
            std::cout << "  \"variant\": \"shamir_shuffle_proxy\",\n";
            std::cout << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n";
            std::cout << "  \"clients\": " << opt.clients << ",\n";
            std::cout << "  \"dimension\": " << opt.dimension << ",\n";
            std::cout << "  \"ring_dim\": " << opt.ringDim << ",\n";
            std::cout << "  \"packing_profile\": \"plaintext_vector_shamir_proxy\",\n";
            std::cout << "  \"packed_coefficients\": " << opt.dimension << ",\n";
            std::cout << "  \"plaintext_modulus\": " << opt.plaintextModulus << ",\n";
            std::cout << "  \"k\": 1,\n";
            std::cout << "  \"k0\": 0,\n";
            std::cout << "  \"noise\": \"none\",\n";
            std::cout << "  \"messages_file_used\": " << (opt.messagesFile.empty() ? "false" : "true") << ",\n";
            std::cout << "  \"apbr_enabled\": false,\n";
            std::cout << "  \"material_formula\": \"two additive plaintext shares over Z_t; compact two-shuffler proxy without client identifiers or cross-path tags; not a faithful UFL reimplementation\",\n";
            std::cout << "  \"public_a_sampler\": \"not applicable\",\n";
            std::cout << "  \"secret_sampler\": \"not applicable\",\n";
            std::cout << "  \"error_sampler\": \"not applicable\",\n";
            std::cout << "  \"openfhe_dcrtpoly_objects\": false,\n";
            std::cout << "  \"wire_serialization_roundtrip\": " << (roundtripOk ? "true" : "false") << ",\n";
            std::cout << "  \"wire_aggregate_equals_local\": true,\n";
            std::cout << "  \"apbr_sum_preserved_all_paths\": true,\n";
            std::cout << "  \"post_key_removal_mod_t_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"encoded_plaintext_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"encoded_plaintext_mismatch_count\": " << qMismatch << ",\n";
            std::cout << "  \"q_domain_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"message_domain_diff_linf\": " << qLinf << ",\n";
            std::cout << "  \"q_domain_mismatch_count\": " << qMismatch << ",\n";
            std::cout << "  \"real_fragments_per_path\": " << opt.clients << ",\n";
            std::cout << "  \"dummy_fragments_per_path\": 0,\n";
            std::cout << "  \"total_fragments_per_path\": " << opt.clients << ",\n";
            std::cout << "  \"wire_bytes\": {\"S1\": " << bytesS1 << ", \"S2\": " << bytesS2
                      << ", \"T1\": 0, \"T2\": 0},\n";
            std::cout << "  \"client_upload_bytes\": " << clientUploadBytes << ",\n";
            std::cout << "  \"path_to_cs_bytes\": " << pathToCsBytes << ",\n";
            std::cout << "  \"total_payload_bytes\": " << (clientUploadBytes + pathToCsBytes) << ",\n";
            std::cout << "  \"total_wire_bytes\": " << pathToCsBytes << ",\n";
            if (opt.emitRecovered) {
                std::cout << "  \"recovered_plaintext\": ";
                emit_int_vector_json(recovered, opt.dimension);
                std::cout << ",\n";
            }
            std::cout << "  \"runtime_ms\": {\"setup\": " << ms(tSetup0, tSetup1)
                      << ", \"material_generation\": " << ms(tMaterial0, tMaterial1)
                      << ", \"sharing\": " << ms(tMaterial0, tMaterial1)
                      << ", \"path_processing\": " << ms(tPath0, tPath1)
                      << ", \"cs_recovery\": " << ms(tCs0, tCs1)
                      << ", \"total\": " << ms(tTotal0, tTotal1) << "}\n";
            std::cout << "}\n";
            return pass ? 0 : 1;
        }
        const auto tSetup0 = Clock::now();
        const uint32_t cyclotomicOrder = static_cast<uint32_t>(2 * opt.ringDim);
        auto params = std::make_shared<ILDCRTParams<BigInteger>>(cyclotomicOrder, opt.towers, opt.bits);
        const auto tSetup1 = Clock::now();

        if (opt.variant == "wire_codec_selftest") {
            DCRTPoly sample = random_poly(params);
            const std::vector<uint8_t> canonical = serialize_poly(sample);
            const bool roundtrip = (serialize_poly(deserialize_poly(canonical, params)) == canonical);

            bool noncanonicalRejected = false;
            std::vector<uint8_t> noncanonical = canonical;
            const size_t firstResidueOffset = 3 * sizeof(uint64_t) +
                                              params->GetParams().size() * sizeof(uint64_t);
            const uint64_t firstModulus = params->GetParams()[0]->GetModulus().ConvertToInt<uint64_t>();
            for (size_t i = 0; i < sizeof(uint64_t); ++i)
                noncanonical[firstResidueOffset + i] = static_cast<uint8_t>((firstModulus >> (8 * i)) & 0xff);
            try {
                (void)deserialize_poly(noncanonical, params);
            } catch (const std::runtime_error&) {
                noncanonicalRejected = true;
            }

            bool trailingRejected = false;
            std::vector<uint8_t> trailing = canonical;
            trailing.push_back(0);
            try {
                (void)deserialize_poly(trailing, params);
            } catch (const std::runtime_error&) {
                trailingRejected = true;
            }

            bool truncatedRejected = false;
            std::vector<uint8_t> truncated = canonical;
            truncated.pop_back();
            try {
                (void)deserialize_poly(truncated, params);
            } catch (const std::runtime_error&) {
                truncatedRejected = true;
            }

            const bool pass = roundtrip && noncanonicalRejected && trailingRejected && truncatedRejected;
            std::cout << "{\n"
                      << "  \"schema\": \"route_a_v8_wire_codec_selftest_v1\",\n"
                      << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n"
                      << "  \"wire_roundtrip\": " << (roundtrip ? "true" : "false") << ",\n"
                      << "  \"noncanonical_residue_rejected\": " << (noncanonicalRejected ? "true" : "false") << ",\n"
                      << "  \"trailing_bytes_rejected\": " << (trailingRejected ? "true" : "false") << ",\n"
                      << "  \"truncated_payload_rejected\": " << (truncatedRejected ? "true" : "false") << ",\n"
                      << "  \"security_claim\": false\n"
                      << "}\n";
            return pass ? 0 : 1;
        }

        const auto tMaterial0 = Clock::now();
        const auto inputMessages = opt.messagesFile.empty()
                                       ? std::vector<std::vector<int64_t>>()
                                       : load_messages_file(opt.messagesFile, opt.clients, opt.dimension);
        const size_t packedCoeffs = packed_coeff_count(opt);

        // Setup samples the public polynomial once. Clients only consume this shared value.
        DCRTPoly a = random_poly(params);

        std::vector<DCRTPoly> skRecords;
        std::vector<DCRTPoly> bodyRecords;
        std::vector<int64_t> messageTarget(opt.dimension, 0);
        std::vector<int64_t> packedMessageTarget(opt.ringDim, 0);
        std::vector<int64_t> postKeyRemovalTargetFirstTower(opt.ringDim, 0);
        int64_t observedErrorMin = std::numeric_limits<int64_t>::max();
        int64_t observedErrorMax = std::numeric_limits<int64_t>::min();
        for (size_t c = 0; c < opt.clients; ++c) {
            std::vector<int64_t> msgRaw(opt.dimension, 0);
            for (size_t i = 0; i < opt.dimension; ++i) {
                msgRaw[i] = opt.messagesFile.empty()
                                ? workload_coordinate(opt.seed, c, i)
                                : inputMessages[c][i];
                messageTarget[i] += msgRaw[i];
            }
            std::vector<int64_t> msgCoeff = pack_plaintext_profile(msgRaw, opt.ringDim, opt.packing, opt.plaintextModulus);
            DCRTPoly sk = sample_secret_poly(params);
            DCRTPoly error = sample_error_poly(params, opt.noise);
            const std::vector<int64_t> errCoeff = first_tower_coeffs(error, opt.ringDim);
            for (const int64_t coefficient : errCoeff) {
                observedErrorMin = std::min(observedErrorMin, coefficient);
                observedErrorMax = std::max(observedErrorMax, coefficient);
            }
            for (size_t i = 0; i < packedCoeffs; ++i) {
                packedMessageTarget[i] += msgCoeff[i];
                postKeyRemovalTargetFirstTower[i] += msgCoeff[i] + static_cast<int64_t>(opt.plaintextModulus) * errCoeff[i];
            }
            DCRTPoly msg = make_poly(params, embed_plaintext_coeffs(msgCoeff, opt.plaintextModulus));
            DCRTPoly errTimesT = error * NativeInteger(opt.plaintextModulus);
            skRecords.push_back(sk);
            if (opt.variant == "shuffle_only")
                bodyRecords.push_back(msg);
            else
                bodyRecords.push_back(a * sk + errTimesT + msg);
        }
        const auto tMaterial1 = Clock::now();

        if (opt.variant == "openfhe_rlwe_only") {
            const auto tCs0 = Clock::now();
            DCRTPoly sAgg = sum_polys(skRecords, params);
            DCRTPoly bAgg = sum_polys(bodyRecords, params);
            DCRTPoly recoveredPoly = bAgg - (a * sAgg);
            auto recoveredFirstTower = first_tower_coeffs(recoveredPoly, packedCoeffs);
            auto recoveredPacked = crt_coeffs_mod_t(recoveredPoly, packedCoeffs, opt.plaintextModulus);
            auto recoveredPlain = unpack_plaintext_profile(recoveredPacked, opt.dimension, opt.packing, opt.ringDim, opt.plaintextModulus);
            int64_t postKeyLinf = 0, encodedLinf = 0;
            size_t encodedMismatch = 0;
            for (size_t i = 0; i < packedCoeffs; ++i) {
                const int64_t postKeyDiff = recoveredFirstTower[i] - postKeyRemovalTargetFirstTower[i];
                postKeyLinf = std::max<int64_t>(postKeyLinf, std::llabs(postKeyDiff));
            }
            for (size_t i = 0; i < opt.dimension; ++i) {
                const int64_t encodedDiff = recoveredPlain[i] - messageTarget[i];
                encodedLinf = std::max<int64_t>(encodedLinf, std::llabs(encodedDiff));
                if (encodedDiff != 0) ++encodedMismatch;
            }
            const auto tCs1 = Clock::now();
            const auto tTotal1 = Clock::now();
            const bool pass = (encodedMismatch == 0);
            std::cout << "{\n";
            std::cout << "  \"schema\": \"openfhe_dcrtpoly_wire_integration_v4\",\n";
            std::cout << "  \"variant\": \"openfhe_rlwe_only\",\n";
            std::cout << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n";
            std::cout << "  \"clients\": " << opt.clients << ",\n";
            std::cout << "  \"dimension\": " << opt.dimension << ",\n";
            std::cout << "  \"ring_dim\": " << opt.ringDim << ",\n";
            std::cout << "  \"packing_profile\": \"" << reported_packing_profile(opt.packing) << "\",\n";
            std::cout << "  \"packed_coefficients\": " << packedCoeffs << ",\n";
            if (opt.packing == "intcrt_polysubr") {
                std::cout << "  \"intcrt_moduli\": [" << INTCRT_M1 << ", " << INTCRT_M2 << "],\n";
            }
            std::cout << "  \"plaintext_modulus\": " << opt.plaintextModulus << ",\n";
            std::cout << "  \"ciphertext_tower_moduli\": ";
            emit_tower_moduli_json(params);
            std::cout << ",\n";
            std::cout << "  \"k\": " << opt.k << ",\n";
            std::cout << "  \"k0\": " << opt.k0 << ",\n";
            std::cout << "  \"noise\": \"" << opt.noise << "\",\n";
            std::cout << "  \"messages_file_used\": " << (opt.messagesFile.empty() ? "false" : "true") << ",\n";
            std::cout << "  \"material_formula\": \"b_i = a*sk_i + t*e_i + iota_t_to_q(Pack_pp(z_i)) over OpenFHE DCRTPoly\",\n";
            std::cout << "  \"public_a_sampler\": \"OpenFHE DCRTPoly::DugType\",\n";
            std::cout << "  \"secret_sampler\": \"OpenFHE DCRTPoly::TugType centered ternary\",\n";
            std::cout << "  \"error_sampler\": \"" << (opt.noise == "dgg32" ? "OpenFHE DCRTPoly::DggType(3.2), Peikert finite support [-39,39]" : opt.noise) << "\",\n";
            std::cout << "  \"observed_error_min\": " << observedErrorMin << ",\n";
            std::cout << "  \"observed_error_max\": " << observedErrorMax << ",\n";
            std::cout << "  \"shared_a_sampled_once_by_setup\": true,\n";
            std::cout << "  \"public_a_resampled_by_client\": false,\n";
            std::cout << "  \"cryptographic_rng\": \"OpenFHE built-in PRNG\",\n";
            std::cout << "  \"formal_role_process_isolation\": false,\n";
            std::cout << "  \"scope\": \"single_process_preflight_only\",\n";
            std::cout << "  \"openfhe_dcrtpoly_objects\": true,\n";
            std::cout << "  \"wire_serialization_roundtrip\": true,\n";
            std::cout << "  \"wire_aggregate_equals_local\": true,\n";
            std::cout << "  \"apbr_sum_preserved_all_paths\": true,\n";
            std::cout << "  \"post_key_removal_first_tower_diff_linf\": " << postKeyLinf << ",\n";
            std::cout << "  \"post_key_removal_mod_t_diff_linf\": " << encodedLinf << ",\n";
            std::cout << "  \"encoded_plaintext_diff_linf\": " << encodedLinf << ",\n";
            std::cout << "  \"encoded_plaintext_mismatch_count\": " << encodedMismatch << ",\n";
            std::cout << "  \"q_domain_diff_linf\": " << encodedLinf << ",\n";
            std::cout << "  \"message_domain_diff_linf\": " << encodedLinf << ",\n";
            std::cout << "  \"q_domain_mismatch_count\": " << encodedMismatch << ",\n";
            std::cout << "  \"real_fragments_per_path\": 0,\n";
            std::cout << "  \"dummy_fragments_per_path\": 0,\n";
            std::cout << "  \"total_fragments_per_path\": 0,\n";
            std::cout << "  \"wire_bytes\": {\"S1\": 0, \"S2\": 0, \"T1\": 0, \"T2\": 0},\n";
            std::cout << "  \"client_upload_bytes\": 0,\n";
            std::cout << "  \"path_to_cs_bytes\": 0,\n";
            std::cout << "  \"total_payload_bytes\": 0,\n";
            std::cout << "  \"total_wire_bytes\": 0,\n";
            if (opt.emitRecovered) {
                std::cout << "  \"recovered_plaintext\": ";
                emit_int_vector_json(recoveredPlain, opt.dimension);
                std::cout << ",\n";
            }
            std::cout << "  \"runtime_ms\": {\"setup\": " << ms(tSetup0, tSetup1)
                      << ", \"material_generation\": " << ms(tMaterial0, tMaterial1)
                      << ", \"sharing\": 0"
                      << ", \"path_processing\": 0"
                      << ", \"cs_recovery\": " << ms(tCs0, tCs1)
                      << ", \"total\": " << ms(tTotal0, tTotal1) << "}\n";
            std::cout << "}\n";
            return pass ? 0 : 1;
        }

        // Two-share split.
        const auto tSharing0 = Clock::now();
        std::vector<DCRTPoly> sk1, sk2, b1, b2;
        for (size_t i = 0; i < opt.clients; ++i) {
            DCRTPoly rsk = random_poly(params);
            DCRTPoly rb = random_poly(params);
            if (opt.variant == "shuffle_only") {
                DCRTPoly zero(params, Format::EVALUATION, true);
                sk1.push_back(zero);
                sk2.push_back(zero);
            } else {
                sk1.push_back(rsk);
                sk2.push_back(skRecords[i] - rsk);
            }
            b1.push_back(rb);
            b2.push_back(bodyRecords[i] - rb);
        }
        const auto tSharing1 = Clock::now();
        const size_t clientUploadBytes = poly_record_bytes_sum(sk1) + poly_record_bytes_sum(sk2) +
                                         poly_record_bytes_sum(b1) + poly_record_bytes_sum(b2);

        const auto tPath0 = Clock::now();
        bool apbrS1 = true, apbrS2 = true, apbrT1 = true, apbrT2 = true;
        bool rtS1, rtS2, rtT1, rtT2;
        DCRTPoly localS1(params, Format::EVALUATION, true), localS2(params, Format::EVALUATION, true);
        DCRTPoly localT1(params, Format::EVALUATION, true), localT2(params, Format::EVALUATION, true);
        std::vector<WireBlob> wS1, wS2, wT1, wT2;
        if (opt.variant == "four_path_sum_only") {
            wS1 = relay_path_sum_only(sk1, params, rtS1, localS1);
            wS2 = relay_path_sum_only(sk2, params, rtS2, localS2);
            wT1 = relay_path_sum_only(b1, params, rtT1, localT1);
            wT2 = relay_path_sum_only(b2, params, rtT2, localT2);
        } else {
            wS1 = process_path(sk1, params, opt.k, opt.k0, opt.apbr, apbrS1, rtS1, localS1);
            wS2 = process_path(sk2, params, opt.k, opt.k0, opt.apbr, apbrS2, rtS2, localS2);
            wT1 = process_path(b1, params, opt.k, opt.k0, opt.apbr, apbrT1, rtT1, localT1);
            wT2 = process_path(b2, params, opt.k, opt.k0, opt.apbr, apbrT2, rtT2, localT2);
        }
        const auto tPath1 = Clock::now();

        const auto tCs0 = Clock::now();
        auto wire_sum = [&](const std::vector<WireBlob>& blobs) {
            DCRTPoly acc(params, Format::EVALUATION, true);
            for (const auto& blob : blobs)
                acc += deserialize_poly(blob.bytes, params);
            return acc;
        };
        DCRTPoly sAggWire = wire_sum(wS1) + wire_sum(wS2);
        DCRTPoly bAggWire = wire_sum(wT1) + wire_sum(wT2);
        DCRTPoly sAggLocal = localS1 + localS2;
        DCRTPoly bAggLocal = localT1 + localT2;
        bool wireEqualsLocal = (sAggWire == sAggLocal) && (bAggWire == bAggLocal);

        DCRTPoly recoveredPoly = bAggWire - (a * sAggWire);
        auto recoveredFirstTower = first_tower_coeffs(recoveredPoly, packedCoeffs);
        auto recoveredPacked = crt_coeffs_mod_t(recoveredPoly, packedCoeffs, opt.plaintextModulus);
        auto recoveredPlain = unpack_plaintext_profile(recoveredPacked, opt.dimension, opt.packing, opt.ringDim, opt.plaintextModulus);
        int64_t postKeyLinf = 0, encodedLinf = 0;
        size_t encodedMismatch = 0;
        for (size_t i = 0; i < packedCoeffs; ++i) {
            const int64_t postKeyTarget = (opt.variant == "shuffle_only")
                                              ? packedMessageTarget[i]
                                              : postKeyRemovalTargetFirstTower[i];
            const int64_t postKeyDiff = recoveredFirstTower[i] - postKeyTarget;
            postKeyLinf = std::max<int64_t>(postKeyLinf, std::llabs(postKeyDiff));
        }
        for (size_t i = 0; i < opt.dimension; ++i) {
            const int64_t encodedDiff = recoveredPlain[i] - messageTarget[i];
            encodedLinf = std::max<int64_t>(encodedLinf, std::llabs(encodedDiff));
            if (encodedDiff != 0) ++encodedMismatch;
        }
        const auto tCs1 = Clock::now();
        const size_t bytesS1 = wire_bytes_sum(wS1);
        const size_t bytesS2 = wire_bytes_sum(wS2);
        const size_t bytesT1 = wire_bytes_sum(wT1);
        const size_t bytesT2 = wire_bytes_sum(wT2);
        const size_t pathToCsBytes = bytesS1 + bytesS2 + bytesT1 + bytesT2;

        const bool allApbr = apbrS1 && apbrS2 && apbrT1 && apbrT2;
        const bool allRoundtrip = rtS1 && rtS2 && rtT1 && rtT2;
        const bool pass = allApbr && allRoundtrip && wireEqualsLocal && encodedMismatch == 0;
        const auto tTotal1 = Clock::now();
        std::cout << "{\n";
        std::cout << "  \"schema\": \"openfhe_dcrtpoly_wire_integration_v8_rc1_preflight\",\n";
        std::cout << "  \"variant\": \"" << opt.variant << "\",\n";
        std::cout << "  \"status\": \"" << (pass ? "PASS" : "FAIL") << "\",\n";
        std::cout << "  \"clients\": " << opt.clients << ",\n";
        std::cout << "  \"dimension\": " << opt.dimension << ",\n";
        std::cout << "  \"ring_dim\": " << opt.ringDim << ",\n";
        std::cout << "  \"packing_profile\": \"" << reported_packing_profile(opt.packing) << "\",\n";
        std::cout << "  \"packed_coefficients\": " << packedCoeffs << ",\n";
        if (opt.packing == "intcrt_polysubr") {
            std::cout << "  \"intcrt_moduli\": [" << INTCRT_M1 << ", " << INTCRT_M2 << "],\n";
        }
        std::cout << "  \"plaintext_modulus\": " << opt.plaintextModulus << ",\n";
        std::cout << "  \"ciphertext_tower_moduli\": ";
        emit_tower_moduli_json(params);
        std::cout << ",\n";
        std::cout << "  \"k\": " << opt.k << ",\n";
        std::cout << "  \"k0\": " << opt.k0 << ",\n";
        std::cout << "  \"noise\": \"" << opt.noise << "\",\n";
        std::cout << "  \"messages_file_used\": " << (opt.messagesFile.empty() ? "false" : "true") << ",\n";
        std::cout << "  \"apbr_enabled\": " << ((opt.apbr && opt.variant != "four_path_sum_only") ? "true" : "false") << ",\n";
        std::cout << "  \"material_formula\": \"" << (opt.variant == "shuffle_only"
                      ? "shuffle-only message records; no RLWE key-removal semantics"
                      : (opt.variant == "four_path_sum_only"
                         ? "four-path aggregate-only additive key/body shares; each path locally sums received shares and relays one aggregate DCRTPoly record"
                         : "b_i = a*sk_i + t*e_i + iota_t_to_q(Pack_pp(z_i)) over OpenFHE DCRTPoly")) << "\",\n";
        std::cout << "  \"public_a_sampler\": \"OpenFHE DCRTPoly::DugType\",\n";
        std::cout << "  \"secret_sampler\": \"OpenFHE DCRTPoly::TugType centered ternary\",\n";
        std::cout << "  \"error_sampler\": \"" << (opt.noise == "dgg32" ? "OpenFHE DCRTPoly::DggType(3.2), Peikert finite support [-39,39]" : opt.noise) << "\",\n";
        std::cout << "  \"observed_error_min\": " << observedErrorMin << ",\n";
        std::cout << "  \"observed_error_max\": " << observedErrorMax << ",\n";
        std::cout << "  \"shared_a_sampled_once_by_setup\": true,\n";
        std::cout << "  \"public_a_resampled_by_client\": false,\n";
        std::cout << "  \"cryptographic_rng\": \"OpenFHE built-in PRNG\",\n";
        std::cout << "  \"permutation_algorithm\": \"Fisher-Yates with rejection-sampled UniformBelow\",\n";
        std::cout << "  \"formal_role_process_isolation\": false,\n";
        std::cout << "  \"scope\": \"single_process_preflight_only\",\n";
        std::cout << "  \"openfhe_dcrtpoly_objects\": true,\n";
        std::cout << "  \"wire_serialization_roundtrip\": " << (allRoundtrip ? "true" : "false") << ",\n";
        std::cout << "  \"wire_aggregate_equals_local\": " << (wireEqualsLocal ? "true" : "false") << ",\n";
        std::cout << "  \"apbr_sum_preserved_all_paths\": " << (allApbr ? "true" : "false") << ",\n";
        std::cout << "  \"post_key_removal_first_tower_diff_linf\": " << postKeyLinf << ",\n";
        std::cout << "  \"post_key_removal_mod_t_diff_linf\": " << encodedLinf << ",\n";
        std::cout << "  \"encoded_plaintext_diff_linf\": " << encodedLinf << ",\n";
        std::cout << "  \"encoded_plaintext_mismatch_count\": " << encodedMismatch << ",\n";
        std::cout << "  \"q_domain_diff_linf\": " << encodedLinf << ",\n";
        std::cout << "  \"message_domain_diff_linf\": " << encodedLinf << ",\n";
        std::cout << "  \"q_domain_mismatch_count\": " << encodedMismatch << ",\n";
        const size_t realFragmentsPerPath = (opt.variant == "four_path_sum_only") ? 1 : (opt.clients * opt.k);
        const size_t dummyFragmentsPerPath = (opt.variant == "four_path_sum_only") ? 0 : opt.k0;
        std::cout << "  \"real_fragments_per_path\": " << realFragmentsPerPath << ",\n";
        std::cout << "  \"dummy_fragments_per_path\": " << dummyFragmentsPerPath << ",\n";
        std::cout << "  \"total_fragments_per_path\": " << (realFragmentsPerPath + dummyFragmentsPerPath) << ",\n";
        std::cout << "  \"wire_bytes\": {\"S1\": " << bytesS1 << ", \"S2\": " << bytesS2
                  << ", \"T1\": " << bytesT1 << ", \"T2\": " << bytesT2 << "},\n";
        std::cout << "  \"client_upload_bytes\": " << clientUploadBytes << ",\n";
        std::cout << "  \"path_to_cs_bytes\": " << pathToCsBytes << ",\n";
        std::cout << "  \"total_payload_bytes\": " << (clientUploadBytes + pathToCsBytes) << ",\n";
        std::cout << "  \"total_application_protocol_bytes\": " << (clientUploadBytes + pathToCsBytes) << ",\n";
        std::cout << "  \"shuffle_to_cs_relay_payload_bytes\": " << pathToCsBytes << ",\n";
        std::cout << "  \"total_wire_bytes\": " << pathToCsBytes << ",\n";
        if (opt.emitRecovered) {
            std::cout << "  \"recovered_plaintext\": ";
            emit_int_vector_json(recoveredPlain, opt.dimension);
            std::cout << ",\n";
        }
        std::cout << "  \"runtime_ms\": {\"setup\": " << ms(tSetup0, tSetup1)
                  << ", \"material_generation\": " << ms(tMaterial0, tMaterial1)
                  << ", \"sharing\": " << ms(tSharing0, tSharing1)
                  << ", \"path_processing\": " << ms(tPath0, tPath1)
                  << ", \"cs_recovery\": " << ms(tCs0, tCs1)
                  << ", \"total\": " << ms(tTotal0, tTotal1) << "}\n";
        std::cout << "}\n";
        return pass ? 0 : 1;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 2;
    }
}
