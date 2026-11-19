#include "source.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <iostream>
#include <utility>

namespace vqr {
namespace {
struct Range {
  char32_t first, last;
};
struct Combining {
  char32_t code;
  unsigned rank;
};
struct Decomposition {
  char32_t code;
  unsigned count;
  char32_t values[4];
};
struct Composition {
  char32_t first, second, result;
};
#include "unicode17.inc"

bool contains(const auto &table, char32_t cp) {
  auto p = std::ranges::lower_bound(table, cp, {}, &Range::last);
  return p != std::end(table) && p->first <= cp;
}

unsigned rank(char32_t cp) {
  auto p = std::ranges::lower_bound(combining, cp, {}, &Combining::code);
  return p != std::end(combining) && p->code == cp ? p->rank : 0;
}

constexpr char32_t S = 0xac00, L = 0x1100, V = 0x1161, T = 0x11a7;
constexpr unsigned LC = 19, VC = 21, TC = 28, SC = LC * VC * TC;

char32_t combine(char32_t a, char32_t b) {
  if (a >= L && a < L + LC && b >= V && b < V + VC) return S + ((a - L) * VC + b - V) * TC;
  if (a >= S && a < S + SC && (a - S) % TC == 0 && b > T && b < T + TC) return a + (b - T);
  auto p = std::ranges::lower_bound(compositions, std::pair{a, b}, {},
                                    [](const Composition &c) { return std::pair{c.first, c.second}; });
  return p != std::end(compositions) && p->first == a && p->second == b ? p->result : 0;
}

std::string normalize(std::string_view bytes, Span span) {
  std::vector<char32_t> ordered;
  for (std::size_t i = 0; i < bytes.size();) {
    char32_t c = scalar(bytes, i, span);
    if (c >= S && c < S + SC) {
      unsigned offset = c - S;
      ordered.push_back(L + offset / (VC * TC));
      ordered.push_back(V + (offset / TC) % VC);
      if (offset % TC) ordered.push_back(T + offset % TC);
      continue;
    }
    auto p = std::ranges::lower_bound(decompositions, c, {}, &Decomposition::code);
    if (p != std::end(decompositions) && p->code == c)
      ordered.insert(ordered.end(), p->values, p->values + p->count);
    else
      ordered.push_back(c);
  }
  for (auto begin = ordered.begin(); begin != ordered.end();) {
    if (rank(*begin) == 0) ++begin;
    auto end = std::find_if(begin, ordered.end(), [](char32_t c) { return rank(c) == 0; });
    std::stable_sort(begin, end, [](char32_t a, char32_t b) { return rank(a) < rank(b); });
    begin = end;
  }
  std::vector<char32_t> result;
  std::size_t starter = 0;
  unsigned last = 0;
  for (auto cp : ordered) {
    unsigned current = rank(cp);
    if (!result.empty() && (last == 0 || last < current)) {
      if (auto composed = combine(result[starter], cp)) {
        result[starter] = composed;
        continue;
      }
    }
    if (!current) starter = result.size();
    result.push_back(cp);
    last = current;
  }
  std::string output;
  for (auto c : result)
    appendUtf8(output, c);
  return output;
}

int digit(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return 100;
}

constexpr std::string_view keywords[] = {
    "package",  "import",    "as",   "pub",    "var",    "const",  "comptime", "func",
    "class",    "interface", "enum", "error",  "type",   "public", "private",  "static",
    "operator", "embed",     "self", "extern", "export", "unsafe", "return",   "break",
    "continue", "if",        "else", "switch", "select", "for",    "in",       "move",
    "new",      "catch",     "true", "false",  "null",   "_"};
constexpr std::string_view punctuation[] = {
    "<<%=", "<<|=", "<<=", ">>=", "+%=", "-%=", "*%=", "+|=", "-|=", "*|=", "<<%", "<<|", "..=", "=>",
    ":=",   "?.",   "??",  "<<",  ">>",  "..",  "<=",  ">=",  "==",  "!=",  "&&",  "||",  "+=",  "-=",
    "*=",   "/=",   "%=",  "&=",  "^=",  "|=",  "++",  "--",  "+%",  "-%",  "*%",  "+|",  "-|",  "*|"};

class Lexer {
public:
  explicit Lexer(const Source &source) : source(source), input(source.bytes) {}
  void run(std::vector<Token> &tokens, bool indexOnly);

private:
  const Source &source;
  std::string_view input;
  std::size_t pos = 0;
  bool newline = false;

  char peek() const { return pos == input.size() ? '\0' : input[pos]; }
  bool starts(std::string_view prefix) const { return input.substr(pos).starts_with(prefix); }
  Span span(std::size_t begin) const { return {&source, begin, pos}; }
  void trivia();
  Token number(std::size_t begin);
  Token quoted(std::size_t begin);
};
} // namespace

Diagnostic::Diagnostic(Span span, std::string code, std::string message)
    : std::runtime_error(std::move(message)), span(span), code(std::move(code)) {
}

void fail(Span span, std::string_view message, std::string_view code) {
  throw Diagnostic(span, std::string(code), std::string(message));
}

void printDiagnostic(const Diagnostic &error) {
  const auto &s = error.span;
  if (!s.source) {
    std::cerr << "vaqc: error[" << error.code << "]: " << error.what() << '\n';
    return;
  }
  const auto &bytes = s.source->bytes;
  std::size_t line = 1, start = 0, column = 1;
  for (std::size_t i = 0; i < s.begin; ++i) {
    if (bytes[i] == '\n') {
      ++line;
      start = i + 1;
      column = 1;
    } else if ((static_cast<unsigned char>(bytes[i]) & 0xc0) != 0x80)
      ++column;
  }
  std::cerr << s.source->path << ':' << line << ':' << column << ": error[" << error.code
            << "]: " << error.what() << '\n';
  auto end = bytes.find('\n', start);
  std::cerr << bytes.substr(start, end - start) << '\n';
  for (std::size_t i = start; i < s.begin; ++i) {
    if ((static_cast<unsigned char>(bytes[i]) & 0xc0) != 0x80) std::cerr << (bytes[i] == '\t' ? '\t' : ' ');
  }
  std::cerr << "^\n";
}

char32_t scalar(std::string_view bytes, std::size_t &index, Span span) {
  const auto invalid = [&] { fail(span, "invalid UTF-8", "E100"); };
  if (index >= bytes.size()) invalid();
  auto lead = static_cast<unsigned char>(bytes[index++]);
  if (lead < 0x80) return lead;
  unsigned n;
  char32_t value, minimum;
  if (lead >= 0xc2 && lead <= 0xdf) {
    n = 1;
    value = lead & 0x1f;
    minimum = 0x80;
  } else if (lead >= 0xe0 && lead <= 0xef) {
    n = 2;
    value = lead & 0xf;
    minimum = 0x800;
  } else if (lead >= 0xf0 && lead <= 0xf4) {
    n = 3;
    value = lead & 7;
    minimum = 0x10000;
  } else {
    invalid();
    return 0;
  }
  while (n--) {
    if (index >= bytes.size()) invalid();
    auto next = static_cast<unsigned char>(bytes[index++]);
    if ((next & 0xc0) != 0x80) invalid();
    value = (value << 6) | (next & 0x3f);
  }
  if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) invalid();
  return value;
}

void appendUtf8(std::string &out, char32_t c) {
  if (c < 0x80) {
    out.push_back(static_cast<char>(c));
    return;
  }
  if (c < 0x800)
    out.push_back(static_cast<char>(0xc0 | (c >> 6)));
  else if (c < 0x10000) {
    out.push_back(static_cast<char>(0xe0 | (c >> 12)));
    out.push_back(static_cast<char>(0x80 | ((c >> 6) & 63)));
  } else {
    out.push_back(static_cast<char>(0xf0 | (c >> 18)));
    out.push_back(static_cast<char>(0x80 | ((c >> 12) & 63)));
    out.push_back(static_cast<char>(0x80 | ((c >> 6) & 63)));
  }
  out.push_back(static_cast<char>(0x80 | (c & 63)));
}

namespace {
void Lexer::trivia() {
  while (pos < input.size()) {
    char c = peek();
    if (std::string_view(" \t\r\n\f\v").find(c) != std::string_view::npos) {
      newline |= c == '\n' || c == '\r';
      ++pos;
    } else if (starts("//")) {
      while (pos < input.size() && peek() != '\n' && peek() != '\r')
        ++pos;
    } else if (starts("/*")) {
      auto end = input.find("*/", pos + 2);
      if (end == input.npos) fail(span(pos), "unterminated block comment", "E101");
      newline |= input.substr(pos, end - pos).find_first_of("\r\n") != input.npos;
      pos = end + 2;
    } else
      break;
  }
}

Token Lexer::number(std::size_t begin) {
  int base = 10;
  if (starts("0b")) base = 2;
  if (starts("0o")) base = 8;
  if (starts("0x")) base = 16;
  if (base != 10) pos += 2;
  auto digits = [&](int radix) {
    if (digit(peek()) >= radix) fail(span(begin), "expected digit in numeric literal", "E102");
    while (digit(peek()) < radix) {
      ++pos;
      if (peek() == '_') {
        ++pos;
        if (digit(peek()) >= radix) fail(span(begin), "misplaced numeric separator", "E102");
      }
    }
  };
  digits(base);
  bool floating = false;
  if (base == 10) {
    if (peek() == '.' && pos + 1 < input.size() && digit(input[pos + 1]) < 10) {
      floating = true;
      ++pos;
      digits(10);
    }
    if (peek() == 'e' || peek() == 'E') {
      floating = true;
      ++pos;
      if (peek() == '+' || peek() == '-') ++pos;
      digits(10);
    }
  }
  auto suffix = pos;
  while (pos < input.size()) {
    auto next = pos;
    if (!contains(xidContinue, scalar(input, next, span(pos)))) break;
    pos = next;
  }
  auto tail = input.substr(suffix, pos - suffix);
  bool floatSuffix = tail == "f32" || tail == "f64";
  bool intSuffix = tail.size() > 1 && (tail[0] == 'i' || tail[0] == 'u') && tail[1] >= '1' &&
                   tail[1] <= '9' &&
                   std::ranges::all_of(tail.substr(1), [](char c) { return digit(c) < 10; });
  if (!tail.empty() && !(base == 10 && floatSuffix) && !(!floating && intSuffix))
    fail(span(begin), "invalid digit or numeric suffix", "E102");
  std::string spelling(input.substr(begin, suffix - begin));
  std::erase(spelling, '_');
  if (!tail.empty()) spelling += ':' + std::string(tail);
  return {TokenKind::Number, std::move(spelling), span(begin), newline};
}

Token Lexer::quoted(std::size_t begin) {
  char quote = input[pos++];
  std::string output;
  unsigned count = 0;
  while (pos < input.size() && peek() != quote) {
    auto start = pos;
    auto c = scalar(input, pos, span(start));
    if (c == '\n' || c == '\r') fail(span(begin), "newline in literal", "E103");
    if (c == '\\') {
      if (pos == input.size()) fail(span(begin), "unterminated literal", "E103");
      switch (input[pos++]) {
      case 'n':
        c = '\n';
        break;
      case 't':
        c = '\t';
        break;
      case '\\':
        c = '\\';
        break;
      case '\'':
        c = '\'';
        break;
      case '"':
        c = '"';
        break;
      case 'u': {
        if (peek() != '{') fail(span(start), "expected '{' in Unicode escape", "E103");
        ++pos;
        if (digit(peek()) >= 16) fail(span(start), "empty Unicode escape", "E103");
        c = 0;
        while (digit(peek()) < 16) {
          c = 16 * c + digit(input[pos++]);
          if (c > 0x10ffff) fail(span(start), "invalid Unicode scalar", "E103");
        }
        if (peek() != '}' || (c >= 0xd800 && c <= 0xdfff))
          fail(span(start), "invalid Unicode scalar escape", "E103");
        ++pos;
        break;
      }
      default:
        fail(span(start), "unknown escape", "E103");
      }
    }
    appendUtf8(output, c);
    ++count;
  }
  if (pos == input.size()) fail(span(begin), "unterminated literal", "E103");
  ++pos;
  if (quote == '\'' && count != 1)
    fail(span(begin), "character literal must contain one Unicode scalar", "E103");
  return {quote == '\'' ? TokenKind::Character : TokenKind::Text, std::move(output), span(begin), newline};
}

void Lexer::run(std::vector<Token> &tokens, bool indexOnly) {
  if (!indexOnly)
  for (std::size_t i = 0; i < input.size();)
    scalar(input, i, {&source, i, i + 1});
  for (;;) {
    newline = false;
    trivia();
    auto begin = pos;
    if (pos == input.size()) {
      tokens.push_back({TokenKind::End, "", span(begin), newline});
      return;
    }
    if (digit(peek()) < 10) {
      tokens.push_back(number(begin));
      continue;
    }
    if (peek() == '"' || peek() == '\'') {
      tokens.push_back(quoted(begin));
      continue;
    }
    auto next = pos;
    auto c = scalar(input, next, span(begin));
    if (c == '_' || contains(xidStart, c)) {
      pos = next;
      while (pos < input.size()) {
        next = pos;
        if (!contains(xidContinue, scalar(input, next, span(pos)))) break;
        pos = next;
      }
      auto word = normalize(input.substr(begin, pos - begin), span(begin));
      auto kind =
          std::ranges::find(keywords, word) == std::end(keywords) ? TokenKind::Name : TokenKind::Keyword;
      tokens.push_back({kind, std::move(word), span(begin), newline});
      continue;
    }
    std::string_view matched;
    for (auto op : punctuation)
      if (starts(op)) {
        matched = op;
        break;
      }
    if (matched.empty() && c < 128 &&
        std::string_view("()[]{};,:.#?+-*/%<>=!~^&|").find(static_cast<char>(c)) != std::string_view::npos)
      matched = input.substr(pos, 1);
    if (matched.empty()) fail(span(begin), c == '@' ? "'@' is reserved" : "unexpected character", "E104");
    pos += matched.size();
    tokens.push_back({TokenKind::Punctuation, std::string(matched), span(begin), newline});
  }
}
} // namespace

std::vector<Token> lex(const Source &source, bool indexOnly) {
  std::vector<Token> tokens;
  try {
    Lexer(source).run(tokens, indexOnly);
  } catch (const Diagnostic &d) {
    if (!indexOnly) throw;
    tokens.push_back({TokenKind::End, "", d.span});
  }
  return tokens;
}

std::string identifier(std::string_view spelling) {
  Source source{"", std::string(spelling)};
  auto tokens = lex(source, true);
  if (tokens.size() == 2 && tokens[0].kind == TokenKind::Name &&
      tokens[0].span.begin == 0 && tokens[0].span.end == spelling.size()) return tokens[0].text;
  return {};
}
} // namespace vqr
