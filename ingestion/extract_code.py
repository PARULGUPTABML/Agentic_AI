"""Chunk the current source code (function/class level) and documentation (heading level)."""
import ast
import os
import re
from pathlib import Path

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".java", ".go", ".rb", ".php", ".rs",
            ".c", ".h", ".cpp", ".hpp", ".cs", ".kt", ".swift", ".scala"}
DOC_EXT = {".md", ".rst"}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv",
             "site-packages", ".tox", "third_party", ".next", "coverage", ".mypy_cache"}
MAX_FILE_BYTES = 300_000
MAX_CHUNK_CHARS = 4000
WINDOW = 80          # lines per chunk for fallback splitting
MIN_LINES = 15       # merge smaller neighbouring segments
MAX_DOC_FILES = 300

# Rough "start of a function/class" detector for non-Python languages
FUNC_START_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function\b|class\b|def\b|func\b|fn\b|interface\b|struct\b|impl\b"
    r"|(?:public|private|protected|internal|static|final|override|suspend|\s)+[\w<>\[\],.?\s]+\s+\w+\s*\("
    r"|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>)"
)


def iter_files(root: Path, exts: set[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in exts:
                continue
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                if p.stat().st_size <= MAX_FILE_BYTES:
                    yield p
            except OSError:
                continue


def _chunk(path, start, end, lines, symbol):
    text = "\n".join(lines[start - 1:end])[:MAX_CHUNK_CHARS]
    return {"id": f"code:{path}:{start}-{end}", "type": "code", "path": path,
            "start": start, "end": end, "symbol": symbol,
            "text": f"File: {path} (lines {start}-{end})\nSymbol: {symbol}\n\n{text}"}


def _split_windows(path, start, end, lines, symbol):
    out = []
    s = start
    while s <= end:
        e = min(s + WINDOW - 1, end)
        out.append(_chunk(path, s, e, lines, symbol))
        s = e + 1
    return out


def chunk_python(path: str, source: str) -> list[dict]:
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return chunk_generic(path, source)
    chunks, covered = [], set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        end = node.end_lineno or start
        covered.update(range(start, end + 1))
        kind = "class" if isinstance(node, ast.ClassDef) else "def"
        if isinstance(node, ast.ClassDef) and end - start > WINDOW * 1.5:
            methods = [m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            first = min([m.lineno for m in methods] + [end])
            chunks.append(_chunk(path, start, max(start, first - 1), lines, f"class {node.name}"))
            for m in methods:
                ms = min([d.lineno for d in m.decorator_list] + [m.lineno])
                me = m.end_lineno or ms
                sym = f"{node.name}.{m.name}"
                chunks.extend(_split_windows(path, ms, me, lines, sym) if me - ms > WINDOW * 1.5
                              else [_chunk(path, ms, me, lines, sym)])
        elif end - start > WINDOW * 1.5:
            chunks.extend(_split_windows(path, start, end, lines, f"{kind} {node.name}"))
        else:
            chunks.append(_chunk(path, start, end, lines, f"{kind} {node.name}"))
    # module-level code (imports, constants, config) as one chunk
    module_lines = [i for i in range(1, len(lines) + 1) if i not in covered and lines[i - 1].strip()]
    if module_lines:
        s, e = module_lines[0], min(module_lines[-1], module_lines[0] + WINDOW - 1)
        chunks.append(_chunk(path, s, e, lines, "module"))
    return chunks


def chunk_generic(path: str, source: str) -> list[dict]:
    lines = source.splitlines()
    if not lines:
        return []
    starts = [i + 1 for i, line in enumerate(lines) if FUNC_START_RE.match(line)]
    if not starts or starts[0] != 1:
        starts = [1] + starts
    segments = []
    for i, s in enumerate(starts):
        e = (starts[i + 1] - 1) if i + 1 < len(starts) else len(lines)
        if segments and (segments[-1][1] - segments[-1][0] + 1) < MIN_LINES:
            segments[-1] = (segments[-1][0], e)   # merge tiny segment forward
        else:
            segments.append((s, e))
    chunks = []
    for s, e in segments:
        symbol = lines[s - 1].strip()[:80] or "block"
        chunks.extend(_split_windows(path, s, e, lines, symbol) if e - s > WINDOW * 1.5
                      else [_chunk(path, s, e, lines, symbol)])
    return chunks


def extract_code_chunks(repo_path: Path) -> list[dict]:
    chunks = []
    for p in iter_files(repo_path, CODE_EXT):
        rel = p.relative_to(repo_path).as_posix()
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks.extend(chunk_python(rel, src) if p.suffix == ".py" else chunk_generic(rel, src))
    return chunks


def extract_doc_chunks(repo_path: Path) -> list[dict]:
    chunks = []
    for n, p in enumerate(iter_files(repo_path, DOC_EXT)):
        if n >= MAX_DOC_FILES:
            break
        rel = p.relative_to(repo_path).as_posix()
        text = p.read_text(encoding="utf-8", errors="ignore")
        sections = re.split(r"(?m)^(?=#{1,3} )", text)
        for i, sec in enumerate(s for s in sections if s.strip()):
            title = sec.strip().splitlines()[0].lstrip("# ").strip()[:120]
            chunks.append({"id": f"doc:{rel}#{i}", "type": "doc", "path": rel, "title": title,
                           "text": f"Document: {rel} — {title}\n\n{sec.strip()[:3000]}"})
    return chunks
