"""Append-only research queue for newly failed one-year Buy episodes.

The backtest stays deterministic.  It identifies research candidates, while a
separate research pass can add verified findings to the controlled registry.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESEARCH = ROOT / "config" / "target_failure_research.json"
DEFAULT_QUEUE = ROOT / "web" / "data" / "target-failure-research-queue.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _episode_key(row: dict) -> str:
    return f"{str(row.get('ticker') or '').upper()}:{row.get('started') or 'unknown'}"


def build_research_queue(
    backtest: dict,
    *,
    research_path: Path = DEFAULT_RESEARCH,
    queue_path: Path = DEFAULT_QUEUE,
) -> dict:
    """Write a stable queue containing only failures without saved research."""
    registry = (_read(Path(research_path), {}).get("entries") or {})
    prior = _read(Path(queue_path), {})
    prior_rows = {row.get("episode_key"): row for row in prior.get("items") or []}
    details = ((backtest.get("buy_return_achievement") or {}).get("episode_details") or [])
    items = []
    covered = 0
    failed = 0
    for row in details:
        if ((row.get("fixed_30_evaluation") or {}).get("result")) != "failure":
            continue
        failed += 1
        ticker = str(row.get("ticker") or "").upper()
        if ticker in registry:
            covered += 1
            continue
        key = _episode_key(row)
        old = prior_rows.get(key) or {}
        items.append({
            "episode_key": key,
            "ticker": ticker,
            "company": row.get("company"),
            "first_buy": row.get("started"),
            "last_buy": row.get("last_buy"),
            "highest_one_year_gain_pct": row.get("highest_gain_within_one_year_pct"),
            "status": old.get("status") or "pending_research",
            "attempts": int(old.get("attempts") or 0),
            "last_attempted_at": old.get("last_attempted_at"),
            "research_scope": "Use authoritative sources published during or soon after the one-year evaluation window; avoid hindsight.",
        })
    result = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "policy": {
            "append_only": True,
            "existing_research_overwritten": False,
            "score_impact": "none",
            "allowed_statuses": ["pending_research", "research_in_progress", "cause_not_verified"],
        },
        "summary": {
            "failed_episodes": failed,
            "covered_by_existing_research": covered,
            "pending_research": len(items),
        },
        "items": items,
    }
    queue_path = Path(queue_path)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def apply_verified_findings(
    findings_path: Path,
    *,
    research_path: Path = DEFAULT_RESEARCH,
) -> list[str]:
    """Append source-backed findings; refuse to replace an existing ticker."""
    research_path = Path(research_path)
    registry = _read(research_path, {"version": 1, "entries": {}})
    entries = registry.setdefault("entries", {})
    findings = _read(Path(findings_path), {})
    rows = findings.get("findings") if isinstance(findings, dict) else findings
    if not isinstance(rows, list):
        raise ValueError("findings file must contain a findings list")
    added = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        reason = str(row.get("reason") or "").strip()
        sources = row.get("sources") or []
        if not ticker or not reason or not row.get("code") or not row.get("category"):
            raise ValueError("each finding needs ticker, code, category, and reason")
        if ticker in entries:
            continue
        if len(reason) > 360:
            raise ValueError(f"{ticker}: reason must be a concise one-liner")
        if not sources or any(urlparse(str(url)).scheme != "https" for url in sources):
            raise ValueError(f"{ticker}: at least one HTTPS authoritative source is required")
        entries[ticker] = {
            "code": row["code"],
            "category": row["category"],
            "reason": reason,
            "sources": sources,
            "researched_for_episode": row.get("episode_key"),
            "researched_at": row.get("researched_at") or datetime.now(timezone.utc).date().isoformat(),
        }
        added.append(ticker)
    registry["researched_at"] = datetime.now(timezone.utc).date().isoformat()
    research_path.parent.mkdir(parents=True, exist_ok=True)
    research_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added
