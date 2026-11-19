cmake_minimum_required(VERSION 3.24)
project(Vaqrin VERSION 0.1.2 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(LLVM REQUIRED CONFIG)
option(VAQRIN_REQUIRE_BASELINE "Require the LLVM 20.1.8 design baseline" OFF)
if(NOT LLVM_PACKAGE_VERSION VERSION_EQUAL 20.1.8)
    if(VAQRIN_REQUIRE_BASELINE)
        message(FATAL_ERROR "Vaqrin's LLVM baseline is 20.1.8, found ${LLVM_PACKAGE_VERSION}")
    endif()
    message(STATUS "Vaqrin uses LLVM ${LLVM_PACKAGE_VERSION} (design baseline: 20.1.8)")
endif()
if(LLVM_PACKAGE_VERSION VERSION_LESS 20.1.8)
    message(FATAL_ERROR "Vaqrin requires LLVM 20.1.8 or newer")
endif()

add_executable(vaqc src/driver.cpp src/source.cpp src/parser.cpp src/compiler.cpp src/body.cpp src/expressions.cpp src/patterns.cpp src/numeric.cpp src/ctfe.cpp src/stdlib.cpp)
target_include_directories(vaqc SYSTEM PRIVATE ${LLVM_INCLUDE_DIRS})
separate_arguments(llvm_definitions NATIVE_COMMAND "${LLVM_DEFINITIONS}")
target_compile_options(vaqc PRIVATE ${llvm_definitions}
    $<$<CXX_COMPILER_ID:Clang,GNU>:-Wall;-Wextra;-pedantic-errors>)
if(TARGET LLVM)
    target_link_libraries(vaqc PRIVATE LLVM)
else()
    llvm_map_components_to_libnames(llvm_libs all)
    target_link_libraries(vaqc PRIVATE ${llvm_libs})
endif()
set(VAQRIN_STDLIB_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/../stdlib")
file(GLOB_RECURSE vaqrin_package_files CONFIGURE_DEPENDS
    RELATIVE "${VAQRIN_STDLIB_SOURCE}" "${VAQRIN_STDLIB_SOURCE}/*.vqr")
foreach(package_file IN LISTS vaqrin_package_files)
    configure_file("${VAQRIN_STDLIB_SOURCE}/${package_file}"
        "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/stdlib/${package_file}" COPYONLY)
endforeach()
install(DIRECTORY "${VAQRIN_STDLIB_SOURCE}/" DESTINATION share/vaqrin/stdlib
    FILES_MATCHING PATTERN "*.vqr")

install(TARGETS vaqc RUNTIME DESTINATION bin)
install(FILES UNICODE-LICENSE DESTINATION share/licenses/vaqc)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME vaqc-acceptance COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/acceptance.py $<TARGET_FILE:vaqc>)
endif()
