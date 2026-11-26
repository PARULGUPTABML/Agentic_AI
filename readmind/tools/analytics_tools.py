"""Analytics / external tools (second MCP server, ``readmind-analytics``).

    calculate(expression)          safe arithmetic (AST whitelist — no eval of names/calls)
    date_diff(start, end, unit)    elapsed time between two ISO dates (timeline questions)
    estimate_cost(p_tok, c_tok)    USD cost from the configured price sheet
    web_lookup(query, k)           external background lookup (Wikipedia search API, no key);
                                   degrades to ok=False when the host has no internet
"""
from __future__ import annotations

import ast
import json
import math
import operator
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from readmind.schemas import ToolResponse
from readmind.settings import get_settings

SERVER = "readmind-analytics"
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "abs": abs, "round": round, "min": min, "max": max}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def calculate(expression: str) -> dict:
    try:
        if len(expression) > 200:
            raise ValueError("expression too long")
        val = _eval(ast.parse(expression, mode="eval"))
        return ToolResponse(tool="calculate", server=SERVER, query=expression, value=float(val)).model_dump()
    except Exception as e:
        return ToolResponse(ok=False, tool="calculate", server=SERVER, query=expression, error=str(e)).model_dump()


def _parse_date(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            d = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {s}")


def date_diff(start: str, end: str, unit: str = "days") -> dict:
    try:
        delta = _parse_date(end) - _parse_date(start)
        days = delta.total_seconds() / 86400
        val = {"days": days, "weeks": days / 7, "months": days / 30.4375, "years": days / 365.25}.get(unit, days)
        return ToolResponse(tool="date_diff", server=SERVER, query=f"{start} -> {end} ({unit})",
                            value=round(val, 2)).model_dump()
    except Exception as e:
        return ToolResponse(ok=False, tool="date_diff", server=SERVER, query=f"{start} -> {end}", error=str(e)).model_dump()


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> dict:
    s = get_settings()
    usd = prompt_tokens / 1e6 * s.price_in_per_m + completion_tokens / 1e6 * s.price_out_per_m
    return ToolResponse(tool="estimate_cost", server=SERVER, query=f"{prompt_tokens}+{completion_tokens} tokens ({s.model})",
                        value=round(usd, 6)).model_dump()


def web_lookup(query: str, k: int = 3) -> dict:
    """Wikipedia search (background knowledge only — never repository evidence)."""
    q = (query or "").strip()[:200]
    if not q:
        return ToolResponse(ok=False, tool="web_lookup", server=SERVER, error="empty query").model_dump()
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": max(1, min(5, int(k)))})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ReadMind/1.0 (academic project)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        hits = []
        for h in data.get("query", {}).get("search", []):
            title = h.get("title", "")
            snippet = h.get("snippet", "").replace('<span class="searchmatch">', "").replace("</span>", "")
            hits.append({"id": f"web:{title.replace(' ', '_')}", "type": "web", "title": title, "snippet": snippet,
                         "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"})
        return ToolResponse(tool="web_lookup", server=SERVER, query=q, results=hits,
                            warnings=["background knowledge, not repository evidence"]).model_dump()
    except Exception as e:
        return ToolResponse(ok=False, tool="web_lookup", server=SERVER, query=q,
                            error=f"web lookup unavailable: {type(e).__name__}").model_dump()


ANALYTICS_TOOLS = {"calculate": calculate, "date_diff": date_diff, "estimate_cost": estimate_cost,
                   "web_lookup": web_lookup}
