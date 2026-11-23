(keyword) @keyword
(boolean) @boolean
(null_literal) @constant.builtin
(attribute) @attribute
(number) @number
[(string) (character)] @string
(escape) @string.escape
[(line_comment) (block_comment)] @comment
(operator) @operator
(delimiter) @punctuation.delimiter
(bracket) @punctuation.bracket
(identifier) @variable

(package_declaration "package" @keyword name: (identifier) @variable.special)
(import_declaration "import" @keyword ["(" ")"] @punctuation.bracket)
(import_declaration "," @punctuation.delimiter)
(import_path (identifier) @variable.special)
(import_path "/" @punctuation.delimiter)
(import_item "as" @keyword alias: (identifier) @variable.special)

(source_file (identifier) @function . (bracket "("))
(source_file [(delimiter ".") (operator "?.")] . (identifier) @property)
(source_file [(delimiter ".") (operator "?.")] . (identifier) @function.method . (bracket "("))
(source_file (keyword "const") . (identifier) @constant)
(source_file (keyword "func") . (identifier) @function)
(source_file (keyword ["class" "interface" "enum" "type" "error"]) . (identifier) @type)
((source_file (identifier) @type.builtin)
 (#match? @type.builtin "^([iu][1-9][0-9]*|isize|usize|f16|f32|f64|bool|char|str|string|void|never|ErrorId|Self|c_int|c_char)$"))

((source_file [(bracket "(") (delimiter ",")] . (identifier) @keyword . (identifier) . [(bracket ")") (delimiter ",")])
 (#eq? @keyword "ref"))

((source_file (keyword "if") . (identifier) @keyword)
 (#eq? @keyword "let"))
((source_file (delimiter ":") . (identifier) @keyword . (identifier))
 (#match? @keyword "^(ref|mut)$"))
((source_file (identifier) @keyword . (identifier))
 (#match? @keyword "^(ref|raw|from|while)$"))
((source_file (identifier) @_ref . (identifier) @keyword)
 (#eq? @_ref "ref")
 (#eq? @keyword "mut"))
