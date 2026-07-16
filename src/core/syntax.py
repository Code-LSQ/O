from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QFont, QColor
from PySide6.QtCore import QRegularExpression

from src.util import logger


class Highlighter(QSyntaxHighlighter):
    """基础语法高亮器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []
        self._compiled_patterns = []

    def addRule(self, pattern, format):
        if not pattern:
            return
        try:
            expr = QRegularExpression(pattern)
            if not expr.isValid():
                return
            self.highlighting_rules.append((pattern, format))
            self._compiled_patterns.append(expr)
        except Exception:
            logger.exception("高亮规则创建失败")

    def highlightBlock(self, text: str):
        if not text:
            return
        if not self.document():
            return
        for i, (pattern, fmt) in enumerate(self.highlighting_rules):
            try:
                expression = self._compiled_patterns[i]
                match_iterator = expression.globalMatch(text)
                while match_iterator.hasNext():
                    match = match_iterator.next()
                    start = match.capturedStart()
                    length = match.capturedLength()
                    if length > 0 and start >= 0:
                        self.setFormat(start, length, fmt)
            except Exception:
                continue

    def _fmt(self, color=None, bold=False, italic=False, bg=None):
        fmt = QTextCharFormat()
        if color is not None:
            fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if italic:
            fmt.setFontItalic(True)
        if bg is not None:
            fmt.setBackground(QColor(bg))
        return fmt

    def _kws(self, words, color, bold=True):
        fmt = self._fmt(color, bold=bold)
        for w in words:
            self.addRule(f"\\b{w}\\b", fmt)


def _setupPython(hl):
    hl._kws([
        "and", "as", "assert", "break", "class", "continue", "def",
        "del", "elif", "else", "except", "False", "finally", "for",
        "from", "global", "if", "import", "in", "is", "lambda",
        "None", "nonlocal", "not", "or", "pass", "raise", "return",
        "True", "try", "while", "with", "yield"
    ], "#0000FF")

    sf = hl._fmt("#008000")
    hl.addRule(r'\".*?\"', sf)
    hl.addRule(r'\'.*?\'', sf)
    hl.addRule(r'\"\"\".*?\"\"\"', sf)
    hl.addRule(r'\'\'\'.*?\'\'\'', sf)

    hl.addRule(r'#.*', hl._fmt("#808080"))
    hl.addRule(r'\b\d+\b', hl._fmt("#FF00FF"))
    hl.addRule(r'\bdef\s+(\w+)', hl._fmt("#000080"))


def _setupCpp(hl):
    hl._kws([
        "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand",
        "bitor", "bool", "break", "case", "catch", "char", "char8_t",
        "char16_t", "char32_t", "class", "compl", "concept", "const",
        "consteval", "constexpr", "constinit", "const_cast", "continue",
        "co_await", "co_return", "co_yield", "decltype", "default",
        "delete", "do", "double", "dynamic_cast", "else", "enum",
        "explicit", "export", "extern", "false", "float", "for", "friend",
        "goto", "if", "inline", "int", "long", "mutable", "namespace",
        "new", "noexcept", "not", "not_eq", "nullptr", "operator", "or",
        "or_eq", "private", "protected", "public", "register",
        "reinterpret_cast", "requires", "return", "short", "signed",
        "sizeof", "static", "static_assert", "static_cast", "struct",
        "switch", "template", "this", "thread_local", "throw", "true",
        "try", "typedef", "typeid", "typename", "union", "unsigned",
        "using", "virtual", "void", "volatile", "wchar_t", "while",
        "xor", "xor_eq"
    ], "#0000FF")

    sf = hl._fmt("#008000")
    hl.addRule(r'\".*?\"', sf)
    hl.addRule(r'\'.*?\'', sf)

    hl.addRule(r'//.*', hl._fmt("#808080"))
    hl.addRule(r'/\*.*?\*/', hl._fmt("#808080"))
    hl.addRule(r'\b\d+\b', hl._fmt("#FF00FF"))
    hl.addRule(r'^#.*', hl._fmt("#800080"))


def _setupCmd(hl):
    hl._kws([
        "echo", "set", "if", "else", "for", "in", "do", "goto", "call",
        "start", "exit", "pause", "rem", "dir", "cd", "md", "rd", "del",
        "copy", "move", "ren", "type", "cls", "chdir", "mkdir", "rmdir"
    ], "#0000FF")

    hl.addRule(r'^\s*:\w+', hl._fmt("#800000"))
    hl.addRule(r'%[^%]+%', hl._fmt("#008080"))
    hl.addRule(r'^\s*rem\s+.*', hl._fmt("#808080"))
    hl.addRule(r'^\s*::.*', hl._fmt("#808080"))
    hl.addRule(r'\".*?\"', hl._fmt("#008000"))


def _setupJson(hl):
    hl.addRule(r'"[^"]+"(?=\s*:)', hl._fmt("#0451A5"))
    hl.addRule(r':\s*"[^"]*"', hl._fmt("#A31515"))
    hl.addRule(r'\b-?\d+\.?\d*([eE][+-]?\d+)?\b', hl._fmt("#098658"))
    hl.addRule(r'\b(true|false|null)\b', hl._fmt("#0000FF"))
    hl.addRule(r'[\[\]{}]', hl._fmt("#000000"))


def _setupMarkdown(hl):
    hl.addRule(r'^#{1,6}\s+.*', hl._fmt("#1F4E79", bold=True))
    hl.addRule(r'\*\*[^*]+\*\*', hl._fmt(bold=True))
    hl.addRule(r'__[^_]+__', hl._fmt(bold=True))
    hl.addRule(r'\*[^*]+\*', hl._fmt(italic=True))
    hl.addRule(r'_[^_]+_', hl._fmt(italic=True))
    hl.addRule(r'`[^`]+`', hl._fmt("#A31515", bg="#F5F5F5"))
    hl.addRule(r'^```[\s\S]*?```', hl._fmt("#A31515", bg="#F5F5F5"))
    hl.addRule(r'^```.*', hl._fmt("#A31515", bg="#F5F5F5"))
    hl.addRule(r'\[.+?\]\(.+?\)', hl._fmt("#0066CC"))
    hl.addRule(r'!\[[^\]]*\]\(.+?\)', hl._fmt("#0066CC"))
    hl.addRule(r'<img\s+[^>]+>', hl._fmt("#0066CC"))
    hl.addRule(r'^[\s]*[-*+]\s+', hl._fmt("#6800D4"))
    hl.addRule(r'^[\s]*\d+\.\s+', hl._fmt("#6800D4"))
    hl.addRule(r'^>\s*.*', hl._fmt("#6A9955"))
    hl.addRule(r'^[-*_]{3,}$', hl._fmt("#C0C0C0"))


def _setupShell(hl):
    hl._kws([
        "if", "then", "else", "elif", "fi", "case", "esac", "for",
        "while", "until", "do", "done", "in", "function", "select",
        "time", "coproc", "export", "local", "readonly", "declare",
        "typeset", "unset", "shift", "return", "exit", "break",
        "continue", "source", "alias", "unalias", "eval", "exec",
        "and", "or", "not", "begin", "end", "switch", "return"
    ], "#0000FF")

    bf = hl._fmt("#0451A5")
    for b in [
        "echo", "printf", "read", "cd", "pwd", "pushd", "popd",
        "ls", "cp", "mv", "rm", "mkdir", "rmdir", "touch", "cat",
        "grep", "sed", "awk", "find", "xargs", "sort", "uniq",
        "wc", "head", "tail", "cut", "tr", "tee", "test", "true", "false",
        "jobs", "fg", "bg", "wait", "kill", "nohup", "xargs", "which"
    ]:
        hl.addRule(f"\\b{b}\\b", bf)
    for b in [
        "set", "contains", "count", "argparse", "complete", "functions",
        "status", "bind", "string", "math", "random", "fish_config"
    ]:
        hl.addRule(f"\\b{b}\\b", bf)

    sf = hl._fmt("#A31515")
    hl.addRule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
    hl.addRule(r"'[^']*'", sf)

    vf = hl._fmt("#001080")
    hl.addRule(r'\$\{?[a-zA-Z_][a-zA-Z0-9_]*\}?', vf)
    hl.addRule(r'\$[0-9@#?$!*]', vf)
    hl.addRule(r'\$\{[^}]+\}', vf)

    hl.addRule(r'#.*', hl._fmt("#808080"))
    hl.addRule(r'\b\d+\b', hl._fmt("#098658"))
    hl.addRule(r'[|&;<>]', hl._fmt("#000000"))


def _setupGo(hl):
    hl._kws([
        "break", "case", "chan", "const", "continue", "default",
        "defer", "else", "fallthrough", "for", "func", "go", "goto",
        "if", "import", "interface", "map", "package", "range",
        "return", "select", "struct", "switch", "type", "var",
        "bool", "byte", "complex64", "complex128", "error", "float32",
        "float64", "int", "int8", "int16", "int32", "int64", "rune",
        "string", "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
        "true", "false", "iota", "nil", "append", "cap", "close",
        "complex", "copy", "delete", "imag", "len", "make", "new",
        "panic", "print", "println", "real", "recover"
    ], "#0000FF")

    sf = hl._fmt("#008000")
    hl.addRule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
    hl.addRule(r'`[^`]*`', sf)

    hl.addRule(r'//.*', hl._fmt("#808080"))
    hl.addRule(r'/\*[\s\S]*?\*/', hl._fmt("#808080"))

    nf = hl._fmt("#098658")
    hl.addRule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
    hl.addRule(r'\b0x[0-9a-fA-F]+\b', nf)

    hl.addRule(r'\bfunc\s+(\w+)', hl._fmt("#795E26"))


def _setupJava(hl):
    hl._kws([
        "abstract", "assert", "boolean", "break", "byte", "case", "catch",
        "char", "class", "const", "continue", "default", "do", "double",
        "else", "enum", "extends", "final", "finally", "float", "for",
        "goto", "if", "implements", "import", "instanceof", "int",
        "interface", "long", "native", "new", "package", "private",
        "protected", "public", "return", "short", "static", "strictfp",
        "super", "switch", "synchronized", "this", "throw", "throws",
        "transient", "try", "void", "volatile", "while", "true", "false",
        "null", "var", "record", "sealed", "permits", "yield", "instance"
    ], "#0000FF")

    hl.addRule(r'"[^"\\]*(\\.[^"\\]*)*"', hl._fmt("#008000"))
    hl.addRule(r'//.*', hl._fmt("#808080"))
    hl.addRule(r'/\*[\s\S]*?\*/', hl._fmt("#808080"))

    nf = hl._fmt("#098658")
    hl.addRule(r'\b\d+\.?\d*([eE][+-]?\d+)?[fFdDlL]?\b', nf)
    hl.addRule(r'\b0x[0-9a-fA-F]+[lL]?\b', nf)

    hl.addRule(r'@[a-zA-Z_]\w*', hl._fmt("#808000"))

    cf = hl._fmt("#2B91AF")
    hl.addRule(r'\bclass\s+(\w+)', cf)
    hl.addRule(r'\b(public|private|protected)\s+(static\s+)?class\s+(\w+)', cf)


def _setupJavaScript(hl):
    hl._kws([
        "async", "await", "break", "case", "catch", "class", "const",
        "continue", "debugger", "default", "delete", "do", "else",
        "export", "extends", "false", "finally", "for", "function",
        "if", "import", "in", "instanceof", "let", "new", "null",
        "return", "static", "super", "switch", "this", "throw",
        "true", "try", "typeof", "undefined", "var", "void", "while",
        "with", "yield", "of", "as", "from", "get", "set"
    ], "#0000FF")

    sf = hl._fmt("#A31515")
    hl.addRule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
    hl.addRule(r"'[^'\\]*(\\.[^'\\]*)*'", sf)
    hl.addRule(r'`[^`]*`', sf)

    cf = hl._fmt("#008000")
    hl.addRule(r'//.*', cf)
    hl.addRule(r'/\*[\s\S]*?\*/', cf)

    nf = hl._fmt("#098658")
    hl.addRule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
    hl.addRule(r'\b0x[0-9a-fA-F]+\b', nf)

    ff = hl._fmt("#795E26")
    hl.addRule(r'\bfunction\s+(\w+)', ff)
    hl.addRule(r'\b(\w+)\s*\(', ff)

    hl.addRule(r'/\w+/[gimsuy]*', hl._fmt("#FF0000"))
    hl.addRule(r'=>', hl._fmt("#0000FF"))


def _setupHTML(hl):
    tf = hl._fmt("#800000")
    hl.addRule(r'</?[a-zA-Z][a-zA-Z0-9]*', tf)
    hl.addRule(r'/?>', tf)

    hl.addRule(r'\b[a-zA-Z-]+(?==)', hl._fmt("#FF0000"))

    vf = hl._fmt("#0000FF")
    hl.addRule(r'"[^"]*"', vf)
    hl.addRule(r"'[^']*'", vf)

    hl.addRule(r'<!--[\s\S]*?-->', hl._fmt("#808080"))
    hl.addRule(r'<!DOCTYPE[^>]*>', hl._fmt("#0000FF"))


def _setupTypeScript(hl):
    hl._kws([
        "async", "await", "break", "case", "catch", "class", "const",
        "continue", "debugger", "default", "delete", "do", "else",
        "enum", "export", "extends", "false", "finally", "for",
        "function", "if", "implements", "import", "in", "instanceof",
        "interface", "let", "new", "null", "package", "private",
        "protected", "public", "return", "static", "super", "switch",
        "this", "throw", "true", "try", "typeof", "undefined", "var",
        "void", "while", "with", "yield", "of", "as", "from", "get",
        "set", "type", "abstract", "any", "boolean", "declare",
        "namespace", "require", "readonly", "keyof", "infer",
        "never", "unknown", "object", "number", "string", "symbol",
        "bigint", "asserts", "satisfies"
    ], "#0000FF")

    sf = hl._fmt("#A31515")
    hl.addRule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
    hl.addRule(r"'[^'\\]*(\\.[^'\\]*)*'", sf)
    hl.addRule(r'`[^`]*`', sf)

    cf = hl._fmt("#008000")
    hl.addRule(r'//.*', cf)
    hl.addRule(r'/\*[\s\S]*?\*/', cf)

    nf = hl._fmt("#098658")
    hl.addRule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
    hl.addRule(r'\b0x[0-9a-fA-F]+\b', nf)
    hl.addRule(r'\b0b[01]+\b', nf)
    hl.addRule(r'\b0o[0-7]+\b', nf)

    ff = hl._fmt("#795E26")
    hl.addRule(r'\bfunction\s+(\w+)', ff)
    hl.addRule(r'\b(\w+)\s*\(', ff)

    hl.addRule(r':\s*(string|number|boolean|any|void|never|unknown|object|Array|Promise|Record|Partial|Required|Readonly|Pick|Omit|Record)', hl._fmt("#267F99"))
    hl.addRule(r'@[a-zA-Z_]\w*', hl._fmt("#808000"))
    hl.addRule(r'/\w+/[gimsuy]*', hl._fmt("#FF0000"))
    hl.addRule(r'=>', hl._fmt("#0000FF"))


def _setupRust(hl):
    hl._kws([
        "as", "async", "await", "break", "const", "continue", "crate",
        "dyn", "else", "enum", "extern", "false", "fn", "for", "if",
        "impl", "in", "let", "loop", "match", "mod", "move", "mut",
        "pub", "ref", "return", "self", "Self", "static", "struct",
        "super", "trait", "true", "type", "unsafe", "use", "where",
        "while", "macro_rules!", "some", "none", "Ok", "Err"
    ], "#0000FF")

    hl._kws([
        "i8", "i16", "i32", "i64", "i128", "isize",
        "u8", "u16", "u32", "u64", "u128", "usize",
        "f32", "f64", "bool", "char", "str", "String",
        "Vec", "Option", "Result", "Box", "Rc", "Arc",
        "Cell", "RefCell", "Mutex", "HashMap", "HashSet"
    ], "#267F99", bold=False)

    sf = hl._fmt("#A31515")
    hl.addRule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
    hl.addRule(r'r#*"[^"]*"#*', sf)

    hl.addRule(r'//.*', hl._fmt("#808080"))
    hl.addRule(r'/\*[\s\S]*?\*/', hl._fmt("#808080"))

    nf = hl._fmt("#098658")
    hl.addRule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
    hl.addRule(r'\b0x[0-9a-fA-F_]+\b', nf)
    hl.addRule(r'\b0b[01_]+\b', nf)
    hl.addRule(r'\b0o[0-7_]+\b', nf)

    ff = hl._fmt("#795E26")
    hl.addRule(r'\bfn\s+(\w+)', ff)
    hl.addRule(r'\b(\w+)\s*\(', ff)

    hl.addRule(r'\b[a-z_][a-zA-Z0-9_]*!', hl._fmt("#DCDCAA"))
    hl.addRule(r'#\[.*?\]', hl._fmt("#808000"))


def _setupPowerShell(hl):
    hl._kws([
        "begin", "break", "catch", "class", "continue", "data",
        "define", "do", "dynamicparam", "else", "elseif", "end",
        "exit", "filter", "finally", "for", "foreach", "from",
        "function", "hidden", "if", "in", "param", "process",
        "return", "static", "switch", "throw", "trap", "try",
        "until", "using", "var", "while", "workflow"
    ], "#0000FF")

    cef = hl._fmt("#0451A5")
    for cmdlet in [
        "Get-", "Set-", "New-", "Remove-", "Add-", "Clear-",
        "Write-", "Read-", "Out-", "Start-", "Stop-", "Import-",
        "Export-", "Invoke-", "Select-", "Where-", "ForEach-",
        "Sort-", "Group-", "Measure-", "Compare-", "Test-",
        "ConvertTo-", "ConvertFrom-", "Format-", "Show-",
        "Register-", "Unregister-", "Enable-", "Disable-",
        "Test-", "Debug-", "Trace-"
    ]:
        hl.addRule(f"{cmdlet}[a-zA-Z]+", cef)
    for cmdlet in [
        "Write-Host", "Write-Output", "Write-Error", "Write-Warning",
        "Write-Verbose", "Write-Debug", "Write-Information",
        "Get-Content", "Set-Content", "Add-Content", "Get-Item",
        "Set-Item", "Copy-Item", "Move-Item", "Remove-Item",
        "Get-ChildItem", "Get-Location", "Set-Location", "Push-Location",
        "Pop-Location", "Get-Process", "Stop-Process", "Start-Process",
        "Get-Service", "Stop-Service", "Start-Service", "Restart-Service"
    ]:
        hl.addRule(f"\\b{cmdlet}\\b", cef)

    vf = hl._fmt("#001080")
    hl.addRule(r'\$[a-zA-Z_][a-zA-Z0-9_]*', vf)
    hl.addRule(r'\$\{[^}]+\}', vf)
    hl.addRule(r'\$[0-9]+', vf)

    sf = hl._fmt("#A31515")
    hl.addRule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
    hl.addRule(r"'[^']*'", sf)
    hl.addRule(r'@".*?"@', sf)

    cf = hl._fmt("#008000")
    hl.addRule(r'#.*', cf)
    hl.addRule(r'<#[\s\S]*?#>', cf)

    nf = hl._fmt("#098658")
    hl.addRule(r'\b\d+\b', nf)
    hl.addRule(r'\b0x[0-9a-fA-F]+\b', nf)

    of = hl._fmt("#000000")
    hl.addRule(r'[-+*/%=]', of)
    hl.addRule(r'-eq|-ne|-gt|-lt|-le|-ge|-like|-notlike|-match|-notmatch|-contains|-notcontains|-in|-notin|-replace', of)


_SETUP_MAP = [
    ('.py', _setupPython),
    (('.cpp', '.cc', '.cxx', '.h', '.hpp', '.c', '.hxx'), _setupCpp),
    (('.bat', '.cmd'), _setupCmd),
    ('.json', _setupJson),
    ('.md', _setupMarkdown),
    (('.sh', '.bash', '.zsh', '.fish', '.ksh', '.csh', '.tcsh'), _setupShell),
    ('.go', _setupGo),
    ('.java', _setupJava),
    (('.js', '.jsx', '.mjs', '.cjs'), _setupJavaScript),
    (('.ts', '.tsx'), _setupTypeScript),
    ('.rs', _setupRust),
    (('.ps1', '.psm1', '.psd1', '.pssc', '.psrc'), _setupPowerShell),
    (('.html', '.htm', '.xml', '.xhtml', '.svg'), _setupHTML),
]


def createHighlighter(file_path: str, parent=None):
    """根据文件扩展名创建高亮器"""
    if not file_path:
        return None

    try:
        file_path_lower = file_path.lower()
    except (AttributeError, TypeError):
        return None

    try:
        hl = Highlighter(parent)
        for exts, setup_fn in _SETUP_MAP:
            if file_path_lower.endswith(exts):
                setup_fn(hl)
                return hl
        return None
    except Exception:
        logger.exception("创建语法高亮器失败")
        return None
