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

; These words remain identifiers outside the syntax that introduces them.
((source_file (keyword "if") . (identifier) @keyword)
 (#eq? @keyword "let"))

; A writable parameter mode occurs between its colon and the data type.
((source_file (delimiter ":") . (identifier) @keyword .
  [(identifier) (operator ["*" "^" "?" "!"]) (bracket ["[" "("]) (keyword "func")])
 (#eq? @keyword "mut"))

; Reference types, borrow expressions and patterns, and local aliases.
; At a token boundary, an operand must follow; e.g. ref(), x.ref and
; a binding named ref do not match. Operators/keywords cover expression starts.
((source_file
  [(bracket ["(" ")" "[" "]" "{"]) (delimiter [":" "," ";"])
   (operator ["=" ":=" "=>" "??" "!" "?" "^" "*"]) (keyword ["return" "in" "move"])] .
  [(line_comment) (block_comment)]* . (identifier) @keyword . [(identifier) (keyword "self") (operator "*")])
 (#eq? @keyword "ref"))
((source_file
  [(bracket ["(" ")" "[" "]" "{"]) (delimiter [":" "," ";"])
   (operator ["=" ":=" "=>" "??" "!" "?" "^" "*"]) (keyword ["return" "in" "move"])] .
  [(line_comment) (block_comment)]* . (identifier) @keyword . (identifier) @keyword .
  [(identifier) (keyword "self") (operator "*")])
 (#any-eq? @keyword "ref")
 (#any-eq? @keyword "mut"))

; Raw address expressions accept a place name, self, or an explicit dereference.
((source_file
  [(bracket ["(" "[" "{"]) (delimiter ["," ";"])
   (operator ["=" ":=" "=>" "??"]) (keyword "return")] .
  [(line_comment) (block_comment)]* . (identifier) @keyword . [(identifier) (keyword "self") (operator "*")])
 (#eq? @keyword "raw"))

; The borrow source clause follows a return type and names a parameter or self.
((source_file
  (identifier) @keyword . [(identifier) (keyword "self")] .
  [(bracket ["{" ")"]) (delimiter [":" ";" ","])])
 (#eq? @keyword "from"))

; Loop headers follow a block/statement boundary or an explicit label.
((source_file
  [(bracket ["{" "}"]) (delimiter [";" ":"])] .
  [(line_comment) (block_comment)]* . (identifier) @keyword .
  [(identifier) (boolean) (null_literal) (number) (character) (string)
   (keyword ["self" "new"]) (operator ["!" "-" "*" "~"]) (bracket "(")])
 (#eq? @keyword "while"))
