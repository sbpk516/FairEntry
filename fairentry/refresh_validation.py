"""Fail-closed validation for a freshly built live FairEntry board."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .emerging import POLICY_VERSION


def _utc(value: str, label: str) -> datetime:
    if not value:
        raise ValueError(f"{label} is missing")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_live_refresh(board_path: str | Path, db_path: str | Path,
                          *, max_age_minutes: float = 120,
                          now: datetime | None = None) -> dict:
    """Verify that the exported board came from the latest two universes."""
    board_path, db_path = Path(board_path), Path(db_path)
    board = json.loads(board_path.read_text(encoding="utf-8"))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    max_age_seconds = float(max_age_minutes) * 60
    meta = board.get("meta") or {}
    emerging_meta = meta.get("emerging_candidates") or {}

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT source,refreshed_at,member_count FROM universe_refresh "
            "WHERE source IN ('finviz','finviz_discovery')"
        ).fetchall()
        sources = {row["source"]: dict(row) for row in rows}
        missing_official = con.execute(
            "SELECT count(*) FROM universe_membership o "
            "WHERE o.source='finviz' AND NOT EXISTS ("
            "SELECT 1 FROM universe_membership d "
            "WHERE d.source='finviz_discovery' AND d.ticker=o.ticker)"
        ).fetchone()[0]
        discovery_members = {row[0] for row in con.execute(
            "SELECT ticker FROM universe_membership WHERE source='finviz_discovery'")}
    finally:
        con.close()

    errors = []
    ages = {}
    for source in ("finviz", "finviz_discovery"):
        row = sources.get(source)
        if not row:
            errors.append(f"{source} universe refresh is missing")
            continue
        if int(row["member_count"] or 0) <= 0:
            errors.append(f"{source} universe is empty")
        age = (now - _utc(row["refreshed_at"], f"{source} refreshed_at")).total_seconds()
        ages[source] = round(age / 60, 1)
        if age < -300 or age > max_age_seconds:
            errors.append(
                f"{source} universe is not fresh ({ages[source]} minutes old; "
                f"maximum {max_age_minutes:g})")

    official = sources.get("finviz") or {}
    discovery = sources.get("finviz_discovery") or {}
    if official and discovery:
        if int(discovery["member_count"]) < int(official["member_count"]):
            errors.append("discovery universe is smaller than the official universe")
        if missing_official:
            errors.append(f"discovery universe omits {missing_official} official member(s)")
        if meta.get("universe_refreshed_at") != official.get("refreshed_at"):
            errors.append("board official-universe timestamp does not match the store")
        if emerging_meta.get("discovery_universe_refreshed_at") != discovery.get("refreshed_at"):
            errors.append("board discovery-universe timestamp does not match the store")

    generated_age = (now - _utc(meta.get("generated_at"), "board generated_at")).total_seconds()
    if generated_age < -300 or generated_age > max_age_seconds:
        errors.append("exported board is not fresh")
    if meta.get("universe_source") != "finviz":
        errors.append("exported board is not based on the official Finviz universe")
    if not int(meta.get("count") or 0):
        errors.append("exported official board is empty")
    if emerging_meta.get("policy_version") != POLICY_VERSION:
        errors.append("Emerging policy version is missing or unexpected")
    if emerging_meta.get("not_validated") is not True:
        errors.append("Emerging research is not explicitly marked not validated")
    if emerging_meta.get("official_score_effect") != 0:
        errors.append("Emerging research has a non-zero official score effect")

    candidates = board.get("emerging_candidates") or []
    for stock in candidates:
        ticker = stock.get("ticker")
        evidence = stock.get("emerging_candidate") or {}
        if ticker not in discovery_members:
            errors.append(f"Emerging candidate {ticker} is absent from discovery membership")
        if evidence.get("not_validated") is not True:
            errors.append(f"Emerging candidate {ticker} lacks its not-validated label")
        if not evidence.get("matched_variants"):
            errors.append(f"Emerging candidate {ticker} has no matched research variant")

    if errors:
        raise ValueError("Live refresh validation failed: " + "; ".join(errors))
    return {
        "ok": True,
        "board_generated_at": meta.get("generated_at"),
        "official": {"members": int(official["member_count"]),
                     "age_minutes": ages.get("finviz")},
        "discovery": {"members": int(discovery["member_count"]),
                      "age_minutes": ages.get("finviz_discovery")},
        "emerging_candidates": len(candidates),
        "policy_version": emerging_meta.get("policy_version"),
    }
