#include "openfhe.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

using namespace lbcrypto;

namespace {

struct Options {
    size_t clients = 2;
    size_t dimension = 16;
    size_t ringDim = 1024;
    uint32_t towers = 2;
    uint32_t bits = 50;
    uint64_t seed = 2024;
    std::string noise = "zero";
};

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
        else if (a == "--seed") o.seed = parse_u64(need("--seed"));
        else if (a == "--noise") o.noise = need("--noise");
        else throw std::runtime_error("unknown argument: " + a);
    }
    if (o.dimension > o.ringDim)
        throw std::runtime_error("dimension must not exceed ring dimension in this smoke");
    return o;
}

int64_t sample_error(std::mt19937_64& rng, const std::string& mode) {
    if (mode == "zero") return 0;
    if (mode == "small") {
        std::uniform_int_distribution<int64_t> dist(-1, 1);
        return dist(rng);
    }
    if (mode == "dgg32") {
        std::normal_distribution<double> dist(0.0, 3.2);
        return static_cast<int64_t>(std::llround(dist(rng)));
    }
    throw std::runtime_error("unsupported noise mode");
}

int64_t centered(uint64_t x, uint64_t mod) {
    return (x > mod / 2) ? static_cast<int64_t>(x - mod) : static_cast<int64_t>(x);
}

DCRTPoly make_poly(const std::shared_ptr<ILDCRTParams<BigInteger>>& params,
                   const std::vector<int64_t>& coeffs) {
    DCRTPoly p(params, Format::COEFFICIENT, true);
    p = coeffs;
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

}  // namespace

int main(int argc, char** argv) {
    try {
        const Options opt = parse(argc, argv);
        const uint32_t cyclotomicOrder = static_cast<uint32_t>(2 * opt.ringDim);
        auto params = std::make_shared<ILDCRTParams<BigInteger>>(cyclotomicOrder, opt.towers, opt.bits);
        const uint64_t q0 = params->GetParams()[0]->GetModulus().ConvertToInt<uint64_t>();

        std::mt19937_64 rng(opt.seed);
        std::uniform_int_distribution<int64_t> msgDist(-1000, 1000);
        std::uniform_int_distribution<int64_t> skDist(-2, 2);

        std::vector<int64_t> aCoeff(opt.ringDim, 0);
        std::uniform_int_distribution<uint64_t> aDist(0, q0 - 1);
        for (size_t i = 0; i < opt.ringDim; ++i)
            aCoeff[i] = static_cast<int64_t>(aDist(rng) % 1000000);  // small coeff smoke
        DCRTPoly a = make_poly(params, aCoeff);

        DCRTPoly skAgg(params, Format::EVALUATION, true);
        DCRTPoly bodyAgg(params, Format::EVALUATION, true);
        std::vector<int64_t> messageTarget(opt.ringDim, 0);
        std::vector<int64_t> qTarget(opt.ringDim, 0);

        for (size_t c = 0; c < opt.clients; ++c) {
            std::vector<int64_t> skCoeff(opt.ringDim, 0);
            std::vector<int64_t> msgCoeff(opt.ringDim, 0);
            std::vector<int64_t> errCoeff(opt.ringDim, 0);
            for (size_t i = 0; i < opt.dimension; ++i) {
                skCoeff[i] = skDist(rng);
                msgCoeff[i] = msgDist(rng);
                errCoeff[i] = sample_error(rng, opt.noise);
                messageTarget[i] += msgCoeff[i];
                qTarget[i] += msgCoeff[i] + errCoeff[i];
            }

            DCRTPoly sk = make_poly(params, skCoeff);
            DCRTPoly msg = make_poly(params, msgCoeff);
            DCRTPoly err = make_poly(params, errCoeff);
            DCRTPoly body = a * sk + err + msg;
            skAgg += sk;
            bodyAgg += body;
        }

        DCRTPoly recoveredPoly = bodyAgg - (a * skAgg);
        const auto recovered = first_tower_coeffs(recoveredPoly, opt.dimension);
        int64_t qLinf = 0;
        int64_t msgLinf = 0;
        size_t qMismatch = 0;
        for (size_t i = 0; i < opt.dimension; ++i) {
            const int64_t qDiff = recovered[i] - qTarget[i];
            const int64_t msgDiff = recovered[i] - messageTarget[i];
            qLinf = std::max<int64_t>(qLinf, std::llabs(qDiff));
            msgLinf = std::max<int64_t>(msgLinf, std::llabs(msgDiff));
            if (qDiff != 0) ++qMismatch;
        }

        std::cout << "{\n";
        std::cout << "  \"schema\": \"openfhe_dcrtpoly_material_smoke_v1\",\n";
        std::cout << "  \"status\": \"" << (qMismatch == 0 ? "PASS" : "FAIL") << "\",\n";
        std::cout << "  \"clients\": " << opt.clients << ",\n";
        std::cout << "  \"dimension\": " << opt.dimension << ",\n";
        std::cout << "  \"ring_dim\": " << opt.ringDim << ",\n";
        std::cout << "  \"towers\": " << opt.towers << ",\n";
        std::cout << "  \"tower_bits\": " << opt.bits << ",\n";
        std::cout << "  \"noise\": \"" << opt.noise << "\",\n";
        std::cout << "  \"openfhe_dcrtpoly_objects\": true,\n";
        std::cout << "  \"shared_a\": true,\n";
        std::cout << "  \"body_formula\": \"b_i = a*sk_i + e_i + m_i over DCRTPoly\",\n";
        std::cout << "  \"q_domain_diff_linf\": " << qLinf << ",\n";
        std::cout << "  \"message_domain_diff_linf\": " << msgLinf << ",\n";
        std::cout << "  \"q_domain_mismatch_count\": " << qMismatch << "\n";
        std::cout << "}\n";
        return qMismatch == 0 ? 0 : 1;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 2;
    }
}
