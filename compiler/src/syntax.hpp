#pragma once

#include "source.hpp"

#include <memory>
#include <vector>

namespace vqr {

enum class NodeKind {
  Name,
  Number,
  Text,
  Character,
  Boolean,
  Null,
  Tuple,
  Array,
  Fields,
  Block,
  Unary,
  Binary,
  Compare,
  Call,
  Lambda,
  Member,
  Index,
  Slice,
  Generic,
  If,
  Switch,
  Select,
  Arm,
  Return,
  Break,
  Continue,
  Unsafe,
  Move,
  New,
  Binding,
  Assign,
  Discard,
  For,
  ForIn,
  PatternName,
  PatternRef,
  PatternMutRef,
  PatternIndirect,
  PatternCase,
  PatternTuple,
  PatternValue,
  PatternAny,
  TypeName,
  TypeArray,
  TypeTuple,
  TypePrefix,
  TypeFunction
};

struct Node;
using Ast = std::shared_ptr<Node>;
enum class ParameterMode { Value, In, Mut, Move };
inline ParameterMode parameterMode(const Ast &syntax);
struct Parameter {
  std::string name;
  Span span;
  Ast type;
  bool comptime = false;
};
struct Node {
  NodeKind kind;
  Span span;
  std::string text;
  std::vector<Ast> children;
  std::vector<std::string> names;
  std::vector<Parameter> parameters;
  Ast type;
  Ast tail;
  std::vector<Span> bindingSpans;
};

inline ParameterMode parameterMode(const Ast &syntax) {
  if (!syntax || syntax->kind != NodeKind::TypePrefix) return ParameterMode::Value;
  if (syntax->text == "in") return ParameterMode::In;
  if (syntax->text == "mut") return ParameterMode::Mut;
  if (syntax->text == "move") return ParameterMode::Move;
  return ParameterMode::Value;
}

struct Attribute {
  std::string name;
  Span span;
  std::string value;
  std::vector<Ast> types{};
};

enum class DeclKind { Function, Global, Alias, Error, Enum, Class, Interface, Field, Variant, Conditional };
struct Decl {
  DeclKind kind;
  Span span, nameSpan;
  std::string name;
  Ast type, body;
  std::vector<Parameter> params;
  std::vector<Parameter> generics;
  std::vector<Attribute> attributes;
  std::vector<Decl> members, otherwise;
  std::vector<std::pair<std::string, Ast>> initializers;
  bool visible = false;
  bool external = false;
  bool exported = false;
  bool readOnly = false;
  bool comptime = false;
  bool unsafe = false;
  bool associated = false;
  bool embed = false;
  std::string borrowSource;
};

struct Import {
  bool filePath = false;
  std::string path, alias;
  Span span;
};
struct File {
  std::string package, module;
  Span span;
  std::vector<Import> imports;
  std::vector<Decl> declarations;
  bool standard = false;
  bool anonymous = false;
  bool selected = false;
  bool explicitInput = false;
  std::string identity;
};

File parse(const Source &source);
File parse(const Source &source, std::vector<Token> tokens);
void deriveSerde(File &file);
void printAst(const File &file);
} // namespace vqr
