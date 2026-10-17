"""
╔══════════════════════════════════════════════════════════════╗
║  LIVE DEMO: Multi-Agent System — Parul Gupta                ║
║  Shows how agents work step-by-step with visible output     ║
╚══════════════════════════════════════════════════════════════╝

Run:
    cd Agentic_AI
    set PYTHONPATH=C:/Users/Hp/Downloads/Agentic_AI;C:/Users/Hp/Downloads/Deveops/backend
    set LLM_PROVIDER=mock
    set EMBED_MODEL=hash
    set USE_RERANKER=false
    python demo.py
"""
import os
import sys
import time

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Setup environment for demo (mock mode — no API keys needed)
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBED_MODEL"] = "hash"
os.environ["USE_RERANKER"] = "false"

# Add paths
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Deveops", "backend"))

from agents.blackboard import Blackboard, BlackboardStatus, EvidenceFragment
from agents.history_rag_agent import HistoryRAGAgent
from agents.evidence_verification_agent import EvidenceVerificationAgent
from agents.multi_agent_pipeline import run_multi_agent
from agents.messaging import MessageBus


# ================================================================== #
#  Pretty printing helpers                                            #
# ================================================================== #

def banner(text, char="═"):
    width = 60
    print(f"\n{'╔' + char*width + '╗'}")
    print(f"║  {text:<{width-2}}║")
    print(f"{'╚' + char*width + '╝'}")


def section(text):
    print(f"\n{'─'*60}")
    print(f"  >> {text}")
    print(f"{'─'*60}")


def step(num, text):
    print(f"\n  ┌─ Step {num}: {text}")


def info(text):
    print(f"  │  {text}")


def result(text):
    print(f"  └─ ✅ {text}")


def warn(text):
    print(f"  └─ ⚠️  {text}")


def pause():
    print("\n  [Press Enter to continue...]")
    input()


# ================================================================== #
#  Demo 1: Blackboard in Action                                      #
# ================================================================== #

def demo_blackboard():
    banner("DEMO 1: BLACKBOARD — Shared Evidence Store")
    print("""
  The Blackboard is where all agents post their findings.
  Think of it as a shared whiteboard that everyone can read/write.
    """)

    step(1, "Creating Blackboard for a question")
    bb = Blackboard(
        question="Why was the authentication mechanism redesigned?",
        repo_slug="seL4__seL4"
    )
    info(f"Run ID:    {bb.run_id}")
    info(f"Question:  {bb.question}")
    info(f"Repo:      {bb.repo_slug}")
    info(f"Status:    {bb.get_status().value}")
    info(f"Evidence:  {bb.evidence_count()} pieces")
    result("Blackboard created!")

    step(2, "History/RAG Agent posts evidence")
    fragments = [
        EvidenceFragment(
            id="commit:a1b2c3d4e5",
            source_agent="history_rag",
            evidence_type="commit",
            title="Redesign auth to use capability-based security",
            content="Commit a1b2c3d4e5 by john on 2024-03-15\n\n"
                    "Replaced password-based auth with capability tokens.\n"
                    "Files changed: auth.c, security.h, tokens.c",
            confidence=0.92,
            url="https://github.com/seL4/seL4/commit/a1b2c3d4e5",
            date="2024-03-15T10:30:00Z",
            files=["auth.c", "security.h", "tokens.c"],
        ),
        EvidenceFragment(
            id="issue:142",
            source_agent="history_rag",
            evidence_type="issue",
            title="Security audit: password auth is vulnerable to replay attacks",
            content="Issue #142: Security audit found that the current password-based\n"
                    "authentication is vulnerable to replay attacks. Recommend switching\n"
                    "to capability-based tokens.",
            confidence=0.87,
            url="https://github.com/seL4/seL4/issues/142",
            date="2024-02-20T08:00:00Z",
        ),
        EvidenceFragment(
            id="pr:89",
            source_agent="history_rag",
            evidence_type="pr",
            title="PR #89: Implement capability-based auth (fixes #142)",
            content="Pull Request #89: Implement capability-based authentication\n"
                    "This PR replaces the vulnerable password auth with capability tokens.\n"
                    "Reviewed by: alice, bob. Merged on 2024-03-16.",
            confidence=0.85,
            url="https://github.com/seL4/seL4/pull/89",
            date="2024-03-16T14:00:00Z",
        ),
    ]
    added = bb.post_evidence("history_rag", fragments)
    info(f"Posted {added} evidence fragments")
    for f in fragments:
        info(f"  [{f.id}] ({f.evidence_type}) conf={f.confidence:.2f} — {f.title[:50]}")
    result(f"Blackboard now has {bb.evidence_count()} evidence pieces!")

    step(3, "Checking Blackboard state")
    info(f"Status:         {bb.get_status().value}")
    info(f"Total evidence: {bb.evidence_count()}")
    info(f"Commits:        {len(bb.get_evidence(filter_type='commit'))}")
    info(f"Issues:         {len(bb.get_evidence(filter_type='issue'))}")
    info(f"PRs:            {len(bb.get_evidence(filter_type='pr'))}")
    result("Blackboard is working!")

    step(4, "Logging agent actions (Observability)")
    bb.log("history_rag", "search_commits", "Searched for 'auth redesign'", duration_ms=150.3)
    bb.log("history_rag", "search_issues", "Searched for 'security vulnerability'", duration_ms=89.7)
    for log in bb.get_logs():
        info(f"  [{log.agent}] {log.action} — {log.detail[:40]} ({log.duration_ms:.0f}ms)")
    result("All actions logged!")

    step(5, "Serializing to dict (for API response)")
    d = bb.to_dict()
    info(f"Keys: {list(d.keys())}")
    info(f"Evidence count: {d['evidence_count']}")
    info(f"Logs count: {len(d['logs'])}")
    result("Ready to send as API response!")

    return bb


# ================================================================== #
#  Demo 2: Evidence Verification Agent (CoT)                         #
# ================================================================== #

def demo_verification(bb):
    banner("DEMO 2: EVIDENCE VERIFICATION AGENT — Chain-of-Thought")
    print("""
  This agent checks if the collected evidence is SUFFICIENT
  to answer the question. It reasons step-by-step (CoT).
    """)

    step(1, "Creating Verification Agent")
    agent = EvidenceVerificationAgent()
    info(f"Agent name: {agent.agent_name}")
    result("Agent ready!")

    step(2, "Running CoT verification on Blackboard evidence")
    info(f"Question: {bb.question}")
    info(f"Evidence pieces: {bb.evidence_count()}")
    info("Running Chain-of-Thought reasoning...")
    print()

    verdict = agent.run(bb)

    step(3, "Verification Result")
    sufficient = "✅ SUFFICIENT" if verdict["sufficient"] else "❌ INSUFFICIENT"
    info(f"Verdict:    {sufficient}")
    info(f"Confidence: {verdict['confidence']:.2f}")
    info(f"Reasoning:  {verdict['reasoning'][:100]}")
    if verdict.get("covered_aspects"):
        info(f"Covered:    {', '.join(verdict['covered_aspects'])}")
    if verdict.get("gaps"):
        info(f"Gaps:       {', '.join(verdict['gaps'][:2])}")
    if verdict.get("retry_queries"):
        info(f"Retry with: {', '.join(verdict['retry_queries'][:2])}")
    result("Verification complete!")

    step(4, "Blackboard updated with verdict")
    info(f"Status: {bb.get_status().value}")
    info(f"Verification stored: {bb.verification_result is not None}")
    result("Verification saved to Blackboard!")


# ================================================================== #
#  Demo 3: Message Bus (Broadcast Protocol)                          #
# ================================================================== #

def demo_message_bus():
    banner("DEMO 3: MESSAGE BUS — Broadcast Protocol")
    print("""
  In the Broadcast protocol, agents publish messages to a bus.
  All subscribed agents receive every message.
    """)

    step(1, "Creating Message Bus")
    bus = MessageBus()
    result("Bus created!")

    step(2, "Subscribing agents to topics")
    bus.subscribe("history_rag", "retry")
    bus.subscribe("evidence_verification", "evidence")
    bus.subscribe("pipeline", "verdict")
    info("history_rag         → listens on 'retry'")
    info("evidence_verification → listens on 'evidence'")
    info("pipeline            → listens on 'verdict'")
    result("Subscriptions set!")

    step(3, "Agents publishing messages")
    bus.publish("history_rag", "evidence", {"count": 5, "types": ["commit", "issue", "pr"]})
    info("history_rag published: evidence (5 pieces found)")

    bus.publish("evidence_verification", "verdict", {"sufficient": True, "confidence": 0.85})
    info("evidence_verification published: verdict (sufficient!)")

    bus.publish("pipeline", "answer", {"answer": "Auth was redesigned due to security audit..."})
    info("pipeline published: final answer")
    result(f"Total messages on bus: {len(bus.get_messages())}")

    step(4, "Reading messages by topic")
    for topic in ["evidence", "verdict", "answer"]:
        msg = bus.get_latest(topic)
        if msg:
            info(f"  [{topic}] from {msg.sender}: {str(msg.payload)[:60]}")
    result("Broadcast protocol working!")


# ================================================================== #
#  Demo 4: Full Multi-Agent Pipeline                                 #
# ================================================================== #

def demo_full_pipeline():
    banner("DEMO 4: FULL MULTI-AGENT PIPELINE")
    print("""
  This runs the complete pipeline:
    1. Create Blackboard
    2. History/RAG Agent (ReAct loop) → search & collect evidence
    3. Evidence Verification Agent (CoT) → check sufficiency
    4. If insufficient → retry with refined queries
    5. Generate final answer with citations
    """)

    # Check if any repo is indexed
    from rag.retriever import list_indexed
    repos = list_indexed()
    if not repos:
        warn("No indexed repos found! Skipping live pipeline demo.")
        info("To run this demo with real data:")
        info("  1. cd Deveops/backend")
        info("  2. python -m ingestion.run_ingestion --repo all")
        info("  3. Then run this demo again")
        return

    slug = repos[0]["slug"]
    repo_name = repos[0].get("github", slug)

    step(1, f"Running multi-agent pipeline on: {repo_name}")
    info(f"Question: 'Why was this project created?'")
    print()

    t0 = time.time()
    result_data = run_multi_agent(slug, "Why was this project created?")
    elapsed = time.time() - t0

    step(2, "Pipeline Result")
    info(f"Condition:  {result_data['condition_label']}")
    info(f"Evidence:   {len(result_data['evidence'])} pieces")
    info(f"Retries:    {result_data['multi_agent']['retry_count']}")
    info(f"Time:       {elapsed:.1f}s")
    print()
    info("Answer:")
    for line in result_data["answer"].split("\n"):
        info(f"  {line}")
    print()
    result("Multi-agent pipeline complete!")

    step(3, "Agent Logs (Observability)")
    for log in result_data["multi_agent"]["agent_logs"][:10]:
        info(f"  [{log['agent']}] {log['action']} — {log['detail'][:50]} ({log['duration_ms']:.0f}ms)")
    result(f"Total log entries: {len(result_data['multi_agent']['agent_logs'])}")


# ================================================================== #
#  Demo 5: ReAct Loop Explained                                     #
# ================================================================== #

def demo_react_explained():
    banner("DEMO 5: ReAct LOOP — How History/RAG Agent Thinks")
    print("""
  The ReAct pattern: Reason → Act → Observe → Repeat

  Here's what happens inside the History/RAG Agent:
    """)

    print("""
  ┌──────────────────────────────────────────────────────────┐
  │  Iteration 1:                                            │
  │  ┌─ Thought: "The question asks WHY auth was redesigned. │
  │  │           I should search commit history first."       │
  │  │                                                        │
  │  ├─ Action:  search_commits("auth redesign security")     │
  │  │                                                        │
  │  └─ Observe: Found 3 commits:                             │
  │      [commit:a1b2c3] Redesign auth module                 │
  │      [commit:d4e5f6] Fix auth vulnerability               │
  │      [commit:g7h8i9] Add capability tokens                │
  └──────────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌──────────────────────────────────────────────────────────┐
  │  Iteration 2:                                            │
  │  ┌─ Thought: "I found commits about the change, but WHY  │
  │  │           was it needed? Let me check issues."         │
  │  │                                                        │
  │  ├─ Action:  search_issues("security vulnerability auth") │
  │  │                                                        │
  │  └─ Observe: Found 2 issues:                              │
  │      [issue:142] Security audit: replay attack vuln       │
  │      [issue:138] Auth tokens expire too quickly           │
  └──────────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌──────────────────────────────────────────────────────────┐
  │  Iteration 3:                                            │
  │  ┌─ Thought: "Issue #142 explains WHY. Let me find the   │
  │  │           PR that fixed it for the full picture."     │
  │  │                                                        │
  │  ├─ Action:  get_linked("issue:142")                      │
  │  │                                                        │
  │  └─ Observe: Found linked evidence:                       │
  │      [pr:89] Implement capability-based auth (fixes #142) │
  │      [review:201] Review approving the new design         │
  └──────────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌──────────────────────────────────────────────────────────┐
  │  Iteration 4:                                            │
  │  ┌─ Thought: "I have commit + issue + PR + review.       │
  │  │           This is enough evidence. Finishing."         │
  │  │                                                        │
  │  └─ Action:  finish("Found security audit issue #142     │
  │              led to PR #89 redesigning auth system")      │
  └──────────────────────────────────────────────────────────┘
                          │
                          ▼
              ┌──────────────────────┐
              │  POST to Blackboard  │
              │  6 evidence pieces   │
              └──────────────────────┘
    """)
    result("This is how ReAct works inside the History/RAG Agent!")


# ================================================================== #
#  Demo 6: CoT Explained                                             #
# ================================================================== #

def demo_cot_explained():
    banner("DEMO 6: Chain-of-Thought — How Verification Agent Thinks")
    print("""
  The CoT pattern: Step-by-step reasoning about evidence sufficiency

  Here's what happens inside the Evidence Verification Agent:
    """)

    print("""
  ┌──────────────────────────────────────────────────────────┐
  │  Question: "Why was authentication redesigned?"          │
  │  Evidence: 6 pieces (commits, issues, PRs, reviews)      │
  ├──────────────────────────────────────────────────────────┤
  │                                                          │
  │  Step 1: Check WHAT changed                              │
  │    → Commit a1b2c3 shows auth.c, security.h changed     │
  │    → PR #89 lists all modified files                     │
  │    → WHAT is covered ✅                                  │
  │                                                          │
  │  Step 2: Check WHY it changed                            │
  │    → Issue #142 mentions security audit                  │
  │    → Found replay attack vulnerability                   │
  │    → WHY is covered ✅                                   │
  │                                                          │
  │  Step 3: Check WHO/WHEN                                  │
  │    → Commit by john on 2024-03-15                        │
  │    → PR reviewed by alice, bob                           │
  │    → WHO/WHEN is covered ✅                              │
  │                                                          │
  │  Step 4: Check CONTEXT                                   │
  │    → Review #201 discusses design alternatives           │
  │    → CONTEXT is covered ✅                               │
  │                                                          │
  │  Step 5: Check COMPLETENESS                              │
  │    → Have commit + issue + PR + review                   │
  │    → Multiple evidence types ✅                          │
  │    → No obvious gaps                                     │
  │                                                          │
  ├──────────────────────────────────────────────────────────┤
  │  VERDICT: ✅ SUFFICIENT (confidence: 0.92)               │
  │  Covered: what, why, who_when, context                   │
  │  Gaps: none                                              │
  │  Retry queries: [] (not needed)                          │
  └──────────────────────────────────────────────────────────┘
    """)
    result("This is how CoT works inside the Verification Agent!")


# ================================================================== #
#  Main                                                               #
# ================================================================== #

def main():
    banner("AGENTIC AI — Multi-Agent System Demo", "═")
    print("""
  Parul Gupta — 230506
  Repository-Aware Multi-Agent AI for Software History

  This demo shows how your 2 agents + Blackboard work together.
  Using MOCK LLM — no API keys needed!
    """)

    # Demo 1: Blackboard
    bb = demo_blackboard()
    pause()

    # Demo 2: Verification Agent
    demo_verification(bb)
    pause()

    # Demo 3: Message Bus
    demo_message_bus()
    pause()

    # Demo 5: ReAct Explained
    demo_react_explained()
    pause()

    # Demo 6: CoT Explained
    demo_cot_explained()
    pause()

    # Demo 4: Full Pipeline (needs indexed data)
    demo_full_pipeline()

    banner("DEMO COMPLETE!", "═")
    print("""
  All agents are working! Next steps:

  1. Run ingestion on 5 repos:
     cd Deveops/backend
     python -m ingestion.run_ingestion --repo all

  2. Test with real LLM (Gemini/Ollama):
     Set GEMINI_API_KEY in .env
     Set LLM_PROVIDER=gemini

  3. Run evaluation:
     python run_eval_multi_agent.py
    """)


if __name__ == "__main__":
    main()
