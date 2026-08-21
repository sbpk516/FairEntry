import json

import pytest

from fairentry.backtest.failure_research import apply_verified_findings, build_research_queue
from scripts.backtest import _write_json


def _artifact(*episodes):
    return {"buy_return_achievement": {"episode_details": list(episodes)}}


def _episode(ticker, started="2024-01-01", result="failure"):
    return {
        "ticker": ticker,
        "company": f"{ticker} Inc",
        "started": started,
        "last_buy": started,
        "highest_gain_within_one_year_pct": 12.5,
        "fixed_30_evaluation": {"result": result},
    }


def test_queue_contains_only_unresearched_failures_and_preserves_state(tmp_path):
    research = tmp_path / "research.json"
    queue = tmp_path / "queue.json"
    research.write_text(json.dumps({"entries": {"OLD": {"reason": "saved"}}}), encoding="utf-8")
    queue.write_text(json.dumps({"items": [{
        "episode_key": "NEW:2024-01-01", "status": "research_in_progress",
        "attempts": 2, "last_attempted_at": "2026-01-01T00:00:00Z",
    }]}), encoding="utf-8")

    result = build_research_queue(
        _artifact(_episode("OLD"), _episode("NEW"), _episode("WIN", result="success")),
        research_path=research,
        queue_path=queue,
    )

    assert result["summary"] == {
        "failed_episodes": 2,
        "covered_by_existing_research": 1,
        "pending_research": 1,
    }
    assert result["items"][0]["status"] == "research_in_progress"
    assert result["items"][0]["attempts"] == 2


def test_verified_findings_append_without_overwriting_existing_research(tmp_path):
    research = tmp_path / "research.json"
    findings = tmp_path / "findings.json"
    research.write_text(json.dumps({"version": 1, "entries": {"OLD": {"reason": "keep me"}}}), encoding="utf-8")
    findings.write_text(json.dumps({"findings": [
        {"ticker": "OLD", "code": "external_event", "category": "risk", "reason": "replace", "sources": ["https://example.com/old"]},
        {"ticker": "NEW", "code": "growth_below_expectations", "category": "growth", "reason": "Verified concise reason.", "sources": ["https://www.sec.gov/new"]},
    ]}), encoding="utf-8")

    assert apply_verified_findings(findings, research_path=research) == ["NEW"]
    saved = json.loads(research.read_text(encoding="utf-8"))["entries"]
    assert saved["OLD"]["reason"] == "keep me"
    assert saved["NEW"]["reason"] == "Verified concise reason."


def test_verified_findings_require_https_source(tmp_path):
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"findings": [{
        "ticker": "NEW", "code": "external_event", "category": "risk",
        "reason": "Reason", "sources": ["http://example.com/not-secure"],
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="HTTPS"):
        apply_verified_findings(findings, research_path=tmp_path / "research.json")


def test_backtest_json_build_always_publishes_research_queue(tmp_path):
    output = tmp_path / "data" / "backtest.json"
    _write_json(_artifact(_episode("BRANDNEW")), output)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    queue = json.loads((output.parent / "target-failure-research-queue.json").read_text(encoding="utf-8"))
    assert artifact["failure_research_queue"]["pending_research"] == 1
    assert queue["items"][0]["ticker"] == "BRANDNEW"
