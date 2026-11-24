#include "syntax.hpp"

#include <algorithm>
#include <initializer_list>
#include <filesystem>
#include <iostream>
#include <utility>

namespace vqr {
namespace {

Ast make(NodeKind kind, Span span, std::string text = {}, std::vector<Ast> children = {}) {
  auto node = std::make_shared<Node>();
  node->kind = kind;
  node->span = span;
  node->text = std::move(text);
  node->children = std::move(children);
  return node;
}

struct Operator {
  std::string_view text;
  int precedence;
};
constexpr Operator operators[] = {{"catch", 1}, {"??", 2},  {"||", 3},   {"&&", 4},  {"|", 5},   {"^", 6},
                                  {"&", 7},     {"==", 8},  {"!=", 8},   {"<", 9},   {"<=", 9},  {">", 9},
                                  {">=", 9},    {"..", 10}, {"..=", 10}, {"<<", 11}, {">>", 11}, {"<<%", 11},
                                  {"+", 12},    {"-", 12},  {"+%", 12},  {"-%", 12},
                                  {"*", 13},    {"/", 13},  {"%", 13},   {"*%", 13}};
constexpr std::string_view assignments[] = {
    "=",  "+=",  "-=",  "*=",   "/=",  "%=",  "<<=", ">>=",  "&=", "|=",
    "^=", "+%=",   "-%=", "*%=", "<<%=", "++", "--"};

class Parser {
public:
  explicit Parser(std::vector<Token> tokens) : tokens(std::move(tokens)) {}
  File file();

private:
  std::vector<Token> tokens;
  std::size_t pos = 0;
  unsigned split = 0, depth = 0;
  bool armExpression = false;

  struct Nest {
    Parser &p;
    explicit Nest(Parser &p) : p(p) {
      if (++p.depth > 256) fail(p.current().span, "syntax nesting limit exceeded", "E110");
    }
    ~Nest() { --p.depth; }
  };

  Token current(std::size_t ahead = 0) const {
    auto t = tokens.at(std::min(pos + ahead, tokens.size() - 1));
    if (split && !ahead) {
      t.text.erase(0, split);
      t.span.begin += split;
    }
    return t;
  }
  bool at(std::string_view text) const {
    auto t = current();
    return (t.kind == TokenKind::Punctuation || t.kind == TokenKind::Keyword) && t.text == text;
  }
  bool atWord(std::string_view text) const {
    auto t = current();
    return t.kind == TokenKind::Name && t.text == text;
  }
  bool eatWord(std::string_view text) {
    if (!atWord(text)) return false;
    take();
    return true;
  }
  Token take() {
    auto t = current();
    if (t.kind == TokenKind::End) fail(t.span, "unexpected end of file", "E110");
    ++pos;
    split = 0;
    return t;
  }
  bool eat(std::string_view text) {
    if (!at(text)) return false;
    take();
    return true;
  }
  Token require(std::string_view text) {
    if (!at(text)) fail(current().span, "expected '" + std::string(text) + "'", "E110");
    return take();
  }
  Token name(bool ignore = false) {
    if (current().kind != TokenKind::Name && !(ignore && at("_")))
      fail(current().span, "expected an identifier", "E110");
    return take();
  }
  Token bindingName(bool ignore = true) {
    if (!eat("&")) return name(ignore);
    auto t = name();
    t.text = "&" + t.text;
    return t;
  }
  bool typePunctuation(char c) {
    auto t = current();
    if (t.kind != TokenKind::Punctuation || t.text.empty() || t.text[0] != c) return false;
    if (t.text.size() == 1)
      take();
    else
      ++split;
    return true;
  }

  template <class Read> void list(std::string_view end, Read read) {
    if (!at(end)) {
      do {
        if (at(end)) break;
        read();
      } while (eat(","));
    }
    require(end);
  }
  bool closesGeneric() const { return current().text.starts_with(">"); }
  void endGeneric() {
    if (!typePunctuation('>')) fail(current().span, "expected '>'", "E110");
  }
  std::vector<Ast> args(std::string_view end) {
    std::vector<Ast> items;
    list(end, [&] { items.push_back(expression()); });
    return items;
  }

  std::vector<Attribute> attributes();
  Decl declaration(bool member = false, bool iface = false);
  void members(std::vector<Decl> &out, bool iface);
  void parameters(Decl &d);
  void genericParameters(Decl &d);
  Ast type(bool inferArray = false);
  Ast parameterType();
  std::vector<Ast> genericArguments();
  Ast block();
  Ast statement(bool header = false);
  Ast expression(int minimum = 1, bool excludeFields = false);
  Ast primary(bool excludeFields);
  Ast lambda(bool excludeFields);
  Ast postfix(Ast base, bool excludeFields);
  Ast loop();
  Ast match();
  Ast selection();
  Ast pattern();
  bool bindingAhead(bool iteration = false) const;
  bool wrappedLoopHeader() const;
};

File Parser::file() {
  File f;
  f.span = current().span;
  if (eat("package")) {
    f.package = name().text;
    eat(";");
  }
  while (eat("import")) {
    require("(");
    if (at(")")) fail(current().span, "import requires at least one item", "E110");
    list(")", [&] {
      Import item;
      item.span = current().span;
      if (current().kind == TokenKind::Text) {
        item.filePath = true;
        item.path = take().text;
        std::filesystem::path target(item.path);
        if (target.is_absolute() || target.extension() != ".vqr" || item.path.find('\0') != item.path.npos)
          fail(item.span, "file import requires a relative .vqr path", "E110");
        if (eat("as")) item.alias = name().text;
        else {
          item.alias = identifier(target.stem().string());
          if (item.alias.empty()) fail(item.span, "file import requires an explicit identifier alias", "E110");
        }
      } else {
        item.path = item.alias = name().text;
        while (eat("/")) {
          item.alias = name().text;
          item.path += '/' + item.alias;
        }
        if (eat("as")) item.alias = name().text;
      }
      f.imports.push_back(std::move(item));
    });
    eat(";");
  }
  while (current().kind != TokenKind::End)
    f.declarations.push_back(declaration());
  return f;
}

std::vector<Attribute> Parser::attributes() {
  std::vector<Attribute> out;
  while (at("#")) {
    take();
    auto t = at("move") ? take() : name();
    if (t.text == "return_borrow")
      fail(t.span, "use 'from name' after the return type to specify a borrow source", "E110");
    require(":");
    if (t.text == "implements") {
      Attribute attribute{t.text, t.span, ""};
      do {
        attribute.types.push_back(type());
      } while (eat(","));
      eat(";");
      out.push_back(std::move(attribute));
      continue;
    }
    if (t.text == "serde_name") {
      auto value = take();
      if (value.kind != TokenKind::Text) fail(value.span, "expected a serialized field name", "E110");
      eat(";");
      out.push_back({t.text, t.span, std::move(value.text)});
      continue;
    }
    std::string value;
    while (current().kind != TokenKind::End && !current().newline && !at(";") && !at("}"))
      value += take().text;
    if (value.empty()) fail(t.span, "attribute requires a value", "E110");
    eat(";");
    out.push_back({t.text, t.span, value});
  }
  return out;
}

void Parser::parameters(Decl &d) {
  require("(");
  list(")", [&] {
    auto start = d.params.size();
    bool comptime = eat("comptime");
    do {
      auto t = name(true);
      d.params.push_back({t.text, t.span, nullptr, comptime});
    } while (!at(":") && eat(","));
    require(":");
    auto t = parameterType();
    for (; start < d.params.size(); ++start)
      d.params[start].type = t;
  });
}

void Parser::genericParameters(Decl &d) {
  if (!eat("<")) return;
  do {
    if (closesGeneric()) break;
    bool c = eat("comptime");
    auto t = name();
    Ast constraint;
    if (eat(":")) {
      constraint = type();
      if (!c) {
        auto current = constraint;
        while (eat("+")) {
          current->tail = type();
          current = current->tail;
        }
        if (at("&")) fail(this->current().span, "use '+' to combine trait constraints", "E110");
      }
    }
    if (c && !constraint) fail(t.span, "comptime parameter requires a type", "E110");
    d.generics.push_back({t.text, t.span, constraint, c});
  } while (eat(","));
  endGeneric();
}

Decl Parser::declaration(bool member, bool iface) {
  Nest nesting(*this);
  Decl d{};
  d.span = current().span;
  d.attributes = attributes();
  d.visible = eat("pub") || iface;
  d.comptime = eat("comptime");
  d.unsafe = eat("unsafe");
  d.external = eat("extern");
  d.exported = !d.external && eat("export");
  if (d.external || d.exported) {
    auto abi = take();
    if (abi.kind != TokenKind::Text || abi.text != "C") fail(abi.span, "expected C ABI", "E110");
  }
  d.associated = member && eat("static");
  bool isConst = eat("const");
  bool isOperator = member && eat("operator");
  if (isOperator || eat("func")) {
    d.kind = DeclKind::Function;
    d.readOnly = isConst;
    auto n = (isOperator || at("select") || (d.associated && at("new"))) ? take() : name();
    d.name = isOperator ? "operator" + n.text : n.text;
    if (!isOperator) d.nameSpan = n.span;
    genericParameters(d);
    parameters(d);
    if (!(member && n.text == "init" && (at(":") || at("{"))))
      d.type = type();
    else
      d.type = make(NodeKind::TypeName, n.span, "void");
    if (eatWord("from")) d.borrowSource = at("self") ? take().text : name().text;
    if (eat(":")) {
      do {
        auto field = at("self") ? take() : name();
        require("(");
        auto values = args(")");
        d.initializers.emplace_back(field.text, make(NodeKind::Tuple, field.span, "", std::move(values)));
      } while (eat(","));
    }
    if (iface || d.external)
      require(";");
    else {
      d.body = block();
      eat(";");
    }
    return d;
  }
  if (isConst || eat("var") || (member && (d.embed = eat("embed")))) {
    d.kind = member ? DeclKind::Field : DeclKind::Global;
    d.readOnly = isConst;
    if (d.embed && !(current().kind == TokenKind::Name && current(1).text == ":")) {
      d.type = type();
      auto dot = d.type->text.rfind('.');
      d.name = d.type->text.substr(dot == std::string::npos ? 0 : dot + 1);
    } else {
      auto id = name();
      d.name = id.text;
      d.nameSpan = id.span;
      if (eat(":")) d.type = type();
    }
    if (eat("=")) d.body = expression();
    if (!d.type && !d.body) fail(d.span, "binding requires a type or initializer", "E110");
    require(";");
    return d;
  }
  if (member) fail(current().span, "expected class or interface member", "E110");
  if (eat("type")) {
    d.kind = DeclKind::Alias;
    auto id = name();
    d.name = id.text;
    d.nameSpan = id.span;
    genericParameters(d);
    require("=");
    d.type = type();
    require(";");
    return d;
  }
  if (eat("error"))
    d.kind = DeclKind::Error;
  else if (eat("enum"))
    d.kind = DeclKind::Enum;
  else if (eat("class"))
    d.kind = DeclKind::Class;
  else if (eat("interface"))
    d.kind = DeclKind::Interface;
  else
    fail(current().span, "expected a declaration", "E110");
  auto id = name();
  d.name = id.text;
  d.nameSpan = id.span;
  genericParameters(d);
  require("{");
  auto inside = attributes();
  d.attributes.insert(d.attributes.end(), inside.begin(), inside.end());
  if (d.kind == DeclKind::Enum || d.kind == DeclKind::Error) {
    list("}", [&] {
      auto v = name();
      Decl m{};
      m.kind = DeclKind::Variant;
      m.span = v.span;
      m.name = v.text;
      if (d.kind == DeclKind::Enum && eat("("))
        list(")", [&] { m.params.push_back({"", current().span, type(), false}); });
      if (d.kind == DeclKind::Enum && eat("=")) m.body = expression();
      d.members.push_back(std::move(m));
    });
  } else {
    members(d.members, d.kind == DeclKind::Interface);
  }
  eat(";");
  return d;
}

void Parser::members(std::vector<Decl> &out, bool iface) {
  Nest nesting(*this);
  while (!eat("}")) {
    if (at("public") || at("private"))
      fail(current().span, "use 'pub' on each public member; class members are private by default", "E110");
    if (!iface && at("comptime") && current(1).kind == TokenKind::Keyword && current(1).text == "if") {
      Decl conditional{};
      conditional.kind = DeclKind::Conditional;
      conditional.span = take().span;
      require("if");
      conditional.body = expression(1, true);
      require("{");
      members(conditional.members, false);
      if (eat("else")) {
        require("{");
        members(conditional.otherwise, false);
      }
      out.push_back(std::move(conditional));
      continue;
    }
    auto member = declaration(true, iface);
    if (iface && (member.kind != DeclKind::Function || member.external || member.exported || member.associated))
      fail(member.span, "an interface member must be an instance method", "E110");
    out.push_back(std::move(member));
  }
}

std::vector<Ast> Parser::genericArguments() {
  require("<");
  std::vector<Ast> items;
  do {
    if (closesGeneric()) break;
    if (current().kind == TokenKind::Number || current().kind == TokenKind::Character ||
        at("-") || at("true") || at("false"))
      items.push_back(expression(14));
    else if (at("(")) {
      auto oldPos = pos;
      auto oldSplit = split;
      try {
        items.push_back(type());
      } catch (const Diagnostic &) {
        pos = oldPos;
        split = oldSplit;
        require("(");
        items.push_back(expression());
        require(")");
      }
    } else
      items.push_back(type());
  } while (eat(","));
  endGeneric();
  return items;
}

Ast Parser::parameterType() {
  auto start = current();
  if (!eat("in") && !eatWord("mut") && !eat("move")) return type();
  auto data = type();
  if (data->kind == NodeKind::TypePrefix && (data->text == "&" || data->text == "&const"))
    fail(start.span, "write the data type directly after the parameter mode", "E110");
  return make(NodeKind::TypePrefix, start.span, start.text, {data});
}

Ast Parser::type(bool inferArray) {
  Nest nesting(*this);
  auto start = current();
  if (at("in") || atWord("mut") || at("move"))
    fail(start.span, "parameter mode is only permitted in parameter lists", "E110");
  if (eatWord("ref")) {
    bool writable = eatWord("mut");
    return make(NodeKind::TypePrefix, start.span, writable ? "ref mut" : "ref", {type()});
  }
  if (typePunctuation('?') || eat("!") || eat("^") || eat("*") || typePunctuation('&') || eat("const")) {
    auto key = start.text == "??" ? "?" : start.text == "&&" ? "&" : start.text;
    if (key == "*" && eat("const")) key = "*const";
    if (key == "&" && eat("const")) key = "&const";
    auto inner = type();
    const auto isWrapper = [](const Ast &node) {
      return node->kind == NodeKind::TypePrefix && (node->text == "?" || node->text == "!");
    };
    // Grouping parentheses have already been removed by type(). A named
    // alias, unlike parentheses, stops the directly written wrapper chain.
    if ((key == "?" || key == "!") && isWrapper(inner) && isWrapper(inner->children.front()))
      fail(inner->children.front()->span, "at most two consecutive type wrappers; use a type alias for deeper nesting",
           "E110");
    return make(NodeKind::TypePrefix, start.span, key, {inner});
  }
  if (eat("[")) {
    if (eat("]")) {
      auto key = eat("const") ? "[]const" : "[]";
      return make(NodeKind::TypePrefix, start.span, key, {type()});
    }
    Ast count;
    if (eat("_")) {
      if (!inferArray) fail(start.span, "inferred array length requires an array constructor", "E111");
    } else
      count = expression();
    require("]");
    return make(NodeKind::TypeArray, start.span, "", {count, type()});
  }
  if (eat("(")) {
    if (at(")")) fail(start.span, "empty tuple type", "E110");
    auto first = type();
    if (eat(")")) return first;
    require(",");
    std::vector<Ast> parts{first};
    list(")", [&] { parts.push_back(type()); });
    return make(NodeKind::TypeTuple, start.span, "", std::move(parts));
  }
  bool c = eat("extern");
  if (c) {
    auto abi = take();
    if (abi.kind != TokenKind::Text || abi.text != "C") fail(abi.span, "expected C ABI", "E110");
  }
  if (c || at("func")) {
    require("func");
    require("(");
    std::vector<Ast> types;
    std::vector<std::string> parameters;
    list(")", [&] {
      std::string parameter;
      if (current().kind == TokenKind::Name && current(1).text == ":") {
        parameter = take().text;
        require(":");
        if (std::ranges::find(parameters, parameter) != parameters.end())
          fail(current().span, "duplicate function type parameter", "E110");
      }
      parameters.push_back(std::move(parameter));
      types.push_back(parameterType());
    });
    auto n = make(NodeKind::TypeFunction, start.span, c ? "C" : "", std::move(types));
    if (atWord("borrow") && current(1).text == "(")
      fail(current().span, "use named parameters and 'from name' to specify a borrow source", "E110");
    n->type = type();
    if (eatWord("from")) {
      auto source = name();
      auto parameter = std::ranges::find(parameters, source.text);
      if (!n->names.empty() || parameter == parameters.end())
        fail(source.span, "from must identify one named function type parameter", "E110");
      auto index = static_cast<std::size_t>(parameter - parameters.begin());
      auto mode = parameterMode(n->children[index]);
      if (mode != ParameterMode::In && mode != ParameterMode::Mut)
        fail(source.span, "from must identify an in or mut parameter", "E230");
      n->names.push_back(std::to_string(index + 1));
      n->bindingSpans.push_back(source.span);
    }
    return n;
  }
  auto id = name();
  auto leaf = id.span;
  while (eat(".")) {
    auto member = name();
    leaf = member.span;
    id.text += '.' + member.text;
  }
  std::vector<Ast> types;
  if (at("<")) types = genericArguments();
  auto result = make(NodeKind::TypeName, id.span, id.text, std::move(types));
  result->bindingSpans.push_back(leaf);
  return result;
}

bool Parser::bindingAhead(bool iteration) const {
  std::size_t i = 0;
  do {
    bool reference = current(i).text == "&";
    if (reference) ++i;
    auto t = current(i++);
    if (reference && t.kind != TokenKind::Name) return false;
    if (t.kind != TokenKind::Name && t.text != "_") return false;
  } while (current(i).text == "," && ++i);
  return current(i).text == (iteration ? "in" : ":=");
}

Ast Parser::statement(bool header) {
  if (atWord("ref") && (current(2).text == "=" ||
                    (current(1).text == "mut" && current(3).text == "="))) {
    auto start = take();
    bool writable = eatWord("mut");
    auto binding = name();
    require("=");
    auto n = make(NodeKind::Binding, start.span, "var",
                  {make(NodeKind::Unary, start.span, writable ? "ref mut" : "ref", {expression(1, header)})});
    n->names.push_back(binding.text);
    n->bindingSpans.push_back(binding.span);
    return n;
  }
  if (at("var") || at("const") || at("comptime")) {
    auto t = take();
    if (t.text == "comptime") require("const");
    auto n = make(NodeKind::Binding, t.span, t.text);
    auto binding = t.text == "var" ? bindingName() : name();
    n->names.push_back(binding.text);
    n->bindingSpans.push_back(binding.span);
    if (eat(":")) {
      if (binding.text.starts_with('&'))
        fail(binding.span, "use 'var name: &T = ...' for a typed reference", "E110");
      n->type = type();
    }
    if (eat("=")) n->children.push_back(expression());
    if (!n->type && n->children.empty()) fail(t.span, "binding requires a type or initializer", "E110");
    if (n->children.empty() && t.text != "var") fail(t.span, "constant requires an initializer", "E110");
    return n;
  }
  if (bindingAhead()) {
    auto n = make(NodeKind::Binding, current().span, ":=");
    do {
      auto binding = bindingName();
      n->names.push_back(binding.text);
      n->bindingSpans.push_back(binding.span);
    } while (eat(","));
    require(":=");
    n->children.push_back(expression());
    return n;
  }
  auto left = expression(1, header);
  auto s = current();
  if (s.kind == TokenKind::Punctuation && std::ranges::find(assignments, s.text) != std::end(assignments)) {
    take();
    std::vector<Ast> items{left};
    if (s.text != "++" && s.text != "--") items.push_back(expression(1, header));
    return make(NodeKind::Assign, s.span, s.text, std::move(items));
  }
  return left;
}

Ast Parser::block() {
  Nest nesting(*this);
  auto t = require("{");
  auto block = make(NodeKind::Block, t.span);
  auto previous = std::exchange(armExpression, false);
  while (!eat("}")) {
    auto n = statement();
    bool semicolon = eat(";");
    if (n->kind == NodeKind::Binding || n->kind == NodeKind::Assign || n->kind == NodeKind::Return ||
        n->kind == NodeKind::Break || n->kind == NodeKind::Continue) {
      if (!semicolon && !at("}")) fail(current().span, "expected ';' after statement", "E110");
      if (!semicolon && n->kind != NodeKind::Break && n->kind != NodeKind::Continue &&
          n->kind != NodeKind::Return)
        fail(current().span, "expected ';' after statement", "E110");
    } else if (!semicolon && at("}") && n->kind != NodeKind::For && n->kind != NodeKind::ForIn) {
      block->tail = n;
      require("}");
      break;
    } else if (!semicolon && n->kind != NodeKind::Block && n->kind != NodeKind::If &&
               n->kind != NodeKind::Switch && n->kind != NodeKind::Unsafe && n->kind != NodeKind::For &&
               n->kind != NodeKind::ForIn) {
      fail(current().span, "expected ';' after expression", "E110");
    }
    block->children.push_back(n);
  }
  armExpression = previous;
  return block;
}

bool Parser::wrappedLoopHeader() const {
  if (!at("(")) return false;
  unsigned nesting = 0;
  for (std::size_t i = pos; i + 1 < tokens.size(); ++i) {
    const auto &t = tokens[i];
    if (t.kind != TokenKind::Punctuation) continue;
    if (t.text == "(") ++nesting;
    if (t.text == ")" && --nesting == 0) {
      const auto &next = tokens[i + 1];
      return next.kind == TokenKind::Punctuation && next.text == "{";
    }
  }
  return false;
}

Ast Parser::loop() {
  if (atWord("while")) {
    auto start = take();
    auto condition = expression(1, true);
    return make(NodeKind::For, start.span, "", {nullptr, condition, nullptr, block()});
  }
  auto t = require("for");
  bool parens = wrappedLoopHeader();
  if (parens) require("(");
  if (!parens && at("{")) return make(NodeKind::For, t.span, "", {nullptr, nullptr, nullptr, block()});
  if (bindingAhead(true)) {
    auto n = make(NodeKind::ForIn, t.span);
    do {
      auto binding = bindingName();
      n->names.push_back(binding.text);
      n->bindingSpans.push_back(binding.span);
    } while (eat(","));
    require("in");
    n->children.push_back(expression(1, !parens));
    if (parens) require(")");
    n->children.push_back(block());
    return n;
  }
  Ast first, condition, step;
  if (!at(";")) first = statement(!parens);
  if (eat(";")) {
    if (!at(";")) condition = expression();
    require(";");
    if (!at(parens ? ")" : "{")) step = statement(!parens);
  } else {
    condition = first;
    first.reset();
  }
  if (parens) require(")");
  return make(NodeKind::For, t.span, "", {first, condition, step, block()});
}

Ast Parser::pattern() {
  Nest nesting(*this);
  auto t = current();
  if (eat("_")) return make(NodeKind::PatternAny, t.span);
  if (eat("&")) {
    auto binding = name();
    return make(NodeKind::PatternIndirect, binding.span, binding.text);
  }
  if (t.kind == TokenKind::Name && t.text == "ref" && current(1).kind == TokenKind::Name) {
    take();
    const bool writable = eatWord("mut");
    auto binding = name();
    return make(writable ? NodeKind::PatternMutRef : NodeKind::PatternRef, binding.span, binding.text);
  }
  if (eat("null")) return make(NodeKind::PatternCase, t.span, "null");
  if (eat("(")) {
    auto p = make(NodeKind::PatternTuple, t.span);
    list(")", [&] { p->children.push_back(pattern()); });
    return p;
  }
  if (t.kind == TokenKind::Name) {
    take();
    auto key = t.text;
    while (eat("."))
      key += '.' + name().text;
    if (!at("(") && key == t.text) return make(NodeKind::PatternName, t.span, key);
    auto p = make(NodeKind::PatternCase, t.span, key);
    if (eat("(")) list(")", [&] { p->children.push_back(pattern()); });
    return p;
  }
  return make(NodeKind::PatternValue, t.span, "", {expression(10, true)});
}

Ast Parser::match() {
  auto t = require("switch");
  auto n = make(NodeKind::Switch, t.span, "", {expression(1, true)});
  require("{");
  while (!eat("}")) {
    auto a = make(NodeKind::Arm, current().span);
    if (eat("else"))
      a->children.push_back(make(NodeKind::PatternAny, a->span));
    else
      do {
        a->children.push_back(pattern());
      } while (eat(","));
    require("=>");
    auto previous = std::exchange(armExpression, true);
    a->tail = expression();
    armExpression = previous;
    n->children.push_back(a);
    if (!eat(",") && !at("}")) fail(current().span, "expected ',' after switch arm", "E110");
  }
  return n;
}

Ast Parser::selection() {
  auto token = require("select");
  auto result = make(NodeKind::Select, token.span);
  require("{");
  bool hasElse = false;
  while (!eat("}")) {
    auto arm = make(NodeKind::Arm, current().span);
    if (eat("else")) {
      if (hasElse) fail(arm->span, "select has more than one else arm", "E110");
      hasElse = true;
      arm->text = "else";
    } else {
      if (current().kind == TokenKind::Name && current(1).text == ":=") {
        auto binding = take();
        arm->names.push_back(binding.text);
        arm->bindingSpans.push_back(binding.span);
        require(":=");
      }
      auto call = expression(1, true);
      if (call->kind != NodeKind::Call) fail(call->span, "select arm must call recv, send, or after", "E110");
      auto target = call->children[0];
      if (target->kind == NodeKind::Member && target->text == "recv" && call->children.size() == 1) {
        arm->text = "recv";
        arm->children.push_back(target->children[0]);
      } else if (target->kind == NodeKind::Member && target->text == "send" && call->children.size() == 2 &&
                 arm->names.empty()) {
        arm->text = "send";
        arm->children = {target->children[0], call->children[1]};
      } else if (target->kind == NodeKind::Name && target->text == "after" && call->children.size() == 2 &&
                 arm->names.empty()) {
        arm->text = "after";
        arm->children.push_back(call->children[1]);
      } else {
        fail(call->span, "invalid select arm", "E110");
      }
    }
    require("=>");
    auto previous = std::exchange(armExpression, true);
    arm->tail = expression();
    armExpression = previous;
    result->children.push_back(arm);
    if (!eat(",") && !at("}")) fail(current().span, "expected ',' after select arm", "E110");
  }
  if (result->children.empty()) fail(token.span, "select requires at least one arm", "E110");
  return result;
}

Ast Parser::lambda(bool excludeFields) {
  auto start = current();
  if (start.kind == TokenKind::Name && current(1).text == "=>") {
    take();
    require("=>");
    auto node = make(NodeKind::Lambda, start.span);
    node->parameters.push_back({start.text, start.span, nullptr, false});
    node->tail = expression(1, excludeFields);
    return node;
  }
  if (!at("[") && !at("(")) return nullptr;
  auto oldPos = pos;
  auto oldSplit = split;
  auto node = make(NodeKind::Lambda, start.span);
  try {
    if (eat("[")) {
      list("]", [&] {
        auto t = current();
        if (eat("move")) {
          require("(");
          auto id = name();
          node->children.push_back(
              make(NodeKind::Move, t.span, "", {make(NodeKind::Name, id.span, id.text)}));
          require(")");
        } else if (eat("&")) {
          auto id = name();
          node->children.push_back(
              make(NodeKind::Unary, t.span, "&", {make(NodeKind::Name, id.span, id.text)}));
        } else {
          auto id = name();
          node->children.push_back(make(NodeKind::Name, id.span, id.text));
        }
      });
    }
    require("(");
    std::size_t untyped = 0;
    list(")", [&] {
      auto param = name(true);
      node->parameters.push_back({param.text, param.span, nullptr, false});
      if (eat(":")) {
        auto t = parameterType();
        for (; untyped < node->parameters.size(); ++untyped)
          node->parameters[untyped].type = t;
      }
    });
    if (!at("=>")) node->type = type();
    require("=>");
  } catch (const Diagnostic &) {
    pos = oldPos;
    split = oldSplit;
    return nullptr;
  }
  node->tail = expression(1, excludeFields);
  return node;
}

Ast Parser::primary(bool excludeFields) {
  auto t = current();
  if (t.kind == TokenKind::Number || t.kind == TokenKind::Text || t.kind == TokenKind::Character) {
    take();
    auto k = t.kind == TokenKind::Number ? NodeKind::Number
             : t.kind == TokenKind::Text ? NodeKind::Text
                                         : NodeKind::Character;
    return make(k, t.span, t.text);
  }
  if (auto closure = lambda(excludeFields)) return closure;
  if (t.kind == TokenKind::Name && current(1).text == ":" &&
      (current(2).text == "for" || current(2).text == "while")) {
    auto label = take();
    require(":");
    auto result = loop();
    result->text = label.text;
    return result;
  }
  if (atWord("while")) return loop();
  if ((atWord("ref") || atWord("raw")) &&
      (current(1).kind == TokenKind::Name || current(1).text == "self" || current(1).text == "*")) {
    take();
    auto operation = t.text == "ref" && eatWord("mut") ? "ref mut" : t.text;
    return make(NodeKind::Unary, t.span, operation, {expression(14, excludeFields)});
  }
  if (t.kind == TokenKind::Name || at("self")) {
    take();
    return make(NodeKind::Name, t.span, t.text);
  }
  if (eat("true") || eat("false")) return make(NodeKind::Boolean, t.span, t.text);
  if (eat("null")) return make(NodeKind::Null, t.span);
  if (at("{")) return block();
  if (eat("(")) {
    auto n = expression();
    if (eat(")")) return n;
    require(",");
    std::vector<Ast> items{n};
    list(")", [&] { items.push_back(expression()); });
    return make(NodeKind::Tuple, t.span, "", std::move(items));
  }
  if (at("[")) {
    auto n = make(NodeKind::Array, t.span);
    n->type = type(true);
    if (n->type->kind != NodeKind::TypeArray) fail(t.span, "expected fixed array constructor", "E110");
    require("{");
    n->children = args("}");
    return n;
  }
  if (eat("if")) {
    if (eatWord("let")) {
      auto binding = pattern();
      require("=");
      auto subject = expression(1, true);
      auto yes = make(NodeKind::Arm, binding->span, "", {binding});
      yes->tail = block();
      auto no = make(NodeKind::Arm, t.span, "", {make(NodeKind::PatternAny, t.span)});
      if (eat("else")) no->tail = at("if") ? expression(1, excludeFields) : block();
      else no->tail = make(NodeKind::Block, t.span);
      return make(NodeKind::Switch, t.span, "", {subject, yes, no});
    }
    Ast cond;
    bool blockForm = !at("(");
    if (blockForm)
      cond = expression(1, true);
    else {
      auto start = pos;
      auto oldSplit = split;
      try {
        cond = expression(1, true);
        blockForm = at("{");
      } catch (const Diagnostic &) {
        blockForm = false;
      }
      if (!blockForm) {
        pos = start;
        split = oldSplit;
        require("(");
        cond = expression();
        require(")");
      }
    }
    auto yes = blockForm ? block() : expression(1, excludeFields);
    Ast no;
    if (!blockForm && !at("else")) fail(t.span, "single-expression if requires else", "E110");
    if (eat("else")) {
      if (at("if")) no = expression(1, excludeFields);
      else no = blockForm ? block() : expression(1, excludeFields);
      if (!blockForm && (yes->kind == NodeKind::Block || no->kind == NodeKind::Block))
        fail(t.span, "if branches must both be blocks or both be single expressions", "E110");
    }
    return make(NodeKind::If, t.span, "", {cond, yes, no});
  }
  if (at("switch")) return match();
  if (at("for")) return loop();
  if (eat("unsafe")) return make(NodeKind::Unsafe, t.span, "", {block()});
  if (eat("move")) {
    require("(");
    auto n = expression();
    require(")");
    return make(NodeKind::Move, t.span, "", {n});
  }
  if (eat("return")) {
    auto n = make(NodeKind::Return, t.span);
    if (!at(";") && !at("}") && !at(",")) {
      n->children.push_back(expression());
      if (!armExpression && at(",")) fail(current().span, "return a tuple using parentheses", "E110");
    }
    return n;
  }
  if (eat("break") || eat("continue"))
    return make(t.text == "break" ? NodeKind::Break : NodeKind::Continue, t.span,
                current().kind == TokenKind::Name && !current().newline ? take().text : "");
  if (at("!") || at("-") || at("~") || at("*") || at("&")) {
    take();
    return make(NodeKind::Unary, t.span, t.text, {expression(14, excludeFields)});
  }
  if (eat("new")) {
    auto n = make(NodeKind::New, t.span);
    if (eat("(")) {
      n->tail = expression();
      require(")");
    }
    n->type = type();
    require("(");
    n->children = args(")");
    return n;
  }
  if (at("select")) return selection();
  fail(t.span, "expected an expression", "E110");
}

Ast Parser::postfix(Ast base, bool excludeFields) {
  for (;;) {
    auto t = current();
    if (eat("(")) {
      auto arguments = args(")");
      arguments.insert(arguments.begin(), base);
      base = make(NodeKind::Call, t.span, "", std::move(arguments));
    } else if (eat(".") || eat("?.")) {
      auto member = (at("new") || at("select") || current().kind == TokenKind::Number) ? take() : name();
      base = make(NodeKind::Member, member.span, member.text, {base});
      if (t.text == "?.") base->names.push_back("optional");
    } else if (eat("!") || eat("?")) {
      base = make(NodeKind::Unary, t.span, t.text == "!" ? "post!" : "post?", {base});
    } else if (eat("[")) {
      if (eat(":"))
        base = make(NodeKind::Slice, t.span, "", {base});
      else {
        auto index = expression();
        base = make(index->kind == NodeKind::Binary && (index->text == ".." || index->text == "..=")
                        ? NodeKind::Slice
                        : NodeKind::Index,
                    t.span, "", {base, index});
      }
      require("]");
    } else if (!excludeFields && at("{") &&
               (base->kind == NodeKind::Name || base->kind == NodeKind::Member ||
                base->kind == NodeKind::Generic)) {
      take();
      auto n = make(NodeKind::Fields, t.span, "", {base});
      list("}", [&] {
        auto field = name();
        n->names.push_back(field.text);
        n->bindingSpans.push_back(field.span);
        require(":");
        n->children.push_back(expression());
      });
      base = n;
    } else if (at("<") && (base->kind == NodeKind::Name || base->kind == NodeKind::Member)) {
      auto oldPos = pos;
      auto oldSplit = split;
      std::vector<Ast> types;
      try {
        types = genericArguments();
      } catch (const Diagnostic &) {
        pos = oldPos;
        split = oldSplit;
        break;
      }
      if (types.empty() || !(at("(") || at(".") || (!excludeFields && at("{")))) {
        pos = oldPos;
        split = oldSplit;
        break;
      }
      types.insert(types.begin(), base);
      base = make(NodeKind::Generic, t.span, "", std::move(types));
    } else
      break;
  }
  return base;
}

Ast Parser::expression(int minimum, bool excludeFields) {
  Nest nesting(*this);
  auto left = primary(excludeFields);
  if (left->kind != NodeKind::Return && left->kind != NodeKind::Break && left->kind != NodeKind::Continue &&
      left->kind != NodeKind::For && left->kind != NodeKind::ForIn && left->kind != NodeKind::If)
    left = postfix(left, excludeFields);
  for (;;) {
    auto t = current();
    auto op = std::ranges::find(operators, t.text, &Operator::text);
    if (op == std::end(operators) || op->precedence < minimum || t.kind == TokenKind::Text) break;
    take();
    auto p = op->precedence;
    std::string binder;
    Span binderSpan;
    if (p == 1 && at("(") && current(1).kind == TokenKind::Name && current(2).text == ")" &&
        current(3).text == "{") {
      take();
      auto binding = name();
      binder = binding.text;
      binderSpan = binding.span;
      require(")");
    }
    auto right = expression(p <= 2 ? p : p + 1, excludeFields);
    if (p == 9) {
      if (left->kind != NodeKind::Compare) left = make(NodeKind::Compare, t.span, "", {left});
      left->children.push_back(right);
      left->names.push_back(t.text);
      left->bindingSpans.push_back(t.span);
    } else {
      if ((p == 8 || p == 10) && left->kind == NodeKind::Binary &&
          std::ranges::any_of(operators, [&](auto previous) {
            return previous.text == left->text && previous.precedence == p;
          }))
        fail(t.span, "operator cannot be chained", "E110");
      left = make(NodeKind::Binary, t.span, t.text, {left, right});
      if (!binder.empty()) {
        left->names.push_back(binder);
        left->bindingSpans.push_back(binderSpan);
      }
    }
  }
  return left;
}

void show(const Ast &node, unsigned indent) {
  if (!node) return;
  std::cout << std::string(indent, ' ') << static_cast<int>(node->kind);
  if (!node->text.empty()) std::cout << ' ' << node->text;
  for (const auto &n : node->names)
    std::cout << ' ' << n;
  std::cout << '\n';
  for (const auto &c : node->children)
    show(c, indent + 2);
  if (node->tail) {
    std::cout << std::string(indent + 2, ' ') << "tail\n";
    show(node->tail, indent + 4);
  }
}
} // namespace

File parse(const Source &source) {
  return Parser(lex(source)).file();
}

File parse(const Source &source, std::vector<Token> tokens) {
  if (tokens.empty()) return parse(source);
  validateUtf8(source);
  return Parser(std::move(tokens)).file();
}

void printAst(const File &file) {
  if (!file.package.empty()) std::cout << "package " << file.package << '\n';
  for (const auto &d : file.declarations) {
    std::cout << d.name << '\n';
    show(d.body, 2);
  }
}
} // namespace vqr
