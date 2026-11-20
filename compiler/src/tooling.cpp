#include "tooling.hpp"
#include "syntax.hpp"

#include <llvm/ADT/SmallString.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/JSON.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/Support/raw_ostream.h>

#include <algorithm>
#include <iostream>
#include <utility>

namespace vqr {
namespace fs = std::filesystem;

Source readSource(const fs::path &path, const fs::path &contents) {
  auto input = llvm::MemoryBuffer::getFile(contents.string());
  if (!input) fail({}, contents.string() + ": " + input.getError().message(), "E301");
  return {path.string(), (*input)->getBuffer().str()};
}

namespace {

std::pair<int64_t, int64_t> position(std::string_view bytes, std::size_t offset) {
  int64_t line = 0, column = 0;
  for (std::size_t i = 0; i < std::min(offset, bytes.size()); ++i) {
    auto byte = static_cast<unsigned char>(bytes[i]);
    if (byte == '\r' || byte == '\n') {
      if (byte == '\n' && i > 0 && bytes[i - 1] == '\r') continue;
      ++line;
      column = 0;
    } else if ((byte & 0xc0) != 0x80) {
      column += byte >= 0xf0 ? 2 : 1;
    }
  }
  return {line, column};
}

struct Comment {
  std::size_t start;
  std::size_t end;
};

std::vector<Comment> blockComments(std::string_view code, const std::vector<Token> &tokens) {
  std::vector<Comment> comments;
  std::size_t start = 0;
  for (const auto &token : tokens) {
    for (auto at = start; at < token.span.begin;) {
      if (code.substr(at).starts_with("/*")) {
        const auto end = code.find("*/", at + 2) + 2;
        comments.push_back({at, end});
        at = end;
      } else if (code.substr(at).starts_with("//")) {
        at = code.find_first_of("\r\n", at + 2);
      } else {
        ++at;
      }
    }
    start = token.span.end;
  }
  return comments;
}

char closing(char opener) {
  switch (opener) {
  case '{': return '}';
  case '(': return ')';
  case '[': return ']';
  default: return '\0';
  }
}

std::string format(const Source &source) {
  parse(source);
  auto tokens = lex(source);
  std::string_view code = source.bytes;
  auto comments = blockComments(code, tokens);
  std::string expected, out;
  std::size_t tokenIndex = 0, commentIndex = 0;
  std::string_view newline = "\n";
  for (std::size_t start = 0; start < code.size();) {
    auto end = code.find_first_of("\r\n", start);
    if (end == code.npos) end = code.size();
    auto next = end;
    if (next < code.size()) {
      ++next;
      if (code[end] == '\r' && next < code.size() && code[next] == '\n') ++next;
      newline = code.substr(end, next - end);
    }
    auto line = code.substr(start, end - start);
    auto first = line.find_first_not_of(" \t");
    auto last = line.find_last_not_of(" \t");
    while (commentIndex < comments.size() && comments[commentIndex].end <= start) ++commentIndex;
    bool insideComment = commentIndex < comments.size() && comments[commentIndex].start < start;

    auto indentation = expected.size();
    auto leading = start + (first == line.npos ? line.size() : first);
    bool prefix = true;
    for (; tokenIndex < tokens.size() && tokens[tokenIndex].span.begin < end; ++tokenIndex) {
      const auto &token = tokens[tokenIndex];
      auto punctuation = token.kind == TokenKind::Punctuation && token.text.size() == 1 ? token.text.front() : '\0';
      if (!expected.empty() && punctuation == expected.back()) {
        expected.pop_back();
        if (prefix && token.span.begin == leading) {
          indentation = expected.size();
          leading = code.find_first_not_of(" \t", token.span.end);
        } else {
          prefix = false;
        }
      } else {
        if (auto closer = closing(punctuation)) expected.push_back(closer);
        prefix = false;
      }
    }

    if (insideComment) {
      out.append(line);
    } else if (first != line.npos) {
      out.append(indentation * 4, ' ');
      out.append(line.substr(first, last - first + 1));
    }
    out.append(code.substr(end, next - end));
    start = next;
  }
  if (!out.empty() && out.back() != '\n' && out.back() != '\r') out.append(newline);
  return out;
}

void replaceSource(const fs::path &path, const std::string &formatted) {
  if (fs::is_symlink(path)) fail({}, "format --write requires a regular source path", "E302");
  auto permissions = fs::status(path).permissions();
  llvm::SmallString<256> temp;
  int fd;
  if (auto error = llvm::sys::fs::createUniqueFile((path.parent_path() / ".vaqrin-format-%%%%%%").string(), fd, temp))
    fail({}, error.message(), "E302");
  auto temporary = fs::path(temp.str().str());
  std::error_code error;
  {
    llvm::raw_fd_ostream output(fd, true);
    output << formatted;
    output.close();
    if (output.has_error()) {
      error = output.error();
      output.clear_error();
    }
  }
  if (!error) fs::permissions(temporary, permissions, error);
  if (!error) fs::rename(temporary, path, error);
  if (error) {
    std::error_code ignored;
    fs::remove(temporary, ignored);
    fail({}, error.message(), "E302");
  }
}

} // namespace

void printJsonDiagnostic(const Diagnostic &error) {
  llvm::json::Object item{{"code", error.code}, {"message", error.what()}};
  if (const auto *source = error.span.source) {
    const auto [line, column] = position(source->bytes, error.span.begin);
    const auto [endLine, endColumn] = position(source->bytes, error.span.end);
    item["path"] = source->path;
    item["range"] = llvm::json::Object{
        {"start", llvm::json::Object{{"line", line}, {"character", column}}},
        {"end", llvm::json::Object{{"line", endLine}, {"character", endColumn}}}};
  }
  llvm::errs() << llvm::json::Value(std::move(item)) << '\n';
}

int formatCommand(int argc, char **argv) {
  Source source;
  try {
    enum class Mode { Print, Check, Write };
    Mode mode = Mode::Print;
    std::vector<fs::path> paths;
    for (int i = 2; i < argc; ++i) {
      std::string_view arg(argv[i]);
      if (arg == "--check" || arg == "--write" || arg == "-w") {
        if (mode != Mode::Print) fail({}, "format modes are mutually exclusive", "E300");
        mode = arg == "--check" ? Mode::Check : Mode::Write;
      } else if (!arg.starts_with('-') || arg == "-") {
        paths.emplace_back(arg);
      } else {
        fail({}, "unknown format option '" + std::string(arg) + "'", "E300");
      }
    }
    if (paths.empty() || (mode == Mode::Print && paths.size() != 1))
      fail({}, "usage: vaqc fmt [--check | --write] FILE... | vaqc fmt -", "E300");
    bool changed = false;
    for (const auto &path : paths) {
      if (path == "-") {
        if (mode != Mode::Print) fail({}, "stdin requires stdout format mode", "E300");
        auto input = llvm::MemoryBuffer::getSTDIN();
        if (!input) fail({}, input.getError().message(), "E301");
        source = {"<stdin>", (*input)->getBuffer().str()};
      } else {
        if (path.extension() != ".vqr") fail({}, "format requires .vqr source files", "E300");
        source = readSource(path, path);
      }
      const auto formatted = format(source);
      if (mode == Mode::Print) {
        llvm::outs() << formatted;
      } else if (formatted != source.bytes) {
        changed = true;
        if (mode == Mode::Check) llvm::errs() << path.string() << " needs formatting\n";
        else replaceSource(path, formatted);
      }
    }
    return mode == Mode::Check && changed ? 1 : 0;
  } catch (const Diagnostic &error) {
    printDiagnostic(error);
  } catch (const fs::filesystem_error &error) {
    std::cerr << "vaqc: error[E301]: " << error.what() << '\n';
  }
  return 1;
}

} // namespace vqr
