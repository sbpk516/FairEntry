import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fairentry.adapters import finviz
from fairentry.emerging import evaluate_emerging_candidate
from fairentry.backtest.emerging_candidates import collapse_episodes, summarize
from fairentry.config import load_config
from fairentry.pipeline.emerging import classify_emerging_stock
from fairentry.refresh_validation import validate_live_refresh
from fairentry.store import Store


def _stock(**overrides):
    stock = {
        "ticker": "EDGE", "company": "Edge Co", "sector": "Technology",
        "strategy": ["growth"], "price": 10, "preliminary": 72,
        "verdict": "Buy", "model_verdict": "Buy", "vetoes": [],
        "categories": [
            {"id": "quality", "score": 75},
            {"id": "survival", "score": 78},
            {"id": "growth", "score": 70},
        ],
        "valuation": {"upside": 42, "method_agreement": {
            "status": "pass", "method_count": 2, "dispersion_pct": 30}},
        "entry_exit_evidence": {"entry_alignment": "constructive"},
    }
    stock.update(overrides)
    return stock


def test_finviz_discovery_floor_includes_liquidity_fringe(monkeypatch):
    cfg = load_config()
    row = {
        "Ticker": "EDGE", "Company": "Edge Co", "Sector": "Technology",
        "Industry": "Software - Application", "Country": "USA",
        "Price": "10", "Market Cap": "500", "Average Volume": "700",
    }
    monkeypatch.setattr(finviz, "_fetch_rows", lambda force=False: [row])
    official, _ = finviz.fetch(cfg, ["price", "avg_dollar_volume"])
    discovery, metrics = finviz.fetch(
        cfg, ["price", "avg_dollar_volume"], avg_dollar_volume_min=5_000_000)
    assert official == []
    assert [security["ticker"] for security in discovery] == ["EDGE"]
    assert metrics["EDGE"]["avg_dollar_volume"] == 7_000_000


def test_emerging_confirmation_is_explicit_and_zero_effect():
    cfg = load_config()
    result = classify_emerging_stock(
        _stock(), {"avg_dollar_volume": {"value": 7_000_000}},
        cfg.sectors["emerging_candidates"])
    assert result["status"] == "selective"
    assert result["label"] == "Selective Confirmation Review · not validated"
    assert result["matched_variants"] == ["broad", "balanced", "selective"]
    assert result["liquidity_band"] == "5m_to_10m"
    assert result["official_buy"] is False
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert all(check["passes"] for check in result["checks"])
    assert all(check["passes"] for check in result["confirmation_checks"])
    assert result["blockers"] == []


def test_emerging_rule_rejects_weak_growth():
    cfg = load_config()
    stock = _stock(categories=[
        {"id": "quality", "score": 75},
        {"id": "survival", "score": 78},
        {"id": "growth", "score": 40},
    ])
    assert classify_emerging_stock(
        stock, {"avg_dollar_volume": {"value": 7_000_000}},
        cfg.sectors["emerging_candidates"]) is None


def test_shared_rule_exposes_broad_balanced_and_selective_levels():
    cfg = load_config()
    policy = cfg.sectors["emerging_candidates"]
    broad = evaluate_emerging_candidate({
        "avg_dollar_volume": 8_000_000, "shadow_verdict": "Watch",
        "no_hard_veto": True, "business_quality": 55,
        "financial_strength": 65, "growth": 55,
        "valuation_upside_pct": 25, "valuation_method_count": 1,
        "valuation_agreement": "insufficient", "entry_alignment": "unavailable",
    }, policy)
    assert broad["matched_variants"] == ["broad"]
    balanced = evaluate_emerging_candidate({
        "avg_dollar_volume": 15_000_000, "shadow_verdict": "Watch",
        "no_hard_veto": True, "business_quality": 65,
        "financial_strength": 65, "growth": 55,
        "valuation_upside_pct": 35, "valuation_method_count": 2,
        "valuation_agreement": "caution", "entry_alignment": "mixed",
    }, policy)
    assert balanced["matched_variants"] == ["broad", "balanced"]
    assert balanced["liquidity_band"] == "10m_to_20m"


def test_backtest_collapses_repeated_signals_and_reports_targets():
    passing = {"variants": {"broad": {"passes": True}}}
    rows = [
        {"ticker": "EDGE", "issuer_key": "EDGE", "decision_date": "2020-01-02",
         "entry_date": "2020-01-03", "avg_dollar_volume": 7_000_000,
         "_emerging_evaluation": passing,
         "_tuning_outcome": {"last_observed_days": 365,
                             "first_hit_days_by_target": {"20": 40, "25": 70, "30": 90},
                             "max_drawdown_pct": -12}},
        {"ticker": "EDGE", "issuer_key": "EDGE", "decision_date": "2020-02-03",
         "entry_date": "2020-02-04", "avg_dollar_volume": 8_000_000,
         "_emerging_evaluation": passing,
         "_tuning_outcome": {"last_observed_days": 365,
                             "first_hit_days_by_target": {"20": 30, "25": 50, "30": 80},
                             "max_drawdown_pct": -10}},
    ]
    episodes = collapse_episodes(rows, "broad", "5m_to_10m", step_days=30)
    assert len(episodes) == 1
    assert episodes[0]["_episode"]["signals"] == 2
    report = summarize(episodes)
    assert report["targets_within_365_days"]["30"]["success_rate_pct"] == 100
    assert report["targets_within_365_days"]["30"]["median_days_to_hit"] == 90


def test_emerging_database_preserves_history_and_records_graduation(tmp_path):
    with Store(tmp_path / "emerging.db") as store:
        candidate = {
            "ticker": "EDGE", "company": "Edge Co", "sector": "Technology",
            "strategy": "growth", "status": "emerging_candidate", "price": 10,
            "shadow_score": 65, "shadow_verdict": "Watch",
            "financial_strength": 72, "avg_dollar_volume": 7_000_000,
            "source": "finviz_discovery", "policy_version": "emerging_candidate_v1",
            "evidence": {"information_only": True, "score_effect": 0},
        }
        first = store.replace_emerging_candidates(
            [candidate], observed_at="2026-08-28T12:00:00+00:00")
        assert first["new"] == 1
        current = store.emerging_candidates()
        assert current[0]["ticker"] == "EDGE"
        assert current[0]["evidence"]["score_effect"] == 0

        second = store.replace_emerging_candidates(
            [], official_tickers=["EDGE"],
            observed_at="2026-08-29T12:00:00+00:00")
        assert second["graduated_to_active"] == 1
        assert store.emerging_candidates() == []
        historical = store.emerging_candidates(active_only=False)[0]
        assert historical["status"] == "graduated_to_active"
        assert historical["active"] is False
        events = list(store.con.execute(
            "SELECT status FROM emerging_candidate_events WHERE ticker='EDGE' "
            "ORDER BY event_id"))
        assert [row["status"] for row in events] == [
            "emerging_candidate", "graduated_to_active"]


def test_ui_keeps_emerging_research_separate_from_official_buy():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(
        encoding="utf-8")
    assert 'data-mode="emerging"' in html
    assert "Emerging Research · not validated" in html
    assert 'id="emvariant"' in html
    assert 'id="emliquidity"' in html
    assert "research-only names cannot create a position" in html
    assert "b.emerging_candidates || []" in html
    backtest = (Path(__file__).resolve().parents[1] / "web" / "backtest.html").read_text(
        encoding="utf-8")
    assert "function emergingCandidateBacktest(b)" in backtest
    assert "Broad · Balanced · Selective · point-in-time" in backtest


def test_live_refresh_validation_checks_both_universes(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    observed = now.isoformat()
    db_path = tmp_path / "live.db"
    with Store(db_path) as store:
        store.upsert_security("EDGE", "Edge Co", "Technology")
        store.upsert_security("CORE", "Core Co", "Technology")
        store.replace_universe("finviz", ["CORE"], refreshed_at=observed)
        store.replace_universe(
            "finviz_discovery", ["CORE", "EDGE"], refreshed_at=observed)
    board = {
        "meta": {
            "generated_at": observed, "count": 1,
            "universe_source": "finviz", "universe_refreshed_at": observed,
            "emerging_candidates": {
                "discovery_universe_refreshed_at": observed,
                "policy_version": "emerging_research_v2",
                "not_validated": True, "official_score_effect": 0,
            },
        },
        "emerging_candidates": [{
            "ticker": "EDGE", "emerging_candidate": {
                "not_validated": True, "matched_variants": ["broad"]}},
        ],
    }
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(board), encoding="utf-8")
    result = validate_live_refresh(board_path, db_path, now=now)
    assert result["official"]["members"] == 1
    assert result["discovery"]["members"] == 2
    assert result["emerging_candidates"] == 1


def test_live_refresh_validation_rejects_stale_data(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stale = (now - timedelta(hours=3)).isoformat()
    db_path = tmp_path / "stale.db"
    with Store(db_path) as store:
        store.upsert_security("CORE", "Core Co", "Technology")
        store.replace_universe("finviz", ["CORE"], refreshed_at=stale)
        store.replace_universe("finviz_discovery", ["CORE"], refreshed_at=stale)
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps({"meta": {
        "generated_at": stale, "count": 1, "universe_source": "finviz",
        "universe_refreshed_at": stale, "emerging_candidates": {
            "discovery_universe_refreshed_at": stale,
            "policy_version": "emerging_research_v2",
            "not_validated": True, "official_score_effect": 0}},
        "emerging_candidates": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="not fresh"):
        validate_live_refresh(board_path, db_path, max_age_minutes=120, now=now)


def test_workflows_refresh_and_validate_current_universes():
    root = Path(__file__).resolve().parents[1]
    refresh = (root / ".github" / "workflows" / "refresh.yml").read_text(
        encoding="utf-8")
    backtest = (root / ".github" / "workflows" / "backtest.yml").read_text(
        encoding="utf-8")
    assert "build_all.py --refresh" in refresh
    assert "validate_live_refresh.py" in refresh
    assert "Refresh current official + Emerging universes" in backtest
    assert "build_all.py --refresh" in backtest
    assert "validate_live_refresh.py" in backtest
