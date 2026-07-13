"""Reasoning-provider abstraction. The scoring/thesis code depends on this, not
on any specific API — so DeepSeek can be swapped for another provider or a local
stub. Every provider returns parsed JSON (structured, evidence-linked output).
"""
from __future__ import annotations

from . import deepseek, local_stub

PROVIDERS = {"deepseek": deepseek, "local_stub": local_stub}


def get_provider(name: str = "deepseek"):
    """Return the reasoning provider; fall back to the local stub if the real
    provider has no key configured (so the pipeline never hard-fails)."""
    mod = PROVIDERS.get(name, deepseek)
    if name == "deepseek" and not deepseek.available():
        return local_stub
    return mod
