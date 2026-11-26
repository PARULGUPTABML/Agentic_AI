"""Messaging architectures for context sharing between agents (requirement 1).

All three implement the same interface, so a pattern can run unchanged on each:

    publish(sender, report, to=None)   an agent shares its SpecialistReport
    context_for(receiver) -> str       what that agent gets to read before it works
    as_state() -> dict                 the same context as ADK session.state entries
    stats() -> dict                    messages, deliveries, bytes delivered, max context size

Blackboard (mandatory baseline)
    One shared store (the team's ``agents.blackboard.Blackboard``). Every agent writes its evidence
    to it; every agent reads the whole, de-duplicated board. Mirrored into ADK ``session.state``
    under ``shared_context`` / ``blackboard`` so ADK agents read it through instruction templating.
P2P (chain hand-off)
    An agent's report is sent only to the next agent, which forwards a merged digest (relay).
    Forwarded evidence is capped at ``RELAY_CAP`` items — models context-window limits of relay
    chains, and makes information loss measurable.
Broadcast (publish/subscribe)
    Every report is copied into the inbox of every subscriber. Agents see all messages as they were
    sent (no de-duplication): highest delivered bytes, lowest coordination.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod

from agents.blackboard import Blackboard, EvidenceFragment
from readmind.observability.callbacks import record_message
from readmind.schemas import EvidenceItem, SpecialistReport

RELAY_CAP = 8


def report_text(r: SpecialistReport, max_ev: int = 10) -> str:
    ev = [e.model_dump(include={"id", "type", "title", "snippet", "date", "source"}) for e in r.evidence[:max_ev]]
    for e in ev:
        e["snippet"] = (e.get("snippet") or "")[:280]
    lines = [f"### {r.agent} report", f"Summary: {r.summary}"]
    if r.findings:
        lines.append("Findings:")
        lines += [f"- ({c.kind}) {c.text} {' '.join('[' + i + ']' for i in c.evidence_ids)}" for c in r.findings[:8]]
    if r.gaps:
        lines.append("Gaps: " + "; ".join(r.gaps))
    lines.append("Evidence JSON: " + json.dumps(ev, ensure_ascii=False))
    return "\n".join(lines)


class Channel(ABC):
    name = "abstract"

    def __init__(self, question: str, repo: str, participants: list[str]):
        self.question = question
        self.repo = repo
        self.participants = list(participants)
        self.reports: list[SpecialistReport] = []
        self.n_messages = 0
        self.n_deliveries = 0
        self.bytes_delivered = 0
        self.max_context = 0

    @abstractmethod
    def publish(self, sender: str, report: SpecialistReport, to: list[str] | None = None) -> None: ...

    @abstractmethod
    def context_for(self, receiver: str) -> str: ...

    def all_evidence(self) -> list[EvidenceItem]:
        seen: dict[str, EvidenceItem] = {}
        for r in self.reports:
            for e in r.evidence:
                seen.setdefault(e.id, e)
        return list(seen.values())

    def as_state(self, receiver: str = "verifier") -> dict:
        return {"shared_context": self.context_for(receiver), "messaging": self.name}

    def _track(self, receiver: str, text: str) -> str:
        self.max_context = max(self.max_context, len(text))
        return text

    def stats(self) -> dict:
        return {"channel": self.name, "messages": self.n_messages, "deliveries": self.n_deliveries,
                "bytes_delivered": self.bytes_delivered, "max_context_chars": self.max_context,
                "evidence_items": len(self.all_evidence())}


class BlackboardChannel(Channel):
    name = "blackboard"

    def __init__(self, question: str, repo: str, participants: list[str]):
        super().__init__(question, repo, participants)
        self.board = Blackboard(question, repo)

    def publish(self, sender, report, to=None):
        self.reports.append(report)
        frags = [EvidenceFragment(id=e.id, source_agent=sender, evidence_type=e.type or e.id.split(":", 1)[0],
                                  title=e.title, content=e.snippet, confidence=0.8, url=e.url, date=e.date,
                                  metadata={"source": e.source}) for e in report.evidence]
        self.board.post_evidence(sender, frags)
        self.board.log(sender, "post", report.summary[:200])
        payload = report_text(report)
        self.n_messages += 1
        self.n_deliveries += 1                      # one write to the shared store
        self.bytes_delivered += len(payload)
        record_message("system", sender, ["blackboard"], payload, self.name)

    def context_for(self, receiver):
        frags = sorted(self.board.get_evidence(), key=lambda f: -f.confidence)
        ev = [{"id": f.id, "type": f.evidence_type, "title": f.title, "snippet": (f.content or "")[:280],
               "date": f.date, "source": f.metadata.get("source", f.source_agent)} for f in frags]
        summaries = [f"- {r.agent}: {r.summary}" for r in self.reports]
        gaps = [g for r in self.reports for g in r.gaps]
        text = (f"BLACKBOARD ({len(ev)} evidence items from {', '.join(dict.fromkeys(r.agent for r in self.reports))})\n"
                + "\n".join(summaries) + ("\nGaps: " + "; ".join(gaps) if gaps else "")
                + "\nEvidence JSON: " + json.dumps(ev, ensure_ascii=False)) if self.reports else "BLACKBOARD: empty"
        self.bytes_delivered += len(text)           # a read of the shared store
        return self._track(receiver, text)

    def as_state(self, receiver: str = "verifier") -> dict:
        return {"shared_context": self.context_for(receiver), "messaging": self.name,
                "blackboard": {"status": self.board.get_status().value, "evidence_ids": self.board.get_evidence_ids(),
                               "evidence_count": self.board.evidence_count()}}


class P2PChannel(Channel):
    name = "p2p"

    def __init__(self, question: str, repo: str, participants: list[str]):
        super().__init__(question, repo, participants)
        self.inbox: dict[str, list[str]] = {p: [] for p in participants}
        self.relay: list[EvidenceItem] = []

    def _next(self, sender: str) -> list[str]:
        if sender in self.participants:
            i = self.participants.index(sender)
            return self.participants[i + 1:i + 2]
        return self.participants[:1]

    def publish(self, sender, report, to=None):
        self.reports.append(report)
        to = to or self._next(sender)
        # relay = previous relay + this report's evidence, capped (models a bounded hand-off message)
        merged = {e.id: e for e in self.relay}
        for e in report.evidence:
            merged.setdefault(e.id, e)
        self.relay = list(merged.values())[-RELAY_CAP:]
        relay_report = SpecialistReport(agent=sender, summary=report.summary, findings=report.findings[:4],
                                        evidence=self.relay, gaps=report.gaps)
        payload = report_text(relay_report)
        for r in to:
            self.inbox.setdefault(r, []).append(payload)
            self.n_deliveries += 1
            self.bytes_delivered += len(payload)
        self.n_messages += 1
        record_message("system", sender, to, payload, self.name)

    def context_for(self, receiver):
        msgs = self.inbox.get(receiver, [])
        text = (f"DIRECT MESSAGE from your predecessor:\n{msgs[-1]}") if msgs else "No message received."
        return self._track(receiver, text)

    def all_evidence(self):
        return list(self.relay)


class BroadcastChannel(Channel):
    name = "broadcast"

    def __init__(self, question: str, repo: str, participants: list[str]):
        super().__init__(question, repo, participants)
        self.inbox: dict[str, list[str]] = {p: [] for p in participants}

    def publish(self, sender, report, to=None):
        self.reports.append(report)
        payload = report_text(report)
        subs = [p for p in self.inbox if p != sender]
        for r in subs:
            self.inbox[r].append(payload)
            self.n_deliveries += 1
            self.bytes_delivered += len(payload)
        self.n_messages += 1
        record_message("system", sender, subs, payload, self.name)

    def context_for(self, receiver):
        msgs = self.inbox.get(receiver, [])
        text = ("BROADCAST MESSAGES RECEIVED:\n" + "\n\n".join(msgs)) if msgs else "No messages received."
        return self._track(receiver, text)


CHANNELS = {"blackboard": BlackboardChannel, "p2p": P2PChannel, "broadcast": BroadcastChannel}


def make_channel(name: str, question: str, repo: str, participants: list[str]) -> Channel:
    if name not in CHANNELS:
        raise KeyError(f"unknown messaging architecture {name}; choose from {sorted(CHANNELS)}")
    return CHANNELS[name](question, repo, participants)
