import json

import pytest

from fairentry.backtest.failure_research import apply_verified_findings, build_research_queue
from scripts.backtest import _write_json


def _finding(ticker, **changes):
    row = {"ticker": ticker, "episode_key": f"{ticker}:2024-01-01",
           "researched_for_episode": f"{ticker}:2024-01-01",
           "episode_start": "2024-01-01", "episode_end": "2024-12-31",
           "code": "growth_below_expectations", "category": "growth",
           "reason": "Verified concise reason.", "sources": ["https://www.sec.gov/new"]}
    return {**row, **changes}


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
    research.write_text(json.dumps({"entries": {"OLD:2024-01-01": _finding("OLD")}}), encoding="utf-8")
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
    assert result["policy"]["pipeline"] == "retrospective_failure_diagnosis"
    assert result["policy"]["predictive_use_forbidden"] is True
    assert result["items"][0]["required_output"]["entry_date_warning_hypothesis"]


def test_verified_findings_append_without_overwriting_existing_research(tmp_path):
    research = tmp_path / "research.json"
    findings = tmp_path / "findings.json"
    research.write_text(json.dumps({"version": 1, "entries": {"OLD:2024-01-01": _finding("OLD", reason="keep me")}}), encoding="utf-8")
    findings.write_text(json.dumps({"findings": [
        _finding("OLD", reason="replace"),
        _finding("NEW"),
    ]}), encoding="utf-8")

    assert apply_verified_findings(findings, research_path=research) == ["NEW:2024-01-01"]
    saved = json.loads(research.read_text(encoding="utf-8"))["entries"]
    assert saved["OLD:2024-01-01"]["reason"] == "keep me"
    assert saved["NEW:2024-01-01"]["reason"] == "Verified concise reason."


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


def test_research_matching_requires_the_exact_episode_and_window():
    from fairentry.backtest.failure_research import find_episode_research
    row = _episode("OLD")
    assert find_episode_research(row, {"OLD": {"reason": "legacy"}}) is None
    assert find_episode_research(row, {"OLD:2024-01-01": _finding("OLD")})
    assert find_episode_research(_episode("OLD", started="2006-01-01"), {"OLD": _finding("OLD")}) is None
    assert find_episode_research(row, {"OLD:2024-01-01": _finding("OLD", episode_end="2025-01-01")}) is None
    assert find_episode_research(row, {"OLD:2024-01-01": _finding("OLD", code="invented")}) is None


def test_legacy_registry_does_not_mark_old_episode_researched(tmp_path):
    research = tmp_path / "research.json"
    research.write_text(json.dumps({"entries": {"LOW": {"reason": "2024 sales fell"}}}), encoding="utf-8")
    result = build_research_queue(_artifact(_episode("LOW", "2006-11-30")),
                                  research_path=research, queue_path=tmp_path / "queue.json")
    assert result["summary"]["pending_research"] == 1
    assert result["summary"]["covered_by_existing_research"] == 0
