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
};

struct Attribute {
  std::string name;
  Span span;
  std::string value;
  std::vector<Ast> types{};
};

enum class DeclKind { Function, Global, Alias, Error, Enum, Class, Interface, Field, Variant, Conditional };
struct Decl {
  DeclKind kind;
  Span span;
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
void printAst(const File &file);
} // namespace vqr
