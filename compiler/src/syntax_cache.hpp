#pragma once

#include "syntax.hpp"

#include <filesystem>
#include <optional>

#include <llvm/Support/MemoryBuffer.h>

namespace vqr {

namespace artifact {
std::unique_ptr<llvm::MemoryBuffer> read(const std::filesystem::path &entry, std::size_t limit);
llvm::StringRef payload(const llvm::MemoryBuffer &record);
void write(const std::filesystem::path &entry, llvm::StringRef bytes, std::size_t limit);
} // namespace artifact

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
