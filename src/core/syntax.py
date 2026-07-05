from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QFont, QColor
from PySide6.QtCore import QRegularExpression

from src.util import logger


class BaseHighlighter(QSyntaxHighlighter):
    """基础语法高亮器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []
        self._compiled_patterns = []
        self.setup_rules()

    def add_rule(self, pattern, format):
        if not pattern:
            return
        self.highlighting_rules.append((pattern, format))
        try:
            expr = QRegularExpression(pattern)
            self._compiled_patterns.append(expr if expr.isValid() else None)
        except Exception:
            self._compiled_patterns.append(None)

    def highlightBlock(self, text: str):
        if not text:
            return
        if not self.document():
            return
        for i, (pattern, fmt) in enumerate(self.highlighting_rules):
            try:
                if i < len(self._compiled_patterns) and self._compiled_patterns[i] is not None:
                    expression = self._compiled_patterns[i]
                else:
                    if not pattern:
                        continue
                    expression = QRegularExpression(pattern)
                    if not expression.isValid():
                        continue
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
            self.add_rule(f"\\b{w}\\b", fmt)


class PythonHighlighter(BaseHighlighter):
    """Python语法高亮器"""

    def setup_rules(self):
        self._kws([
            "and", "as", "assert", "break", "class", "continue", "def",
            "del", "elif", "else", "except", "False", "finally", "for",
            "from", "global", "if", "import", "in", "is", "lambda",
            "None", "nonlocal", "not", "or", "pass", "raise", "return",
            "True", "try", "while", "with", "yield"
        ], "#0000FF")

        sf = self._fmt("#008000")
        self.add_rule(r'\".*?\"', sf)
        self.add_rule(r'\'.*?\'', sf)
        self.add_rule(r'\"\"\".*?\"\"\"', sf)
        self.add_rule(r'\'\'\'.*?\'\'\'', sf)

        self.add_rule(r'#.*', self._fmt("#808080"))
        self.add_rule(r'\b\d+\b', self._fmt("#FF00FF"))
        self.add_rule(r'\bdef\s+(\w+)', self._fmt("#000080"))


class CppHighlighter(BaseHighlighter):
    """C++语法高亮器"""

    def setup_rules(self):
        self._kws([
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

        sf = self._fmt("#008000")
        self.add_rule(r'\".*?\"', sf)
        self.add_rule(r'\'.*?\'', sf)

        self.add_rule(r'//.*', self._fmt("#808080"))
        self.add_rule(r'/\*.*?\*/', self._fmt("#808080"))
        self.add_rule(r'\b\d+\b', self._fmt("#FF00FF"))
        self.add_rule(r'^#.*', self._fmt("#800080"))


class CmdHighlighter(BaseHighlighter):
    """Windows批处理语法高亮器"""

    def setup_rules(self):
        self._kws([
            "echo", "set", "if", "else", "for", "in", "do", "goto", "call",
            "start", "exit", "pause", "rem", "dir", "cd", "md", "rd", "del",
            "copy", "move", "ren", "type", "cls", "chdir", "mkdir", "rmdir"
        ], "#0000FF")

        self.add_rule(r'^\s*:\w+', self._fmt("#800000"))
        self.add_rule(r'%[^%]+%', self._fmt("#008080"))
        self.add_rule(r'^\s*rem\s+.*', self._fmt("#808080"))
        self.add_rule(r'^\s*::.*', self._fmt("#808080"))
        self.add_rule(r'\".*?\"', self._fmt("#008000"))


class JsonHighlighter(BaseHighlighter):
    """JSON语法高亮器"""

    def setup_rules(self):
        self.add_rule(r'"[^"]+"(?=\s*:)', self._fmt("#0451A5"))
        self.add_rule(r':\s*"[^"]*"', self._fmt("#A31515"))
        self.add_rule(r'\b-?\d+\.?\d*([eE][+-]?\d+)?\b', self._fmt("#098658"))
        self.add_rule(r'\b(true|false|null)\b', self._fmt("#0000FF"))
        self.add_rule(r'[\[\]{}]', self._fmt("#000000"))


class MarkdownHighlighter(BaseHighlighter):
    """Markdown语法高亮器"""

    def setup_rules(self):
        self.add_rule(r'^#{1,6}\s+.*', self._fmt("#1F4E79", bold=True))
        self.add_rule(r'\*\*[^*]+\*\*', self._fmt(bold=True))
        self.add_rule(r'__[^_]+__', self._fmt(bold=True))
        self.add_rule(r'\*[^*]+\*', self._fmt(italic=True))
        self.add_rule(r'_[^_]+_', self._fmt(italic=True))
        self.add_rule(r'`[^`]+`', self._fmt("#A31515", bg="#F5F5F5"))
        self.add_rule(r'^```[\s\S]*?```', self._fmt("#A31515", bg="#F5F5F5"))
        self.add_rule(r'^```.*', self._fmt("#A31515", bg="#F5F5F5"))
        self.add_rule(r'\[.+?\]\(.+?\)', self._fmt("#0066CC"))
        self.add_rule(r'!\[[^\]]*\]\(.+?\)', self._fmt("#0066CC"))
        self.add_rule(r'<img\s+[^>]+>', self._fmt("#0066CC"))
        self.add_rule(r'^[\s]*[-*+]\s+', self._fmt("#6800D4"))
        self.add_rule(r'^[\s]*\d+\.\s+', self._fmt("#6800D4"))
        self.add_rule(r'^>\s*.*', self._fmt("#6A9955"))
        self.add_rule(r'^[-*_]{3,}$', self._fmt("#C0C0C0"))


class ShellHighlighter(BaseHighlighter):
    """Shell(Bash/Zsh/Fish等)语法高亮器"""

    def setup_rules(self):
        self._kws([
            "if", "then", "else", "elif", "fi", "case", "esac", "for",
            "while", "until", "do", "done", "in", "function", "select",
            "time", "coproc", "export", "local", "readonly", "declare",
            "typeset", "unset", "shift", "return", "exit", "break",
            "continue", "source", "alias", "unalias", "eval", "exec",
            "and", "or", "not", "begin", "end", "switch", "return"
        ], "#0000FF")

        bf = self._fmt("#0451A5")
        for b in [
            "echo", "printf", "read", "cd", "pwd", "pushd", "popd",
            "ls", "cp", "mv", "rm", "mkdir", "rmdir", "touch", "cat",
            "grep", "sed", "awk", "find", "xargs", "sort", "uniq",
            "wc", "head", "tail", "cut", "tr", "tee", "test", "true", "false",
            "jobs", "fg", "bg", "wait", "kill", "nohup", "xargs", "which"
        ]:
            self.add_rule(f"\\b{b}\\b", bf)
        for b in [
            "set", "contains", "count", "argparse", "complete", "functions",
            "status", "bind", "string", "math", "random", "fish_config"
        ]:
            self.add_rule(f"\\b{b}\\b", bf)

        sf = self._fmt("#A31515")
        self.add_rule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
        self.add_rule(r"'[^']*'", sf)

        vf = self._fmt("#001080")
        self.add_rule(r'\$\{?[a-zA-Z_][a-zA-Z0-9_]*\}?', vf)
        self.add_rule(r'\$[0-9@#?$!*]', vf)
        self.add_rule(r'\$\{[^}]+\}', vf)

        self.add_rule(r'#.*', self._fmt("#808080"))
        self.add_rule(r'\b\d+\b', self._fmt("#098658"))
        self.add_rule(r'[|&;<>]', self._fmt("#000000"))


class GoHighlighter(BaseHighlighter):
    """Go语法高亮器"""

    def setup_rules(self):
        self._kws([
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

        sf = self._fmt("#008000")
        self.add_rule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
        self.add_rule(r'`[^`]*`', sf)

        self.add_rule(r'//.*', self._fmt("#808080"))
        self.add_rule(r'/\*[\s\S]*?\*/', self._fmt("#808080"))

        nf = self._fmt("#098658")
        self.add_rule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
        self.add_rule(r'\b0x[0-9a-fA-F]+\b', nf)

        self.add_rule(r'\bfunc\s+(\w+)', self._fmt("#795E26"))


class JavaHighlighter(BaseHighlighter):
    """Java语法高亮器"""

    def setup_rules(self):
        self._kws([
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

        self.add_rule(r'"[^"\\]*(\\.[^"\\]*)*"', self._fmt("#008000"))
        self.add_rule(r'//.*', self._fmt("#808080"))
        self.add_rule(r'/\*[\s\S]*?\*/', self._fmt("#808080"))

        nf = self._fmt("#098658")
        self.add_rule(r'\b\d+\.?\d*([eE][+-]?\d+)?[fFdDlL]?\b', nf)
        self.add_rule(r'\b0x[0-9a-fA-F]+[lL]?\b', nf)

        self.add_rule(r'@[a-zA-Z_]\w*', self._fmt("#808000"))

        cf = self._fmt("#2B91AF")
        self.add_rule(r'\bclass\s+(\w+)', cf)
        self.add_rule(r'\b(public|private|protected)\s+(static\s+)?class\s+(\w+)', cf)


class JavaScriptHighlighter(BaseHighlighter):
    """JavaScript语法高亮器"""

    def setup_rules(self):
        self._kws([
            "async", "await", "break", "case", "catch", "class", "const",
            "continue", "debugger", "default", "delete", "do", "else",
            "export", "extends", "false", "finally", "for", "function",
            "if", "import", "in", "instanceof", "let", "new", "null",
            "return", "static", "super", "switch", "this", "throw",
            "true", "try", "typeof", "undefined", "var", "void", "while",
            "with", "yield", "of", "as", "from", "get", "set"
        ], "#0000FF")

        sf = self._fmt("#A31515")
        self.add_rule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
        self.add_rule(r"'[^'\\]*(\\.[^'\\]*)*'", sf)
        self.add_rule(r'`[^`]*`', sf)

        cf = self._fmt("#008000")
        self.add_rule(r'//.*', cf)
        self.add_rule(r'/\*[\s\S]*?\*/', cf)

        nf = self._fmt("#098658")
        self.add_rule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
        self.add_rule(r'\b0x[0-9a-fA-F]+\b', nf)

        ff = self._fmt("#795E26")
        self.add_rule(r'\bfunction\s+(\w+)', ff)
        self.add_rule(r'\b(\w+)\s*\(', ff)

        self.add_rule(r'/\w+/[gimsuy]*', self._fmt("#FF0000"))
        self.add_rule(r'=>', self._fmt("#0000FF"))


class HTMLHighlighter(BaseHighlighter):
    """HTML/XML语法高亮器"""

    def setup_rules(self):
        tf = self._fmt("#800000")
        self.add_rule(r'</?[a-zA-Z][a-zA-Z0-9]*', tf)
        self.add_rule(r'/?>', tf)

        self.add_rule(r'\b[a-zA-Z-]+(?==)', self._fmt("#FF0000"))

        vf = self._fmt("#0000FF")
        self.add_rule(r'"[^"]*"', vf)
        self.add_rule(r"'[^']*'", vf)

        self.add_rule(r'<!--[\s\S]*?-->', self._fmt("#808080"))
        self.add_rule(r'<!DOCTYPE[^>]*>', self._fmt("#0000FF"))


class TypeScriptHighlighter(BaseHighlighter):
    """TypeScript语法高亮器"""

    def setup_rules(self):
        self._kws([
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

        sf = self._fmt("#A31515")
        self.add_rule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
        self.add_rule(r"'[^'\\]*(\\.[^'\\]*)*'", sf)
        self.add_rule(r'`[^`]*`', sf)

        cf = self._fmt("#008000")
        self.add_rule(r'//.*', cf)
        self.add_rule(r'/\*[\s\S]*?\*/', cf)

        nf = self._fmt("#098658")
        self.add_rule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
        self.add_rule(r'\b0x[0-9a-fA-F]+\b', nf)
        self.add_rule(r'\b0b[01]+\b', nf)
        self.add_rule(r'\b0o[0-7]+\b', nf)

        ff = self._fmt("#795E26")
        self.add_rule(r'\bfunction\s+(\w+)', ff)
        self.add_rule(r'\b(\w+)\s*\(', ff)

        self.add_rule(r':\s*(string|number|boolean|any|void|never|unknown|object|Array|Promise|Record|Partial|Required|Readonly|Pick|Omit|Record)', self._fmt("#267F99"))
        self.add_rule(r'@[a-zA-Z_]\w*', self._fmt("#808000"))
        self.add_rule(r'/\w+/[gimsuy]*', self._fmt("#FF0000"))
        self.add_rule(r'=>', self._fmt("#0000FF"))


class RustHighlighter(BaseHighlighter):
    """Rust语法高亮器"""

    def setup_rules(self):
        self._kws([
            "as", "async", "await", "break", "const", "continue", "crate",
            "dyn", "else", "enum", "extern", "false", "fn", "for", "if",
            "impl", "in", "let", "loop", "match", "mod", "move", "mut",
            "pub", "ref", "return", "self", "Self", "static", "struct",
            "super", "trait", "true", "type", "unsafe", "use", "where",
            "while", "macro_rules!", "some", "none", "Ok", "Err"
        ], "#0000FF")

        self._kws([
            "i8", "i16", "i32", "i64", "i128", "isize",
            "u8", "u16", "u32", "u64", "u128", "usize",
            "f32", "f64", "bool", "char", "str", "String",
            "Vec", "Option", "Result", "Box", "Rc", "Arc",
            "Cell", "RefCell", "Mutex", "HashMap", "HashSet"
        ], "#267F99", bold=False)

        sf = self._fmt("#A31515")
        self.add_rule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
        self.add_rule(r'r#*"[^"]*"#*', sf)

        self.add_rule(r'//.*', self._fmt("#808080"))
        self.add_rule(r'/\*[\s\S]*?\*/', self._fmt("#808080"))

        nf = self._fmt("#098658")
        self.add_rule(r'\b\d+\.?\d*([eE][+-]?\d+)?\b', nf)
        self.add_rule(r'\b0x[0-9a-fA-F_]+\b', nf)
        self.add_rule(r'\b0b[01_]+\b', nf)
        self.add_rule(r'\b0o[0-7_]+\b', nf)

        ff = self._fmt("#795E26")
        self.add_rule(r'\bfn\s+(\w+)', ff)
        self.add_rule(r'\b(\w+)\s*\(', ff)

        self.add_rule(r'\b[a-z_][a-zA-Z0-9_]*!', self._fmt("#DCDCAA"))
        self.add_rule(r'#\[.*?\]', self._fmt("#808000"))


class PowerShellHighlighter(BaseHighlighter):
    """PowerShell语法高亮器"""

    def setup_rules(self):
        self._kws([
            "begin", "break", "catch", "class", "continue", "data",
            "define", "do", "dynamicparam", "else", "elseif", "end",
            "exit", "filter", "finally", "for", "foreach", "from",
            "function", "hidden", "if", "in", "param", "process",
            "return", "static", "switch", "throw", "trap", "try",
            "until", "using", "var", "while", "workflow"
        ], "#0000FF")

        cef = self._fmt("#0451A5")
        for cmdlet in [
            "Get-", "Set-", "New-", "Remove-", "Add-", "Clear-",
            "Write-", "Read-", "Out-", "Start-", "Stop-", "Import-",
            "Export-", "Invoke-", "Select-", "Where-", "ForEach-",
            "Sort-", "Group-", "Measure-", "Compare-", "Test-",
            "ConvertTo-", "ConvertFrom-", "Format-", "Show-",
            "Register-", "Unregister-", "Enable-", "Disable-",
            "Test-", "Debug-", "Trace-"
        ]:
            self.add_rule(f"{cmdlet}[a-zA-Z]+", cef)
        for cmdlet in [
            "Write-Host", "Write-Output", "Write-Error", "Write-Warning",
            "Write-Verbose", "Write-Debug", "Write-Information",
            "Get-Content", "Set-Content", "Add-Content", "Get-Item",
            "Set-Item", "Copy-Item", "Move-Item", "Remove-Item",
            "Get-ChildItem", "Get-Location", "Set-Location", "Push-Location",
            "Pop-Location", "Get-Process", "Stop-Process", "Start-Process",
            "Get-Service", "Stop-Service", "Start-Service", "Restart-Service"
        ]:
            self.add_rule(f"\\b{cmdlet}\\b", cef)

        vf = self._fmt("#001080")
        self.add_rule(r'\$[a-zA-Z_][a-zA-Z0-9_]*', vf)
        self.add_rule(r'\$\{[^}]+\}', vf)
        self.add_rule(r'\$[0-9]+', vf)

        sf = self._fmt("#A31515")
        self.add_rule(r'"[^"\\]*(\\.[^"\\]*)*"', sf)
        self.add_rule(r"'[^']*'", sf)
        self.add_rule(r'@".*?"@', sf)

        cf = self._fmt("#008000")
        self.add_rule(r'#.*', cf)
        self.add_rule(r'<#[\s\S]*?#>', cf)

        nf = self._fmt("#098658")
        self.add_rule(r'\b\d+\b', nf)
        self.add_rule(r'\b0x[0-9a-fA-F]+\b', nf)

        of = self._fmt("#000000")
        self.add_rule(r'[-+*/%=]', of)
        self.add_rule(r'-eq|-ne|-gt|-lt|-le|-ge|-like|-notlike|-match|-notmatch|-contains|-notcontains|-in|-notin|-replace', of)


def createHighlighter(file_path: str, parent=None):
    """根据文件扩展名创建高亮器"""
    if not file_path:
        return None

    try:
        file_path_lower = file_path.lower()
    except (AttributeError, TypeError):
        return None

    try:
        if file_path_lower.endswith('.py'):
            return PythonHighlighter(parent)
        elif file_path_lower.endswith(('.cpp', '.cc', '.cxx', '.h', '.hpp', '.c', '.hxx')):
            return CppHighlighter(parent)
        elif file_path_lower.endswith(('.bat', '.cmd')):
            return CmdHighlighter(parent)
        elif file_path_lower.endswith('.json'):
            return JsonHighlighter(parent)
        elif file_path_lower.endswith('.md'):
            return MarkdownHighlighter(parent)
        elif file_path_lower.endswith(('.sh', '.bash', '.zsh', '.fish', '.ksh', '.csh', '.tcsh')):
            return ShellHighlighter(parent)
        elif file_path_lower.endswith('.go'):
            return GoHighlighter(parent)
        elif file_path_lower.endswith('.java'):
            return JavaHighlighter(parent)
        elif file_path_lower.endswith(('.js', '.jsx', '.mjs', '.cjs')):
            return JavaScriptHighlighter(parent)
        elif file_path_lower.endswith(('.ts', '.tsx')):
            return TypeScriptHighlighter(parent)
        elif file_path_lower.endswith('.rs'):
            return RustHighlighter(parent)
        elif file_path_lower.endswith(('.ps1', '.psm1', '.psd1', '.pssc', '.psrc')):
            return PowerShellHighlighter(parent)
        elif file_path_lower.endswith(('.html', '.htm', '.xml', '.xhtml', '.svg')):
            return HTMLHighlighter(parent)
        else:
            return None
    except Exception:
        logger.exception("创建语法高亮器失败")
        return None
