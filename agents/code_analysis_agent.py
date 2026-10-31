"""Code Analysis Agent — grounds answers in the *current* code snapshot.

Answers "WHAT does the code look like now?" so the History/RAG and Issue/PR
agents can explain "WHY".  The agent is tool-driven and deterministic (no LLM
call), which keeps it cheap, reproducible and easy to evaluate:

    understand query → find_files → find_definitions → grep_keywords → rank → outline + snippet

Tools
-----
    find_files(path_or_name)   path / basename / path-segment matching
    find_definitions(symbol)   C functions, macros, structs/enums/typedefs, .bf blocks,
                               Python def/class (via ``ast``)
    grep_keywords(terms)       IDF-weighted keyword hits per file
    outline(file)              functions / types declared in a file

Evidence posted
---------------
    code:<relative/path>          type="code"   current file, best snippet with line numbers
    code:absent:<relative/path>   type="code"   a file named in the question that does NOT exist
                                                in the snapshot (signals removal/rename; useful for
                                                "superseded implementation" questions)

Interface — compatible with every architecture variant::

    agent.run("Why was lock.c moved to src/smp?", repo_slug="seL4__seL4") -> list[EvidenceFragment]
    agent.run(blackboard)   # posts to the Blackboard and logs each tool call
"""
from __future__ import annotations

import ast
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from agents.blackboard import Blackboard, EvidenceFragment
from ingestion.corpus_store import STOPWORDS, tokenize

AGENT_NAME = "code_analysis"

CODE_EXTENSIONS = {
    ".c", ".h", ".S", ".s", ".py", ".cmake", ".bf", ".thy", ".rs", ".cpp", ".cc", ".hpp",
    ".go", ".js", ".ts", ".tsx", ".jsx", ".java", ".sh", ".yml", ".yaml", ".toml", ".md",
    ".txt", ".xml", ".dts", ".json", ".ld", ".lds",
}
SKIP_DIRS = {".git", "node_modules", "build", "dist", "__pycache__", ".venv", "venv", ".tox"}
MAX_FILE_BYTES = 400_000

# Words that come from the orchestrator's sub-query templates, not from the question.
TEMPLATE_WORDS = {
    "analyze", "code", "structure", "implementation", "trade", "offs", "tradeoffs", "examine",
    "check", "version", "tags", "changelog", "header", "headers", "definitions", "confirm",
    "inspect", "legacy", "deprecated", "functions", "replacements", "compare", "context",
    "commit", "commits", "logs", "diffs", "rationale", "history", "issue", "issues", "pr", "prs",
    "pull", "request", "requests", "discussion", "discussions", "review", "reviews", "find",
    "search", "older", "removed", "superseded", "supersede", "why", "reason", "support",
    "introduced", "moved", "added", "removed", "changed", "kernel", "file", "files", "directory",
    # generic change / question verbs that say nothing about *where* code lives
    "implemented", "implement", "implementing", "behavior", "behaviour", "moving", "move",
    "checking", "relocated", "relocate", "platforms", "platform", "architecture", "generic",
    "unified", "unify", "operations", "operation", "bit", "used", "use", "using", "made",
    "make", "new", "old", "first", "later", "change", "changes", "added", "adding", "remove",
    "removal", "replace", "replaced", "previous", "current", "currently", "approach", "design",
    "decision", "originally", "instead", "become", "became", "happen", "happened", "date",
    "year", "who", "author", "authored", "merged", "created", "version", "sel4",
}
DOC_LIKE = (".md", ".txt", ".yml", ".yaml", ".json", ".xml", ".dts", ".cmake", ".toml")
C_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "case", "do", "else", "typeof",
              "defined", "assert", "static_assert", "compile_assert"}

_FILE_REF = re.compile(r"[\w.\-/]+\.(?:c|h|S|s|py|cmake|bf|thy|rs|cpp|hpp|go|ts|js|md|txt|yml|yaml|dts)\b")
_SYMBOL = re.compile(r"\b(?:[A-Za-z_]\w*_\w+|[a-z]+[A-Z]\w*|[A-Za-z_]\w*(?=\(\)))\b")
_C_FUNC_DEF_LINE = re.compile(r"^(?!\s)(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**([A-Za-z_]\w*)\s*\([^;]*$")


def _strip_template(query: str) -> str:
    """'Analyze code structure ... for: <question>' -> '<question>'."""
    if " for: " in query:
        return query.split(" for: ", 1)[1]
    return query


class CodeAnalysisAgent:
    agent_name = AGENT_NAME

    def __init__(
        self,
        repo_slug: str = "seL4__seL4",
        repo_path: str | os.PathLike | None = None,
        index: Any = None,
        k: int = 5,
        snippet_radius: int = 12,
        github: str | None = None,
    ):
        self.repo_slug = repo_slug
        self.k = k
        self.snippet_radius = snippet_radius
        self.github = github or repo_slug.replace("__", "/")
        self.repo_path = self._resolve_repo_path(repo_slug, repo_path)
        self.index = index
        self.last_trace: list[dict[str, Any]] = []
        self._files: list[str] | None = None
        self._cache: dict[str, list[str]] = {}
        self._lower: dict[str, str] = {}
        self._dates: dict[str, str] = {}
        self._is_git = bool(self.repo_path and (self.repo_path / ".git").exists())

    # ------------------------------------------------------------------ #
    #  Setup                                                              #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_repo_path(slug: str, explicit) -> Path | None:
        candidates = [explicit, os.environ.get("CODE_REPO_PATH"),
                      f"repos/{slug}", f"data/repos/{slug}", f"../repos/{slug}", f"data/{slug}"]
        for c in candidates:
            if c and Path(c).is_dir():
                return Path(c).resolve()
        return None

    def _list_files(self) -> list[str]:
        if self._files is not None:
            return self._files
        files: list[str] = []
        if self.repo_path is None:
            self._files = files
            return files
        if self._is_git:
            try:
                out = subprocess.run(["git", "-C", str(self.repo_path), "ls-files"],
                                     capture_output=True, text=True, check=True).stdout
                files = [f for f in out.splitlines() if self._wanted(f)]
            except (OSError, subprocess.CalledProcessError):
                files = []
        if not files:
            for root, dirs, names in os.walk(self.repo_path):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for n in names:
                    rel = os.path.relpath(os.path.join(root, n), self.repo_path).replace(os.sep, "/")
                    if self._wanted(rel):
                        files.append(rel)
        self._files = sorted(files)
        return self._files

    @staticmethod
    def _wanted(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        ext = os.path.splitext(name)[1]
        return ext in CODE_EXTENSIONS or name in ("CMakeLists.txt", "Makefile", "Kconfig")

    def _lines(self, rel: str) -> list[str]:
        if rel not in self._cache:
            p = self.repo_path / rel
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    self._cache[rel] = []
                else:
                    self._cache[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self._cache[rel] = []
        return self._cache[rel]

    def _lower_text(self, rel: str) -> str:
        if rel not in self._lower:
            self._lower[rel] = "\n".join(self._lines(rel)).lower()
        return self._lower[rel]

    def _last_change(self, rel: str) -> str:
        if not self._is_git:
            return ""
        if rel not in self._dates:
            try:
                self._dates[rel] = subprocess.run(
                    ["git", "-C", str(self.repo_path), "log", "-1", "--format=%cI", "--", rel],
                    capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                self._dates[rel] = ""
        return self._dates[rel]

    # ------------------------------------------------------------------ #
    #  Query understanding                                                #
    # ------------------------------------------------------------------ #
    def analyse_query(self, query: str) -> dict[str, list[str]]:
        q = _strip_template(query)
        files = sorted(set(_FILE_REF.findall(q)))
        symbols = sorted({s for s in _SYMBOL.findall(q)
                          if not _FILE_REF.fullmatch(s) and s.lower() not in STOPWORDS})
        try:   # reuse the orchestrator's entity extractor so both agents agree
            from orchestrator.agent import OrchestratorAgent
            ents = OrchestratorAgent.extract_entities_heuristic(None, q)  # type: ignore[arg-type]
            files = sorted(set(files) | set(ents.files))
            symbols = sorted(set(symbols) | {f for f in ents.functions if not _FILE_REF.fullmatch(f)})
            features = sorted(set(ents.features))
        except Exception:
            features = sorted(set(re.findall(r"\b[A-Z]{2,}[0-9]*\b", q)))
        file_tokens = {t for f in files for t in tokenize(f)}
        keywords = [t for t in dict.fromkeys(tokenize(q))
                    if t not in TEMPLATE_WORDS and not t.isdigit() and len(t) > 2]
        keywords += [f.lower() for f in features if f.lower() not in keywords]
        keywords = [k for k in keywords if k not in file_tokens or k in {f.lower() for f in features}]
        return {"files": files, "symbols": symbols, "features": features, "keywords": keywords[:12]}

    # ------------------------------------------------------------------ #
    #  Tools                                                              #
    # ------------------------------------------------------------------ #
    def _tool(self, name: str, arg: Any, fn, bb: Blackboard | None):
        t0 = time.perf_counter()
        result = fn()
        ms = (time.perf_counter() - t0) * 1000
        n = len(result) if hasattr(result, "__len__") else 0
        self.last_trace.append({"tool": name, "arg": arg, "results": n, "ms": round(ms, 2)})
        if bb is not None:
            bb.log(self.agent_name, f"tool_{name}", f"{arg!r} -> {n} results", duration_ms=ms)
        return result

    def find_files(self, refs: list[str], keywords: list[str]) -> tuple[dict[str, float], list[str]]:
        """Score files by path similarity. Returns (scores, missing_refs)."""
        files = self._list_files()
        scores: dict[str, float] = {}
        missing: list[str] = []
        for ref in refs:
            ref_n = ref.strip("./")
            base = ref_n.rsplit("/", 1)[-1]
            exact = [f for f in files if f == ref_n or f.endswith("/" + ref_n)]
            by_base = [f for f in files if f.rsplit("/", 1)[-1] == base]
            for f in exact:
                scores[f] = scores.get(f, 0) + 12.0
            for f in by_base:
                if f not in exact:
                    scores[f] = scores.get(f, 0) + (6.0 if len(by_base) <= 3 else 2.0)
            if not exact and "/" in ref_n:
                missing.append(ref_n)
        kw = set(keywords)
        if kw:
            for f in files:
                segs = set(tokenize(f.replace("/", " ").replace(".", " ")))
                hit = len(segs & kw)
                if hit:
                    scores[f] = scores.get(f, 0) + min(4.5, 1.5 * hit)
        return scores, missing

    def _definition_kind(self, line: str, sym: str, rel: str) -> str | None:
        s = re.escape(sym)
        if re.match(rf"^\s*#\s*define\s+{s}\b", line):
            return "macro"
        if re.match(rf"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+{s}\b", line) or \
                re.match(rf"^\s*}}\s*{s}(?:_t)?\s*;", line):
            return "type"
        if rel.endswith(".bf") and re.match(rf"^\s*(?:block|tagged_union)\s+{s}\b", line):
            return "bitfield"
        if rel.endswith(".py") and re.match(rf"^\s*(?:async\s+)?(?:def|class)\s+{s}\b", line):
            return "python_def"
        m = _C_FUNC_DEF_LINE.match(line)
        if m and m.group(1) == sym and not line.rstrip().endswith(";") and \
                rel.endswith((".c", ".h", ".S", ".cpp", ".cc", ".hpp")):
            return "function"
        return None

    def find_definitions(self, symbols: list[str]) -> dict[str, list[dict]]:
        """``{file: [{symbol, kind, line}]}`` for definitions; references counted separately."""
        out: dict[str, list[dict]] = {}
        if not symbols:
            return out
        lowered = [s.lower() for s in symbols]
        for rel in self._list_files():
            text = self._lower_text(rel)
            if not any(s in text for s in lowered):
                continue
            for i, line in enumerate(self._lines(rel)):
                for sym in symbols:
                    if sym in line:
                        kind = self._definition_kind(line, sym, rel)
                        if kind:
                            out.setdefault(rel, []).append({"symbol": sym, "kind": kind, "line": i + 1})
                        else:
                            out.setdefault(rel, []).append({"symbol": sym, "kind": "reference", "line": i + 1})
        return out

    def grep_keywords(self, keywords: list[str]) -> dict[str, dict[str, int]]:
        """``{file: {keyword: count}}`` using whole-word matching."""
        out: dict[str, dict[str, int]] = {}
        if not keywords:
            return out
        pats = {k: re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])") for k in keywords}
        for rel in self._list_files():
            text = self._lower_text(rel)
            counts = {}
            for k, p in pats.items():
                if k in text:
                    c = len(p.findall(text))
                    if c:
                        counts[k] = c
            if counts:
                out[rel] = counts
        return out

    def outline(self, rel: str, limit: int = 20) -> list[str]:
        lines = self._lines(rel)
        names: list[str] = []
        if rel.endswith(".py"):
            try:
                tree = ast.parse("\n".join(lines))
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.append(f"{node.name}()")
                    elif isinstance(node, ast.ClassDef):
                        names.append(f"class {node.name}")
            except SyntaxError:
                pass
        else:
            for line in lines:
                m = _C_FUNC_DEF_LINE.match(line)
                if m and m.group(1) not in C_KEYWORDS and not line.rstrip().endswith(";"):
                    names.append(f"{m.group(1)}()")
                else:
                    t = re.match(r"^\s*(?:typedef\s+)?(struct|enum|union|block|tagged_union)\s+(\w+)", line)
                    if t:
                        names.append(f"{t.group(1)} {t.group(2)}")
        return list(dict.fromkeys(names))[:limit]

    # ------------------------------------------------------------------ #
    #  Ranking + evidence                                                 #
    # ------------------------------------------------------------------ #
    def _rank(self, path_scores, defs, kw_hits, n_files: int) -> list[tuple[str, float, dict]]:
        df: dict[str, int] = {}
        for counts in kw_hits.values():
            for k in counts:
                df[k] = df.get(k, 0) + 1
        # Rare keywords carry the signal; keywords found in >25% of files are near-zero weight.
        idf = {k: max(0.0, math.log((n_files + 1) / (d + 0.5)) - math.log(4)) + 0.05 for k, d in df.items()}
        idf_total = sum(idf.values()) or 1.0
        cands = set(path_scores) | set(defs) | set(kw_hits)
        ranked = []
        for f in cands:
            d = defs.get(f, [])
            n_def = sum(1 for x in d if x["kind"] != "reference")
            n_ref = sum(1 for x in d if x["kind"] == "reference")
            kw = kw_hits.get(f, {})
            # bounded [0,1]: IDF-weighted coverage, saturating in hit count
            kw_norm = sum(idf[k] * min(1.0, math.log1p(c) / math.log1p(15)) for k, c in kw.items()) / idf_total
            score = (path_scores.get(f, 0.0) + 8.0 * min(n_def, 3) + 0.3 * min(n_ref, 10)
                     + 10.0 * kw_norm)
            name = f.rsplit("/", 1)[-1]
            if f.endswith(DOC_LIKE) or name in ("CMakeLists.txt", "Makefile", "Kconfig"):
                score *= 0.5     # prefer source over docs/build config for "what" questions
            if score < 2.0:
                continue        # weak path-segment-only matches are noise
            strength = max(min(1.0, 1.5 * kw_norm), 1.0 if n_def else 0.0,
                           0.9 if path_scores.get(f, 0.0) >= 6.0 else 0.0)
            ranked.append((f, score, {"defs": d, "keywords": kw, "kw_norm": round(kw_norm, 4),
                                      "strength": strength}))
        ranked.sort(key=lambda x: (-x[1], x[0]))
        return ranked

    def _focus_line(self, rel: str, info: dict) -> int:
        defs = [x for x in info["defs"] if x["kind"] != "reference"]
        if defs:
            return defs[0]["line"]
        lines = self._lines(rel)
        kws = list(info["keywords"])
        if not kws or not lines:
            return 1
        best, best_i, window = -1, 1, self.snippet_radius
        hits = [sum(1 for k in kws if k in l.lower()) for l in lines]
        run = sum(hits[:2 * window])
        for i in range(len(lines)):
            if run > best:
                best, best_i = run, i + window
            if i + 2 * window < len(lines):
                run += hits[i + 2 * window]
            run -= hits[i]
        return min(max(1, best_i), len(lines))

    def _snippet(self, rel: str, focus: int) -> tuple[str, int, int]:
        lines = self._lines(rel)
        a = max(1, focus - self.snippet_radius // 2)
        b = min(len(lines), a + 2 * self.snippet_radius)
        body = "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(a, b + 1))
        return body, a, b

    def _fragment(self, rel: str, score: float, best: float, info: dict) -> EvidenceFragment:
        focus = self._focus_line(rel, info)
        snippet, a, b = self._snippet(rel, focus)
        defs = [x for x in info["defs"] if x["kind"] != "reference"]
        reasons = []
        if defs:
            reasons.append("defines " + ", ".join(sorted({f"{x['symbol']} ({x['kind']})" for x in defs})))
        if info["keywords"]:
            reasons.append("mentions " + ", ".join(sorted(info["keywords"], key=lambda k: -info["keywords"][k])[:5]))
        outline = self.outline(rel)
        n_lines = len(self._lines(rel))
        content = (f"File: {rel} ({n_lines} lines) — current snapshot\n"
                   f"Why matched: {'; '.join(reasons) or 'path match'}\n"
                   + (f"Declares: {', '.join(outline)}\n" if outline else "")
                   + f"\nLines {a}-{b}:\n{snippet}")
        # absolute match strength x relative rank, so weak best-of-a-bad-lot hits stay low
        rank_factor = (score / best) ** 0.5 if best else 0.0
        confidence = round(0.2 + 0.7 * info.get("strength", 0.5) * rank_factor, 4)
        return EvidenceFragment(
            id=f"code:{rel}",
            source_agent=self.agent_name,
            evidence_type="code",
            title=f"{rel} — {reasons[0] if reasons else 'path match'}"[:160],
            content=content,
            confidence=min(confidence, 0.9),
            url=f"https://github.com/{self.github}/blob/HEAD/{rel}#L{a}-L{b}",
            date=self._last_change(rel),
            files=[rel],
            metadata={"line_start": a, "line_end": b, "score": round(score, 3),
                      "keyword_coverage": info.get("kw_norm", 0.0),
                      "definitions": defs[:10], "keyword_hits": info["keywords"], "outline": outline},
        )

    def _absent_fragment(self, rel: str) -> EvidenceFragment:
        return EvidenceFragment(
            id=f"code:absent:{rel}",
            source_agent=self.agent_name,
            evidence_type="code",
            title=f"{rel} is not present in the current snapshot",
            content=(f"The file '{rel}' named in the question does not exist in the current code "
                     f"snapshot of {self.github}. It was probably removed, renamed or moved; the "
                     f"History/RAG Agent should locate the commit that deleted or moved it."),
            confidence=0.3,
            files=[rel],
            metadata={"absent": True},
        )

    # ------------------------------------------------------------------ #
    #  Index fallback (no local checkout)                                 #
    # ------------------------------------------------------------------ #
    def _from_index(self, query: str, bb: Blackboard | None) -> list[EvidenceFragment]:
        idx = self.index
        if idx is None:
            try:
                from rag.retriever import get_index
                idx = get_index(self.repo_slug)
            except Exception:
                return []
        if not isinstance(getattr(idx, "docs", None), dict):
            return []
        res = self._tool("index_retrieve", query,
                         lambda: idx.retrieve(query, types={"code", "doc"}, k=self.k, expand=False, rerank=False)[0], bb)
        return [EvidenceFragment(id=r["id"], source_agent=self.agent_name, evidence_type=r.get("type", "code"),
                                 title=r.get("title", ""), content=r.get("text", ""),
                                 confidence=min(1.0, max(0.0, float(r.get("score", 0.5)))),
                                 url=r.get("url", ""), date=r.get("date", ""),
                                 files=r.get("files", []) if isinstance(r.get("files"), list) else [])
                for r in res]

    # ------------------------------------------------------------------ #
    #  Entry point                                                        #
    # ------------------------------------------------------------------ #
    def run(self, query: "str | Blackboard", repo_slug: str | None = None) -> list[EvidenceFragment]:
        bb = query if isinstance(query, Blackboard) else None
        if bb is not None:
            text = bb.question
            if bb.retry_queries:
                text = f"{text} {' '.join(bb.retry_queries)}"
        else:
            text = str(query)
        if repo_slug and repo_slug != self.repo_slug and self.repo_path is None:
            self.repo_slug = repo_slug
            self.repo_path = self._resolve_repo_path(repo_slug, None)
            self._is_git = bool(self.repo_path and (self.repo_path / ".git").exists())
            self._files = None

        self.last_trace = []
        t0 = time.perf_counter()
        if bb is not None:
            bb.log(self.agent_name, "start", f"Code analysis for: {text[:200]}")

        if self.repo_path is None:
            frags = self._from_index(_strip_template(text), bb)
        else:
            q = self.analyse_query(text)
            self.last_trace.append({"tool": "analyse_query", "arg": text[:200], "results": q, "ms": 0.0})
            files = self._list_files()
            path_scores, missing = self._tool("find_files", q["files"],
                                              lambda: self.find_files(q["files"], q["keywords"]), bb)
            defs = self._tool("find_definitions", q["symbols"], lambda: self.find_definitions(q["symbols"]), bb)
            kw_hits = self._tool("grep_keywords", q["keywords"], lambda: self.grep_keywords(q["keywords"]), bb)
            ranked = self._rank(path_scores, defs, kw_hits, len(files))[: self.k]
            best = ranked[0][1] if ranked else 0.0
            frags = [self._fragment(f, s, best, info) for f, s, info in ranked if s > 0]
            for f in frags:
                self._tool("outline", f.files[0], lambda f=f: f.metadata.get("outline", []), bb)
            frags += [self._absent_fragment(m) for m in missing]

        ms = (time.perf_counter() - t0) * 1000
        if bb is not None:
            posted = bb.post_evidence(self.agent_name, frags)
            bb.log(self.agent_name, "complete", f"Posted {posted} code fragments in {ms:.0f}ms", duration_ms=ms)
        return frags
