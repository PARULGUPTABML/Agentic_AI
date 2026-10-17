"""Pytest fixtures and environment configuration for parul_aggarwal test suite."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Stub missing external rag module so tests can mock or run agents._llm_retry without crash
if 'rag' not in sys.modules:
    rag_mock = MagicMock()
    sys.modules['rag'] = rag_mock
    sys.modules['rag.llm'] = rag_mock.llm
