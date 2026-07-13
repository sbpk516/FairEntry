"""Shared adapter helpers: safe secret loading + value coercion.

Secrets resolve in order: process env -> FairEntry/.env -> the user's
out-of-repo baghunter secrets file. Values are never printed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
_ENV = ROOT / ".env"
_BAGHUNTER_SECRETS = Path(
    r"C:/Users/sbpk5/Documents/Pill/P/99_SENSITIVE_PRIVATE/_LOCAL_SECRETS_DO_NOT_COMMIT.md")

_loaded = False


def _load_dotenv():
    global _loaded
    if _loaded:
        return
    _loaded = True
    if _ENV.exists():
        for line in _ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def get_key(name: str, required: bool = False) -> str | None:
    """Return a secret by name without ever printing it."""
    _load_dotenv()
    val = os.environ.get(name)
    if not val and _BAGHUNTER_SECRETS.exists():
        m = re.search(rf"{re.escape(name)}[\s:=`\"'*]+([A-Za-z0-9._\-]{{8,}})",
                      _BAGHUNTER_SECRETS.read_text(encoding="utf-8"))
        if m:
            val = m.group(1)
            os.environ[name] = val
    if required and not val:
        raise RuntimeError(f"{name} not set (env / .env / secrets file)")
    return val


def sf(v) -> float | None:
    """Safe float — strips %, commas, handles '-'/'' -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "").replace("$", "")
    if s in ("", "-", "—", "N/A", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None
