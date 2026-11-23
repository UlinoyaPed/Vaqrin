#include "syntax_cache.hpp"

#include <bit>
#include <cstdlib>
#include <limits>

#ifndef _WIN32
#include <sys/stat.h>
#include <unistd.h>
#endif

#include <llvm/ADT/SmallString.h>
#include <llvm/ADT/StringExtras.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/Support/SHA256.h>
#include <llvm/Support/raw_ostream.h>

namespace vqr {
namespace {

constexpr std::size_t limit = 16 * 1024 * 1024;
constexpr unsigned maxDepth = 512;
struct InvalidCache {};

template <bool Reading> class Archive {
  const Source &source;
  llvm::StringRef input;
  std::string output;
  unsigned depth = 0;

  void number(std::size_t &value) {
    if constexpr (Reading) {
      value = 0;
      for (unsigned bit = 0; bit < std::numeric_limits<std::size_t>::digits; bit += 7) {
        if (input.empty()) throw InvalidCache{};
        const auto byte = static_cast<unsigned char>(input.front());
        input = input.drop_front();
        const std::size_t part = byte & 127;
        if (part > (std::numeric_limits<std::size_t>::max() >> bit)) throw InvalidCache{};
        value |= part << bit;
        if (byte < 128) return;
      }
      throw InvalidCache{};
    } else {
      while (value >= 128) {
        output.push_back(static_cast<char>((value & 127) | 128));
        value >>= 7;
      }
      output.push_back(static_cast<char>(value));
      if (output.size() > limit) throw InvalidCache{};
    }
  }

  template <typename E> void kind(E &value, E last) {
    std::size_t raw = static_cast<std::size_t>(value);
    number(raw);
    if constexpr (Reading) {
      if (raw > static_cast<std::size_t>(last)) throw InvalidCache{};
      value = static_cast<E>(raw);
    }
  }

  void item(bool &value) {
    std::size_t raw = value;
    number(raw);
    if constexpr (Reading) {
      if (raw > 1) throw InvalidCache{};
      value = raw != 0;
    }
  }

  void item(std::string &value) {
    auto size = value.size();
    number(size);
    if constexpr (Reading) {
      if (size > input.size()) throw InvalidCache{};
      value = input.take_front(size).str();
      input = input.drop_front(size);
    } else {
      if (value.size() > limit - output.size()) throw InvalidCache{};
      output += value;
    }
  }

  void item(Span &value) {
    if constexpr (!Reading)
      if (value.source && value.source != &source) throw InvalidCache{};
    std::size_t begin = value.source ? value.begin + 1 : 0;
    auto length = value.end - value.begin;
    number(begin);
    if (begin == 0) return;
    number(length);
    if constexpr (Reading) {
      --begin;
      if (begin > source.bytes.size() || length > source.bytes.size() - begin) throw InvalidCache{};
      value = {&source, begin, begin + length};
    }
  }

  void item(Ast &value) {
    bool present = static_cast<bool>(value);
    item(present);
    if (!present) return;
    if (++depth > maxDepth) throw InvalidCache{};
    if constexpr (Reading) value = std::make_shared<Node>();
    auto &node = *value;
    kind(node.kind, NodeKind::TypeFunction);
    fields(node.span, node.text, node.children, node.names, node.parameters, node.type, node.tail, node.bindingSpans);
    --depth;
  }

  void item(Parameter &value) { fields(value.name, value.span, value.type, value.comptime); }
  void item(Attribute &value) { fields(value.name, value.span, value.value, value.types); }
  void item(Import &value) { fields(value.filePath, value.path, value.alias, value.span); }

  void item(Decl &value) {
    if (++depth > maxDepth) throw InvalidCache{};
    kind(value.kind, DeclKind::Conditional);
    fields(value.span, value.nameSpan, value.name, value.type, value.body, value.params, value.generics,
           value.attributes, value.members, value.otherwise, value.initializers, value.visible, value.external,
           value.exported, value.readOnly, value.comptime, value.unsafe, value.associated, value.embed);
    --depth;
  }

  template <typename L, typename R> void item(std::pair<L, R> &value) { fields(value.first, value.second); }
  template <typename T> void item(std::vector<T> &values) {
    auto size = values.size();
    number(size);
    if constexpr (Reading) {
      if (size > input.size()) throw InvalidCache{};
      for (std::size_t i = 0; i < size; ++i) {
        T value{};
        item(value);
        values.push_back(std::move(value));
      }
    } else {
      for (auto &value : values) item(value);
    }
  }

  void fields(auto &...values) { (item(values), ...); }

public:
  Archive(const Source &source, llvm::StringRef input = {}) : source(source), input(input) {}
  void file(File &value) { fields(value.package, value.span, value.imports, value.declarations); }
  bool complete() const { return input.empty(); }
  std::string release() { return std::move(output); }
};

std::string digest(llvm::StringRef data) {
  llvm::SHA256 hash;
  hash.update(data);
  return llvm::toHex(hash.final(), true);
}

} // namespace

namespace artifact {
namespace {
constexpr std::size_t checksumLength = 64;
}

bool trustedDirectory(const std::filesystem::path &path) {
#ifdef _WIN32
  std::error_code error;
  return std::filesystem::is_directory(std::filesystem::symlink_status(path, error));
#else
  const auto user = geteuid();
  for (const auto &part : path)
    if (part == "..") return false;
  auto directory = path.lexically_normal();
  if (!directory.is_absolute()) return false;
  for (bool leaf = true;; leaf = false, directory = directory.parent_path()) {
    struct stat status {};
    if (lstat(directory.c_str(), &status) != 0 || !S_ISDIR(status.st_mode) ||
        (status.st_uid != user && (leaf || status.st_uid != 0)) ||
        ((status.st_mode & (S_IWGRP | S_IWOTH)) && (leaf || !(status.st_mode & S_ISVTX))))
      return false;
    if (directory == directory.root_path()) return true;
  }
#endif
}

bool trustedFile(const std::filesystem::path &path) {
#ifdef _WIN32
  std::error_code error;
  return std::filesystem::is_regular_file(std::filesystem::symlink_status(path, error));
#else
  struct stat status {};
  return lstat(path.c_str(), &status) == 0 && S_ISREG(status.st_mode) && status.st_uid == geteuid() &&
         !(status.st_mode & (S_IWGRP | S_IWOTH));
#endif
}

std::optional<std::string> executableIdentity(const std::filesystem::path &executable) {
  llvm::sys::fs::file_status status;
  if (llvm::sys::fs::status(executable.string(), status)) return {};
  const auto modified = status.getLastModificationTime().time_since_epoch().count();
  return executable.string() + '\n' + std::to_string(status.getSize()) + '\n' + std::to_string(modified);
}

llvm::StringRef payload(const llvm::MemoryBuffer &record) { return record.getBuffer().drop_front(checksumLength); }

std::unique_ptr<llvm::MemoryBuffer> read(const std::filesystem::path &entry, std::size_t maxBytes) {
  if (entry.empty() || !trustedDirectory(entry.parent_path()) || !trustedFile(entry)) return {};
  std::error_code error;
  const auto size = std::filesystem::file_size(entry, error);
  if (error || size <= checksumLength || size - checksumLength > maxBytes) return {};
  auto buffer = llvm::MemoryBuffer::getFile(entry.string());
  if (!buffer) return {};
  const auto record = (*buffer)->getBuffer();
  if (record.size() != size || digest(payload(**buffer)) != record.take_front(checksumLength)) return {};
  return std::move(*buffer);
}

void write(const std::filesystem::path &entry, llvm::StringRef bytes, std::size_t maxBytes) {
  if (entry.empty() || bytes.empty() || bytes.size() > maxBytes) return;
  auto directory = entry.parent_path();
  std::error_code error;
  std::filesystem::create_directories(directory, error);
  if (error || !trustedDirectory(directory)) return;
  llvm::SmallString<256> temporary;
  int fd;
  if (llvm::sys::fs::createUniqueFile((directory / ".artifact-%%%%%%").string(), fd, temporary)) return;
  {
    llvm::raw_fd_ostream out(fd, true);
    out << digest(bytes) << bytes;
    out.close();
    error = out.error();
    out.clear_error();
  }
  if (!error) llvm::sys::fs::rename(temporary, entry.string());
  llvm::sys::fs::remove(temporary);
}

} // namespace artifact

SyntaxCache::SyntaxCache(const std::filesystem::path &executable) {
  if (auto *custom = std::getenv("VAQRIN_AST_CACHE")) {
    if (!std::filesystem::path(custom).is_absolute()) return;
    directory = custom;
  } else if (auto *xdg = std::getenv("XDG_CACHE_HOME"); xdg && std::filesystem::path(xdg).is_absolute()) {
    directory = std::filesystem::path(xdg) / "vaqrin" / "syntax";
  } else if (auto *home = std::getenv("HOME"); home && std::filesystem::path(home).is_absolute()) {
    directory = std::filesystem::path(home) / ".cache" / "vaqrin" / "syntax";
  } else {
    return;
  }
  auto identity = artifact::executableIdentity(executable);
  if (!identity) {
    directory.clear();
    return;
  }
  compilerKey = "v3\n" + *identity;
}

std::filesystem::path SyntaxCache::entry(const Source &source) const {
  if (directory.empty() || source.bytes.size() < 32768 || source.bytes.size() > limit) return {};
  llvm::SHA256 hash;
  hash.update(compilerKey);
  hash.update("\n");
  hash.update(source.bytes);
  return directory / (llvm::toHex(hash.final(), true) + ".ast");
}

std::optional<File> SyntaxCache::load(const std::filesystem::path &entry, const Source &source) const {
  auto record = artifact::read(entry, limit);
  if (!record) return {};
  auto data = artifact::payload(*record);
  try {
    File value;
    Archive<true> archive(source, data);
    archive.file(value);
    if (archive.complete()) return value;
  } catch (const InvalidCache &) {}
  return {};
}

void SyntaxCache::store(const std::filesystem::path &entry, const Source &source, File &file) const {
  if (entry.empty()) return;
  std::string payload;
  try {
    Archive<false> archive(source);
    archive.file(file);
    payload = archive.release();
  } catch (const InvalidCache &) { return; }
  artifact::write(entry, payload, limit);
}

std::filesystem::path SyntaxCache::tableEntry(const Source &source, std::size_t declarationOffset) const {
  if (directory.empty() || source.bytes.size() > limit) return {};
  llvm::SHA256 hash;
  hash.update(compilerKey);
  hash.update(std::endian::native == std::endian::little ? "\ntable-le\n" : "\ntable-be\n");
  hash.update(std::to_string(declarationOffset));
  hash.update("\n");
  hash.update(source.bytes);
  return directory / (llvm::toHex(hash.final(), true) + ".table");
}

std::optional<std::string> SyntaxCache::loadTable(const std::filesystem::path &entry, std::size_t size) const {
  auto record = artifact::read(entry, limit);
  if (!record || artifact::payload(*record).size() != size) return {};
  return artifact::payload(*record).str();
}

void SyntaxCache::storeTable(const std::filesystem::path &entry, std::string_view bytes) const {
  artifact::write(entry, llvm::StringRef(bytes), limit);
}

} // namespace vqr
