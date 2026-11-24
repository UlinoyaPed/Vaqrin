module.exports = grammar({
  name: "vaqrin",
  extras: $ => [/\s/, $.line_comment, $.block_comment],
  word: $ => $.identifier,

  rules: {
    source_file: $ => repeat(choice(
      $.package_declaration, $.import_declaration,
      $.identifier, $.keyword, $.boolean, $.null_literal,
      $.attribute, $.number, $.string, $.character, $.operator, $.delimiter, $.bracket
    )),

    package_declaration: $ => prec.right(seq("package", optional(field("name", $.identifier)))),
    import_declaration: $ => seq(
      "import", "(", $.import_item, repeat(seq(",", $.import_item)), optional(","), ")"
    ),
    import_item: $ => seq(
      field("path", $.import_path),
      optional(seq("as", field("alias", $.identifier)))
    ),
    import_path: $ => seq($.identifier, repeat(seq("/", $.identifier))),

    identifier: _ => /[_\p{XID_Start}][_\p{XID_Continue}]*/,
    keyword: _ => choice(
      "as", "pub", "var", "const", "comptime", "func", "class",
      "interface", "enum", "error", "type", "static", "operator",
      "embed", "self", "extern", "export", "unsafe", "return", "break", "continue",
      "if", "else", "switch", "select", "for", "in", "move", "new", "catch", "defer", "_"
    ),
    boolean: _ => choice("true", "false"),
    null_literal: _ => "null",
    attribute: _ => token(/#[_\p{XID_Start}][_\p{XID_Continue}]*/),
    number: _ => token(/(0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|[0-9][0-9_]*(\.[0-9][0-9_]*)?([eE][+-]?[0-9][0-9_]*)?)([iuf][0-9]+|[iu]size)?/),
    string: $ => seq('"', repeat(choice(/[^"\\\r\n]+/, $.escape)), '"'),
    character: $ => seq("'", choice(/[^'\\\r\n]/, $.escape), "'"),
    escape: _ => token(/\\(u\{[0-9a-fA-F]+\}|x[0-9a-fA-F]{2}|[^\r\n])/),
    line_comment: _ => token(seq("//", /[^\r\n]*/)),
    block_comment: _ => token(seq("/*", /[^*]*\*+([^/*][^*]*\*+)*/, "/")),
    operator: _ => choice(
      "<<%=", "<<|=", "<<=", ">>=", "+%=", "-%=", "*%=", "+|=", "-|=", "*|=",
      "<<%", "<<|", "..=", "=>", ":=", "?.", "??", "<<", ">>", "..", "<=", ">=",
      "==", "!=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "^=", "|=", "++",
      "--", "+%", "-%", "*%", "+|", "-|", "*|", "=", "+", "-", "*", "/", "%", "&",
      "^", "|", "~", "!", "?", "<", ">"
    ),
    delimiter: _ => choice(".", ":", ";", ","),
    bracket: _ => choice("(", ")", "[", "]", "{", "}"),
  },
});
