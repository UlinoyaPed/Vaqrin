#pragma once

#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace vqr {

struct Source {
  std::string path;
  std::string bytes;
};

struct Span {
  const Source *source = nullptr;
  std::size_t begin = 0;
  std::size_t end = 0;
};

class Diagnostic : public std::runtime_error {
public:
  Diagnostic(Span span, std::string code, std::string message);
  Span span;
  std::string code;
};

[[noreturn]] void fail(Span span, std::string_view message, std::string_view code = "E200");
void printDiagnostic(const Diagnostic &error);

enum class TokenKind { End, Name, Keyword, Number, Text, Character, Punctuation };

struct Token {
  TokenKind kind;
  std::string text;
  Span span;
  bool newline = false;
};

void validateUtf8(const Source &source);
std::vector<Token> lex(const Source &source, bool indexOnly = false);
std::string identifier(std::string_view spelling);
char32_t scalar(std::string_view text, std::size_t &index, Span span);
void appendUtf8(std::string &text, char32_t code);

} // namespace vqr
