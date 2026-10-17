"""Repository History / RAG Agent — ReAct loop over repository history.

This is the mandatory RAG-enabled component of the multi-agent system.
It uses a ReAct-style Reason → Act → Observe loop (Yao et al., 2023)
to iteratively search the repository's commit history, diffs, issues,
pull requests, and reviews until sufficient evidence is collected.

The agent wraps the existing hybrid retriever (BM25 + vector + RRF +
graph expansion + reranker) and exposes it as a set of *tools* that the
LLM can call autonomously.

References:
    [1] P. Lewis et al., "Retrieval-Augmented Generation," NeurIPS, 2020.
    [2] S. Yao et al., "ReAct: Synergizing Reasoning and Acting," ICLR, 2023.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from agents.blackboard import Blackboard, BlackboardStatus, EvidenceFragment
from agents._llm_retry import generate_with_retry as generate
from rag.llm import parse_json
from rag.retriever import get_index, RepoIndex

# ------------------------------------------------------------------ #
#  Constants                                                          #
# ------------------------------------------------------------------ #

MAX_REACT_ITERATIONS = 5          # hard cap to prevent infinite loops
MAX_EVIDENCE_PER_SEARCH = 6       # results per tool call
MAX_TOTAL_EVIDENCE = 15           # stop gathering after this many unique pieces

HISTORY_TYPES = {"commit", "issue", "pr", "review"}
ALL_TYPES = {"commit", "issue", "pr", "review", "code", "doc"}

# ------------------------------------------------------------------ #
#  Tool definitions (what the LLM can call)                           #
# ------------------------------------------------------------------ #

TOOL_DESCRIPTIONS = """You have access to the following tools to search a software repository's history:

1. search_commits(query: str) → Search commit messages and diffs for relevant commits.
2. search_issues(query: str) → Search issue titles, bodies, and comments.
3. search_prs(query: str) → Search pull request titles, bodies, discussions, and reviews.
4. search_all_history(query: str) → Search across all history types (commits + issues + PRs + reviews) at once.
5. get_linked(evidence_id: str) → Given an evidence ID (e.g. "commit:1a2b3c4d5e", "issue:42"), find related evidence through the history graph.
6. finish(summary: str) → Call this when you have gathered enough evidence. Provide a brief summary of what you found.

RULES:
- Call exactly ONE tool per step.
- Always output Thought first, then Action.
- After each Observation, reason about whether you have enough evidence or need more.
- You MUST call finish() when done. Do not keep searching after you have sufficient evidence.
- Maximum {max_iter} iterations allowed.
"""

REACT_SYSTEM_PROMPT = """You are the History/RAG Agent in a multi-agent system that explains software design decisions.
Your job is to retrieve historical evidence (commits, issues, PRs, reviews) from a repository to answer a developer's question.

You work in a ReAct loop: Thought → Action → Observation → Thought → ...

{tool_descriptions}

Output format (strictly follow this):
Thought: <your reasoning about what to search next or whether you have enough>
Action: <tool_name>(<argument>)
"""

REACT_CONTINUE_PROMPT = """Previous steps:
{history}

Based on the observations so far, continue your investigation.
Remember: call finish() when you have enough evidence to answer the question.
Question: {question}

Thought:"""

# ------------------------------------------------------------------ #
#  Tool execution (bridges to existing retriever)                     #
# ------------------------------------------------------------------ #

def _execute_tool(index: RepoIndex, tool_name: str, argument: str) -> list[dict]:
    """Run a tool against the existing RepoIndex and return results."""

    if tool_name == "search_commits":
        types = {"commit"}
    elif tool_name == "search_issues":
        types = {"issue"}
    elif tool_name == "search_prs":
        types = {"pr", "review"}
    elif tool_name == "search_all_history":
        types = HISTORY_TYPES
    elif tool_name == "get_linked":
        # Graph neighbour expansion
        neighbours = []
        for nb_id in index.neighbours(argument):
            if nb_id in index.docs:
                d = index.docs[nb_id]
                neighbours.append({
                    "id": nb_id,
                    "type": d["type"],
                    "title": d.get("title", ""),
                    "date": d.get("date", ""),
                    "url": d.get("url", ""),
                    "text": d["text"][:1200],
                    "score": 0.5,
                })
        return neighbours[:MAX_EVIDENCE_PER_SEARCH]
    elif tool_name == "finish":
        return []  # handled specially by the caller
    else:
        return []

    # For search tools, use the existing hybrid retriever
    evidence, _timings = index.retrieve(
        argument,
        types=types,
        k=MAX_EVIDENCE_PER_SEARCH,
        expand=True,
        rerank=False,  # skip reranker for speed in iterative loop
    )
    return evidence


def _format_observation(results: list[dict], tool_name: str) -> str:
    """Format tool results into a readable observation for the LLM."""
    if not results:
        return "No results found."
    lines = [f"Found {len(results)} results:\n"]
    for r in results:
        date_str = f" ({r.get('date', '')[:10]})" if r.get("date") else ""
        lines.append(f"  [{r['id']}]{date_str} {r.get('title', '')}")
        # Show a snippet of the content
        snippet = r.get("text", "")[:300].replace("\n", " ")
        lines.append(f"    {snippet}\n")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
#  ReAct action parser                                                #
# ------------------------------------------------------------------ #

_ACTION_RE = re.compile(
    r"""Action:\s*(\w+)\(\s*["']?(.*?)["']?\s*\)""",
    re.DOTALL,
)


def _parse_action(llm_output: str) -> tuple[str, str]:
    """Extract (tool_name, argument) from the LLM's output.

    Returns ("finish", "") if parsing fails or the LLM wants to stop.
    """
    m = _ACTION_RE.search(llm_output)
    if not m:
        # If the LLM didn't follow format, check if it's trying to finish
        if "finish" in llm_output.lower():
            return "finish", ""
        return "finish", ""
    return m.group(1).strip(), m.group(2).strip()


# ------------------------------------------------------------------ #
#  Main agent class                                                   #
# ------------------------------------------------------------------ #

class HistoryRAGAgent:
    """ReAct-based agent that iteratively retrieves repository history.

    Usage::

        agent = HistoryRAGAgent(repo_slug="seL4__seL4")
        agent.run(blackboard)
    """

    def __init__(self, repo_slug: str, max_iterations: int = MAX_REACT_ITERATIONS):
        self.repo_slug = repo_slug
        self.max_iterations = max_iterations
        self.index: RepoIndex = get_index(repo_slug)
        self.agent_name = "history_rag"

    def run(self, blackboard: Blackboard) -> list[EvidenceFragment]:
        """Execute the ReAct loop and post evidence to the Blackboard.

        Returns the list of EvidenceFragments collected.
        """
        question = blackboard.question
        # If this is a retry, use the refined queries from the verification agent
        if blackboard.retry_queries:
            question = f"{blackboard.question} (Focus on: {'; '.join(blackboard.retry_queries)})"

        blackboard.log(self.agent_name, "start",
                       f"Starting ReAct loop for: {question}")

        collected_evidence: dict[str, dict] = {}
        react_history: list[str] = []
        t_start = time.perf_counter()

        for iteration in range(1, self.max_iterations + 1):
            t_iter = time.perf_counter()

            # ── Build the prompt ──
            if iteration == 1:
                tool_desc = TOOL_DESCRIPTIONS.format(max_iter=self.max_iterations)
                system = REACT_SYSTEM_PROMPT.format(tool_descriptions=tool_desc)
                prompt = (
                    f"Question: {question}\n\n"
                    f"Repository: {self.index.stats.get('github', self.repo_slug)}\n\n"
                    f"Begin your investigation. Think about what kind of evidence "
                    f"would best answer this question, then call a tool.\n\n"
                    f"Thought:"
                )
            else:
                tool_desc = TOOL_DESCRIPTIONS.format(max_iter=self.max_iterations)
                system = REACT_SYSTEM_PROMPT.format(tool_descriptions=tool_desc)
                prompt = REACT_CONTINUE_PROMPT.format(
                    history="\n".join(react_history[-8:]),  # keep context manageable
                    question=question,
                )

            # ── Call the LLM ──
            llm_output = generate(system, prompt, temperature=0.0)
            thought_text = llm_output.strip()

            # ── Parse the action ──
            tool_name, argument = _parse_action(thought_text)

            # Log the thought + action
            blackboard.log(
                self.agent_name,
                f"react_iter_{iteration}",
                f"Thought+Action: {thought_text[:500]}",
                duration_ms=(time.perf_counter() - t_iter) * 1000,
            )

            # ── Handle finish ──
            if tool_name == "finish":
                react_history.append(f"Thought: {thought_text}\nAction: finish()\n")
                blackboard.log(self.agent_name, "finish",
                               f"Agent decided to finish after {iteration} iterations")
                break

            # ── Execute the tool ──
            t_tool = time.perf_counter()
            results = _execute_tool(self.index, tool_name, argument)
            tool_duration = (time.perf_counter() - t_tool) * 1000

            # Collect new evidence
            new_count = 0
            for r in results:
                if r["id"] not in collected_evidence:
                    collected_evidence[r["id"]] = r
                    new_count += 1

            # Format observation
            observation = _format_observation(results, tool_name)

            blackboard.log(
                self.agent_name,
                f"tool_{tool_name}",
                f"Query: '{argument}' → {len(results)} results ({new_count} new)",
                duration_ms=tool_duration,
            )

            # Add to history
            react_history.append(
                f"Thought: {thought_text}\n"
                f"Action: {tool_name}(\"{argument}\")\n"
                f"Observation: {observation[:800]}\n"
            )

            # ── Auto-stop if we have enough evidence ──
            if len(collected_evidence) >= MAX_TOTAL_EVIDENCE:
                blackboard.log(self.agent_name, "auto_finish",
                               f"Reached {MAX_TOTAL_EVIDENCE} evidence pieces, stopping")
                break

        # ── Convert to EvidenceFragments and post to Blackboard ──
        fragments = []
        for eid, ev in collected_evidence.items():
            # Normalise confidence score to [0, 1]
            raw_score = ev.get("score", 0.5)
            confidence = min(1.0, max(0.0, raw_score))

            frag = EvidenceFragment(
                id=eid,
                source_agent=self.agent_name,
                evidence_type=ev.get("type", "unknown"),
                title=ev.get("title", ""),
                content=ev.get("text", ""),
                confidence=confidence,
                url=ev.get("url", ""),
                date=ev.get("date", ""),
                files=ev.get("files", []) if isinstance(ev.get("files"), list) else [],
            )
            fragments.append(frag)

        posted = blackboard.post_evidence(self.agent_name, fragments)
        total_ms = (time.perf_counter() - t_start) * 1000

        blackboard.log(
            self.agent_name, "complete",
            f"Posted {posted} evidence fragments in {total_ms:.0f}ms "
            f"({len(react_history)} ReAct iterations)",
            duration_ms=total_ms,
        )

        return fragments
