#pragma once

#include "syntax.hpp"

#include <filesystem>
#include <optional>

namespace vqr {

class SyntaxCache {
public:
  explicit SyntaxCache(const std::filesystem::path &executable);
  std::filesystem::path entry(const Source &source) const;
  std::optional<File> load(const std::filesystem::path &entry, const Source &source) const;
  void store(const std::filesystem::path &entry, const Source &source, File &file) const;

private:
  std::filesystem::path directory;
  std::string compilerKey;
};

} // namespace vqr
