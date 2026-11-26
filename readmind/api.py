"""Deployable HTTP service (requirement 11).

    uvicorn readmind.api:app --host 0.0.0.0 --port ${PORT:-8080}

Endpoints
    GET  /                    minimal web UI (ask a question, pick pattern / messaging / prompt variant)
    POST /api/ask             {question, pattern, messaging, variant} -> RunResult (Pydantic JSON) + markdown
    GET  /api/health          liveness + loaded corpus sizes + model
    GET  /api/options         available patterns, messaging architectures, prompt variants
    GET  /api/agents          A2A agent cards served by this instance
    GET  /api/traces/{run_id} full trace (events, spans, provenance ledger, OTel spans) of a run
"""
from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from readmind.messaging import CHANNELS
from readmind.patterns import PATTERN_INFO
from readmind.prompts import VARIANTS
from readmind.schemas import RunResult
from readmind.settings import get_settings

_RM = None
_LOCK = threading.Lock()          # one run at a time: the monitor is process-wide


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _RM
    from readmind.system import ReadMind
    _RM = ReadMind.start()
    yield
    _RM.shutdown()


app = FastAPI(title="ReadMind — repository history agents (ADK + CrewAI)", version="1.0.0", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    pattern: str = "sequential"
    messaging: str = "blackboard"
    variant: str = "default"


class AskResponse(BaseModel):
    result: RunResult
    markdown: str


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": _RM is not None, "model": "mock" if s.is_mock else s.model, "repo": s.repo_github,
            "corpus": getattr(_RM, "store_stats", {})}


@app.get("/api/options")
def options():
    return {"patterns": PATTERN_INFO, "messaging": sorted(CHANNELS),
            "variants": {k: v["dimension"] for k, v in VARIANTS.items()}}


@app.get("/api/agents")
def agents():
    from readmind import a2a_bridge
    return a2a_bridge.agent_cards()


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest):
    if req.pattern not in PATTERN_INFO or req.messaging not in CHANNELS or req.variant not in VARIANTS:
        raise HTTPException(400, "unknown pattern / messaging / variant — see /api/options")
    with _LOCK:
        res = _RM.ask(req.question, pattern=req.pattern, messaging=req.messaging, variant=req.variant)
    return AskResponse(result=res, markdown=res.final.to_markdown())


@app.get("/api/traces/{run_id}")
def trace(run_id: str):
    p = get_settings().work_dir / "traces" / f"{Path(run_id).name}.json"
    if not p.exists():
        raise HTTPException(404, "no such run")
    return json.loads(p.read_text())


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReadMind</title><style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#111;--muted:#555;--line:#ddd;--accent:#2a78d6}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--card:#1c1c1c;--ink:#eee;--muted:#aaa;--line:#333;--accent:#5aa0ef}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
textarea,select,button{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px}
textarea{width:100%;box-sizing:border-box;min-height:70px}.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
button{background:var(--accent);color:#fff;border:0;cursor:pointer}pre{white-space:pre-wrap;font-size:13px}
.muted{color:var(--muted);font-size:13px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:4px;text-align:left}
</style></head><body><main>
<h1>ReadMind</h1><p class="muted">Why does the repository look the way it does? Google ADK + CrewAI agents over MCP tools and A2A.</p>
<div class="card"><textarea id="q">Why was PAE support removed from x86 32-bit under SELFOUR-501?</textarea>
<div class="row"><select id="pattern"></select><select id="messaging"></select><select id="variant"></select><button id="go">Ask</button></div></div>
<div class="card" id="out"><span class="muted">Answer appears here.</span></div>
<script>
async function init(){const o=await (await fetch('/api/options')).json();
 const fill=(id,arr)=>{document.getElementById(id).innerHTML=arr.map(x=>`<option>${x}</option>`).join('')};
 fill('pattern',Object.keys(o.patterns));document.getElementById('pattern').value='sequential';fill('messaging',o.messaging);fill('variant',Object.keys(o.variants));}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
document.getElementById('go').onclick=async()=>{const out=document.getElementById('out');out.innerHTML='<span class="muted">Running agents…</span>';
 const body={question:q.value,pattern:pattern.value,messaging:messaging.value,variant:variant.value};
 const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!r.ok){out.textContent='Error '+r.status;return}const d=await r.json();const m=d.result.metrics;
 out.innerHTML=`<pre>${esc(d.markdown)}</pre><p class="muted">status ${d.result.status} · ${m.llm_calls} LLM calls · ${m.total_tokens} tokens · $${m.cost_usd} · ${(m.latency_ms/1000).toFixed(1)} s · agents: ${esc(d.result.agents_invoked.join(', '))} · <a href="/api/traces/${d.result.run_id}">trace</a></p>`;};
init();
</script></main></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
