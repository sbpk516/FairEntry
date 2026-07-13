"""Deterministic offline stub for the reasoning provider — used when no key is
configured or in tests. Returns a neutral, clearly-labelled thesis so the
pipeline runs without network.
"""
from __future__ import annotations

NAME = "local_stub"
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def available() -> bool:
    return True


def complete_json(system: str, user: str, **kw) -> dict:
    USAGE["calls"] += 1
    return {"_stub": True,
            "recovery_score": 50, "thesis_confidence": "low",
            "summary": "Reasoning provider unavailable — deterministic score only.",
            "situation": [], "kill_switch": "", "temporary_vs_structural": "unknown"}
