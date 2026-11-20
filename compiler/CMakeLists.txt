cmake_minimum_required(VERSION 3.24)
project(Vaqrin VERSION 0.1.5 LANGUAGES C CXX)
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

add_executable(vaqc src/driver.cpp src/inputs.cpp src/source.cpp src/tooling.cpp src/parser.cpp src/compiler.cpp src/body.cpp src/expressions.cpp src/patterns.cpp src/numeric.cpp src/ctfe.cpp src/stdlib.cpp src/std_bits.cpp src/std_atomic.cpp src/std_thread.cpp src/std_task.cpp)
target_include_directories(vaqc SYSTEM PRIVATE ${LLVM_INCLUDE_DIRS})
separate_arguments(llvm_definitions NATIVE_COMMAND "${LLVM_DEFINITIONS}")
target_compile_options(vaqc PRIVATE ${llvm_definitions}
    $<$<CXX_COMPILER_ID:Clang,GNU>:-Wall;-Wextra;-pedantic-errors>)
option(VAQRIN_USE_LLVM_DYLIB "Prefer the complete LLVM shared library when exported by the SDK" ON)
if(VAQRIN_USE_LLVM_DYLIB AND TARGET LLVM AND "all" IN_LIST LLVM_DYLIB_COMPONENTS)
    target_link_libraries(vaqc PRIVATE LLVM)
else()
    # Installed SDKs can leave LLVM_COMPONENT_LIBS unset, making "all" expand to nothing.
    llvm_map_components_to_libnames(llvm_libs
        Core Support TargetParser BitWriter Passes IPO CodeGen MC Target
        AllTargetsCodeGens AllTargetsDescs AllTargetsInfos)
    target_link_libraries(vaqc PRIVATE ${llvm_libs})
endif()
set(VAQRIN_STDLIB_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/../stdlib")
file(GLOB_RECURSE vaqrin_package_files CONFIGURE_DEPENDS
    RELATIVE "${VAQRIN_STDLIB_SOURCE}" "${VAQRIN_STDLIB_SOURCE}/*.vqr")
set(VAQRIN_STDLIB_BUILD "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/stdlib")
file(GLOB_RECURSE staged_packages RELATIVE "${VAQRIN_STDLIB_BUILD}" "${VAQRIN_STDLIB_BUILD}/*.vqr")
foreach(staged_file IN LISTS staged_packages)
    if(NOT staged_file IN_LIST vaqrin_package_files)
        file(REMOVE "${VAQRIN_STDLIB_BUILD}/${staged_file}")
    endif()
endforeach()
foreach(package_file IN LISTS vaqrin_package_files)
    configure_file("${VAQRIN_STDLIB_SOURCE}/${package_file}"
        "${VAQRIN_STDLIB_BUILD}/${package_file}" COPYONLY)
endforeach()
install(DIRECTORY "${VAQRIN_STDLIB_SOURCE}/" DESTINATION share/vaqrin/stdlib
    FILES_MATCHING PATTERN "*.vqr")
configure_file(runtime/tasks.c "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/runtime/tasks.c" COPYONLY)
configure_file(runtime/io_posix.cpp "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/runtime/io_posix.cpp" COPYONLY)
configure_file(runtime/decimal.cpp "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/runtime/decimal.cpp" COPYONLY)
configure_file(runtime/process_posix.cpp "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/runtime/process_posix.cpp" COPYONLY)
configure_file(runtime/net_posix.cpp "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/runtime/net_posix.cpp" COPYONLY)
configure_file(runtime/http2_nghttp2.cpp "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/runtime/http2_nghttp2.cpp" COPYONLY)
configure_file(runtime/tls_openssl.cpp "${CMAKE_CURRENT_BINARY_DIR}/share/vaqrin/runtime/tls_openssl.cpp" COPYONLY)
install(FILES runtime/tasks.c runtime/io_posix.cpp runtime/decimal.cpp runtime/process_posix.cpp
    runtime/net_posix.cpp runtime/tls_openssl.cpp runtime/http2_nghttp2.cpp DESTINATION share/vaqrin/runtime)

install(TARGETS vaqc RUNTIME DESTINATION bin)
install(PROGRAMS tools/protoc-gen-vaqrin DESTINATION bin)
install(FILES UNICODE-LICENSE DESTINATION share/licenses/vaqc)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME vaqc-acceptance COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/acceptance.py $<TARGET_FILE:vaqc>)
    add_test(NAME vaqc-container-acceptance COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/coll_acceptance.py $<TARGET_FILE:vaqc>)
    add_test(NAME vaqc-io-acceptance COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/io_acceptance.py $<TARGET_FILE:vaqc>)
    add_test(NAME vaqc-io-static COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/io_static_validation.py $<TARGET_FILE:vaqc>)
    add_test(NAME vaqc-container-static COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/coll_static_audit.py)
    add_test(NAME vaqc-stdlib-acceptance COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/stdlib_acceptance.py $<TARGET_FILE:vaqc>)
    add_test(NAME vaqc-protocol-acceptance COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/protocol_acceptance.py $<TARGET_FILE:vaqc>)
    add_test(NAME vaqc-editor-acceptance COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/editor_acceptance.py $<TARGET_FILE:vaqc>)
    set_tests_properties(vaqc-editor-acceptance PROPERTIES TIMEOUT 40)
endif()
