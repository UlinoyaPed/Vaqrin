#pragma once

#include "source.hpp"

#include <filesystem>

namespace vqr {

Source readSource(const std::filesystem::path &path, const std::filesystem::path &contents);
void printJsonDiagnostic(const Diagnostic &error);
int formatCommand(int argc, char **argv);

} // namespace vqr
