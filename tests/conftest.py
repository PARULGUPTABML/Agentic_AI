"""Pytest fixtures and environment configuration for multi-agent test suite."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

for mod in [
    "rag", "rag.llm", "rag.retriever", "rag.citations", "rag.pipeline", "config"
]:
    if mod not in sys.modules:
        m = MagicMock()
        sys.modules[mod] = m

sys.modules["rag.llm"].generate.return_value = "Thought: Finished" + chr(10) + "Action: finish"
sys.modules["rag.citations"].verify_citations.return_value = {"valid": 0, "invalid": 0}
sys.modules["rag.retriever"].get_index.return_value = MagicMock()
