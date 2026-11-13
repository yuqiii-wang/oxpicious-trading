"""test.dummy_langgraph.provider — the SHIM after the 2026-11-12
split: the request→response ENGINE (matching provider, Gherkin parser,
``estimate_usage``) lives in the parallel package
:mod:`test.dummy_llm` ("on matched what request, return what
response"); THIS package keeps only the kg/jev cucumber assertion
datasets (``02_decisions.feature`` / ``03_tools.feature``) for the
langgraph tests.

``FEATURES_DIR`` points at THIS package's ``features/`` — the decision
book (``jev.py``) and the tool seam (``mcp.py``) load the kg/jev
scenarios from here; the chat double defaults to ``test.dummy_llm``'s
dataset, exactly where ``01_summary_chain.feature`` now lives.
"""
from pathlib import Path

from test.dummy_llm import (
    DummyLanggraphProvider, DummyLlmProvider, ReplyNotFound,
    estimate_usage,
)

# This package's features directory (the kg/jev assertion datasets).
FEATURES_DIR = Path(__file__).resolve().parent / "features"

__all__ = [
    "FEATURES_DIR",
    "DummyLanggraphProvider", "DummyLlmProvider", "ReplyNotFound",
    "estimate_usage",
]
