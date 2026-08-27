cmake_minimum_required(VERSION 3.16)
project(RelHideFL_OpenFHE_Artifact CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_PREFIX_PATH "/home/liguiping/openfhe-install" CACHE PATH "OpenFHE installation prefix")

find_package(OpenFHE CONFIG REQUIRED)

include_directories(${OpenFHE_INCLUDE})
include_directories(${OpenFHE_INCLUDE}/core)
include_directories(${OpenFHE_INCLUDE}/pke)
include_directories(${OpenFHE_INCLUDE}/binfhe)
include_directories(${OpenFHE_INCLUDE}/third-party/include)

include_directories(/home/liguiping/openfhe-install/include/openfhe)
include_directories(/home/liguiping/openfhe-install/include/openfhe/core)
include_directories(/home/liguiping/openfhe-install/include/openfhe/pke)
include_directories(/home/liguiping/openfhe-install/include/openfhe/binfhe)
include_directories(src/rlwe)

link_directories(${OpenFHE_LIBDIR})
link_directories(/home/liguiping/openfhe-install/lib)

add_executable(
    openfhe_dcrtpoly_material_smoke
    src/rlwe/openfhe_dcrtpoly_material_smoke.cpp
)
target_link_libraries(openfhe_dcrtpoly_material_smoke ${OpenFHE_LIBRARIES})

add_executable(
    openfhe_dcrtpoly_wire_integration
    src/apbr_splitmix/openfhe_dcrtpoly_wire_integration.cpp
)
target_link_libraries(openfhe_dcrtpoly_wire_integration ${OpenFHE_LIBRARIES})

add_executable(
    route_a_v8_randomness_selftest
    src/rlwe/v8_randomness_selftest.cpp
)
target_link_libraries(route_a_v8_randomness_selftest ${OpenFHE_LIBRARIES})

add_executable(
    openfhe_bgv_only_baseline
    experiments/baseline/bgv_only/openfhe_bgv_only_baseline.cpp
)
target_link_libraries(openfhe_bgv_only_baseline ${OpenFHE_LIBRARIES})

add_executable(
    shamir_shuffle_proxy_baseline
    experiments/baseline/shamir_shuffle_proxy/shamir_shuffle_proxy_baseline.cpp
)
target_link_libraries(shamir_shuffle_proxy_baseline ${OpenFHE_LIBRARIES})
