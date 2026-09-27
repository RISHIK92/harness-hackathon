from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Symbol:
    name: str
    kind: str          # function | class | method | variable
    start_line: int
    end_line: int
    docstring: str = ""


@dataclass
class ParsedFile:
    path: str
    language: str
    raw_text: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


def _parse_python(text: str) -> tuple[list[Symbol], list[str]]:
    symbols: list[Symbol] = []
    imports: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            imports.append(stripped)
            continue
        m = re.match(r"^class\s+(\w+)", line)
        if m:
            end = _find_block_end_python(lines, i)
            doc = _extract_docstring_python(lines, i + 1)
            symbols.append(Symbol(name=m.group(1), kind="class", start_line=i + 1, end_line=end, docstring=doc))
            continue
        m = re.match(r"^(\s*)(?:async\s+)?def\s+(\w+)", line)
        if m:
            indent = len(m.group(1))
            name = m.group(2)
            kind = "method" if indent > 0 else "function"
            end = _find_block_end_python(lines, i)
            doc = _extract_docstring_python(lines, i + 1)
            symbols.append(Symbol(name=name, kind=kind, start_line=i + 1, end_line=end, docstring=doc))
    return symbols, imports


def _find_block_end_python(lines: list[str], start: int) -> int:
    if start >= len(lines):
        return start + 1
    header = lines[start]
    base_indent = len(header) - len(header.lstrip())
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if l.strip() == "":
            continue
        indent = len(l) - len(l.lstrip())
        if indent <= base_indent:
            return i
    return len(lines)


def _extract_docstring_python(lines: list[str], start: int) -> str:
    for i in range(start, min(start + 3, len(lines))):
        stripped = lines[i].strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            content = stripped[3:]
            if content.endswith(quote):
                return content[:-3].strip()
            parts = [content]
            for j in range(i + 1, min(i + 20, len(lines))):
                seg = lines[j].strip()
                if seg.endswith(quote):
                    parts.append(seg[:-3])
                    break
                parts.append(seg)
            return " ".join(parts).strip()
    return ""


def _parse_js_ts(text: str) -> tuple[list[Symbol], list[str]]:
    symbols: list[Symbol] = []
    imports: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("require("):
            imports.append(stripped)
            continue
        m = re.match(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="function", start_line=i + 1, end_line=end))
            continue
        m = re.match(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(", line)
        if m and "=>" in line:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="function", start_line=i + 1, end_line=end))
            continue
        m = re.match(r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="class", start_line=i + 1, end_line=end))
    return symbols, imports


def _find_block_end_brace(lines: list[str], start: int) -> int:
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth > 0 and i > start:
            if depth == 0:
                return i + 1
        if depth <= 0 and i > start:
            return i + 1
    return len(lines)


def _parse_go(text: str) -> tuple[list[Symbol], list[str]]:
    symbols: list[Symbol] = []
    imports: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if re.match(r'^import\s+"', stripped):
            imports.append(stripped)
            i += 1
            continue
        if re.match(r'^import\s+\(', stripped):
            i += 1
            while i < len(lines) and ")" not in lines[i]:
                imp = lines[i].strip().strip('"')
                if imp:
                    imports.append(f'import "{imp}"')
                i += 1
            i += 1
            continue
        m = re.match(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="function", start_line=i + 1, end_line=end))
            i += 1
            continue
        m = re.match(r"^type\s+(\w+)\s+(struct|interface)\s*\{", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="class", start_line=i + 1, end_line=end))
            i += 1
            continue
        i += 1
    return symbols, imports


def _parse_rust(text: str) -> tuple[list[Symbol], list[str]]:
    symbols: list[Symbol] = []
    imports: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("use ") and stripped.endswith(";"):
            imports.append(stripped)
            continue
        if re.match(r"^(?:pub\s+)?mod\s+\w+\s*;", stripped):
            imports.append(stripped)
            continue
        m = re.match(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="function", start_line=i + 1, end_line=end))
            continue
        m = re.match(r"^(?:pub\s+)?struct\s+(\w+)", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="class", start_line=i + 1, end_line=end))
            continue
        m = re.match(r"^(?:pub\s+)?enum\s+(\w+)", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(1), kind="class", start_line=i + 1, end_line=end))
            continue
        m = re.match(r"^impl(?:<[^>]+>)?\s+(?:\w+\s+for\s+)?(\w+)", line)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=f"impl {m.group(1)}", kind="class", start_line=i + 1, end_line=end))
            continue
    return symbols, imports


def _parse_java(text: str) -> tuple[list[Symbol], list[str]]:
    symbols: list[Symbol] = []
    imports: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") and stripped.endswith(";"):
            imports.append(stripped)
            continue
        m = re.match(
            r"^(?:(?:public|private|protected|abstract|final|static)\s+)*"
            r"(class|interface|enum|record)\s+(\w+)", stripped)
        if m:
            end = _find_block_end_brace(lines, i)
            symbols.append(Symbol(name=m.group(2), kind="class", start_line=i + 1, end_line=end))
            continue
        m = re.match(
            r"^(?:(?:public|private|protected|static|final|synchronized|abstract|native|default|override)\s+)*"
            r"(?:[\w<>\[\]]+\s+)+(\w+)\s*\(", stripped)
        if m and "{" in line and not re.search(r"\b(class|interface|enum|record|if|for|while|switch)\b", line):
            name = m.group(1)
            if name not in ("if", "for", "while", "switch", "catch", "return"):
                end = _find_block_end_brace(lines, i)
                symbols.append(Symbol(name=name, kind="function", start_line=i + 1, end_line=end))
            continue
    return symbols, imports


def _parse_generic(text: str) -> tuple[list[Symbol], list[str]]:
    """Minimal fallback for languages without a dedicated parser."""
    return [], []


_PARSERS = {
    "python":     _parse_python,
    "javascript": _parse_js_ts,
    "typescript": _parse_js_ts,
    "tsx":        _parse_js_ts,
    "jsx":        _parse_js_ts,
    "go":         _parse_go,
    "rust":       _parse_rust,
    "java":       _parse_java,
}


def parse_file(abs_path: str, language: str) -> ParsedFile:
    """Parse a source file and return a ParsedFile with symbols and imports."""
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    parser_fn = _PARSERS.get(language, _parse_generic)
    try:
        syms, imps = parser_fn(text)
    except Exception:
        syms, imps = [], []
    return ParsedFile(
        path=abs_path,
        language=language,
        raw_text=text,
        symbols=syms,
        imports=imps,
    )
