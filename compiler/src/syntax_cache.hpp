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
  std::filesystem::path tableEntry(const Source &source, std::size_t declarationOffset) const;
  std::optional<std::string> loadTable(const std::filesystem::path &entry, std::size_t size) const;
  void storeTable(const std::filesystem::path &entry, std::string_view bytes) const;

private:
  std::filesystem::path directory;
  std::string compilerKey;
};

} // namespace vqr
