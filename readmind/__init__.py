"""ReadMind — repository-history multi-agent system built on Google ADK + CrewAI.

The two frameworks are the ONLY agentic frameworks used here:

    Google ADK   planner, history agent, verifier, single-agent ReAct, workflow agents
                 (SequentialAgent / ParallelAgent / LoopAgent), A2A server + RemoteA2aAgent
    CrewAI       code-analysis agent, issue/PR discussion agent, reviewer (Pydantic output),
                 hierarchical manager crew, A2A client to the ADK history agent

Tools reach the agents through two MCP servers (``readmind.mcp_servers``); agents of the
two frameworks talk to each other over the A2A protocol (``readmind.a2a_bridge``).

Entry point::

    from readmind.system import ReadMind
    rm = ReadMind.start()
    result = rm.ask("Why was PAE support removed from x86 32-bit?", pattern="sequential")
    print(result.final.to_markdown())
    rm.shutdown()
"""

__version__ = "1.0.0"
