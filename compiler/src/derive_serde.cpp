#include "syntax.hpp"

#include <algorithm>
#include <set>

namespace vqr {
namespace {

std::string quoteField(std::string_view key) {
  std::string result(1, '"');
  constexpr std::string_view hex = "0123456789abcdef";
  for (unsigned char byte : key) {
    if (byte == '"' || byte == '\\') {
      result += '\\';
      result += static_cast<char>(byte);
    } else if (byte < 32 || byte == 127) {
      result += "\\u{";
      result += hex[byte >> 4];
      result += hex[byte & 15];
      result += '}';
    } else {
      result += static_cast<char>(byte);
    }
  }
  return result + '"';
}

struct Field {
  const Decl *declaration;
  std::string key;
  Ast valueType;
  bool optional;

  std::string name(std::size_t index) const { return "vaqrinCell" + std::to_string(index); }
};

void anchor(Ast &node, Span at, const std::vector<Field> &fields) {
  if (!node) return;
  if (node->kind == NodeKind::TypeName || node->kind == NodeKind::Name) {
    constexpr std::string_view placeholder = "VaqrinSerdeFieldType";
    if (node->text.starts_with(placeholder)) {
      auto suffix = node->text.substr(placeholder.size());
      node = fields.at(std::stoul(suffix)).valueType;
      return;
    }
  }
  node->span = at;
  for (auto &span : node->bindingSpans) span = at;
  for (auto &child : node->children) anchor(child, at, fields);
  for (auto &parameter : node->parameters) {
    parameter.span = at;
    anchor(parameter.type, at, fields);
  }
  anchor(node->type, at, fields);
  anchor(node->tail, at, fields);
}

void generate(Decl &record, const std::string &serde, Span origin) {
  std::vector<Field> fields;
  std::set<std::string> keys;
  for (const auto &member : record.members) {
    if (member.kind == DeclKind::Conditional)
      fail(origin, "serde derivation requires a fixed field list");
    if (member.kind != DeclKind::Field) {
      if (member.name == "init")
        fail(origin, "serde derivation requires record construction without a custom initializer");
      continue;
    }
    if (!member.type || member.embed)
      fail(member.span, "a derived serde field requires an explicit type and cannot be embedded");
    auto key = member.name;
    bool renamed = false;
    for (const auto &attribute : member.attributes) {
      if (attribute.name != "serde_name" || renamed || attribute.value.empty())
        fail(attribute.span, "expected one nonempty #serde_name on a derived serde field");
      renamed = true;
      key = attribute.value;
    }
    if (!keys.insert(key).second) fail(member.span, "duplicate serialized field name '" + key + "'");
    const bool optional = member.type->kind == NodeKind::TypePrefix && member.type->text == "?";
    fields.push_back({&member, quoteField(key), optional ? member.type->children.front() : member.type, optional});
  }

  std::string code =
      "class VaqrinSerdeGenerated {\n"
      "pub static func serdeFieldCount() usize { " + std::to_string(fields.size()) + " }\n"
      "pub static func serdeFieldName(index: usize) ?str { switch index {\n";
  for (std::size_t i = 0; i < fields.size(); ++i)
    code += std::to_string(i) + " => some(" + fields[i].key + "),\n";
  code += "_ => null, } }\n";
  code += "pub const func encodeSerde(vaqrinOutput: *" + serde + ".Serializer) !void {\n"
          "vaqrinOutput.beginMap(some(usize(" + std::to_string(fields.size()) + ")))!;\n";
  for (std::size_t i = 0; i < fields.size(); ++i) {
    const auto &field = fields[i];
    const auto type = "VaqrinSerdeFieldType" + std::to_string(i);
    code += "vaqrinOutput.writeKey(" + field.key + ")!;\n";
    code += serde + (field.optional ? ".encodeOptional<" : ".encodeValue<") + type +
            ">(raw self." + field.declaration->name + ", vaqrinOutput)!;\n";
  }
  code += "vaqrinOutput.endMap() }\n"
          "pub static func decodeSerde(vaqrinInput: *" + serde + ".Deserializer) !Self {\n"
          "vaqrinInput.beginMap()!;\n";
  for (std::size_t i = 0; i < fields.size(); ++i) {
    const auto &field = fields[i];
    code += "var " + field.name(i) + ": " + (field.optional ? "??" : "?") +
            "VaqrinSerdeFieldType" + std::to_string(i) + " = null;\n";
  }
  code += "for { var vaqrinKey = vaqrinInput.nextKey()! ?? { break; };\n";
  for (std::size_t i = 0; i < fields.size(); ++i) {
    const auto &field = fields[i];
    const auto cell = field.name(i);
    if (i != 0) code += "else ";
    code += "if vaqrinKey == " + field.key + " {\n"
            "if " + cell + " != null { return " + serde + ".SerdeError.DuplicateKey; }\n" +
            cell + " = some(" + serde + (field.optional ? ".decodeOptional<" : ".decodeValue<") +
            "VaqrinSerdeFieldType" + std::to_string(i) + ">(vaqrinInput)!); }\n";
  }
  code += (fields.empty() ? "return " : "else { return ") + serde + ".SerdeError.UnknownField; ";
  if (!fields.empty()) code += "} ";
  code += "} vaqrinInput.endMap()!;\n";
  for (std::size_t i = 0; i < fields.size(); ++i) {
    const auto &field = fields[i];
    code += "var vaqrinValue" + std::to_string(i) + " = move(" + field.name(i) + ") ?? { ";
    code += field.optional ? "null" : "return " + serde + ".SerdeError.MissingField;";
    code += " };\n";
  }
  code += "Self{";
  for (std::size_t i = 0; i < fields.size(); ++i)
    code += fields[i].declaration->name + ": move(vaqrinValue" + std::to_string(i) + "), ";
  code += "} } }\n";
  const Source generated{origin.source->path, std::move(code)};
  try {
    auto wrapper = parse(generated);
    auto &members = wrapper.declarations.front().members;
    for (auto &member : members) {
      member.span = origin;
      member.nameSpan = {};
      anchor(member.type, origin, fields);
      anchor(member.body, origin, fields);
      for (auto &parameter : member.params) {
        parameter.span = origin;
        anchor(parameter.type, origin, fields);
      }
    }
    record.members.insert(record.members.end(), std::make_move_iterator(members.begin()),
                          std::make_move_iterator(members.end()));
  } catch (const Diagnostic &error) {
    fail(origin, "could not generate serde methods: " + std::string(error.what()));
  }
}

} // namespace

void deriveSerde(File &file) {
  auto serde = std::ranges::find_if(file.imports, [](const auto &import) {
    return !import.filePath && import.path == "serde";
  });
  for (auto &decl : file.declarations) {
    auto attribute = std::ranges::find(decl.attributes, "derive", &Attribute::name);
    if (attribute == decl.attributes.end()) continue;
    if (decl.kind != DeclKind::Class || attribute->value != "serde")
      fail(attribute->span, "#derive: serde requires a record class");
    if (serde == file.imports.end()) fail(attribute->span, "#derive: serde requires an import of serde");
    generate(decl, serde->alias.empty() ? "serde" : serde->alias, attribute->span);
  }
}

} // namespace vqr
