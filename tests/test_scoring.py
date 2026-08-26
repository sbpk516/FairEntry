import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.scoring.rules import apply_rule
from fairentry.scoring.engine import score_ticker, growth_qualification
from fairentry.scoring.fair_value import fair_value
from fairentry.pipeline.export import _export_categories, _labels, _map

_SEC = {"ticker": "T", "company": "Test", "sector": "Technology"}
_SETTINGS = {"margin_of_safety_pct": 15, "target_upside_pct": 30}
_MED = {"Technology": {"gross_margin": 40}}


def _strong_metrics(**over):
    """A clean, cheap, high-quality name (should score Buy) so veto/gate tests
    isolate exactly the negative signal under test."""
    m = {"price": {"value": 100}, "target_price": {"value": 160},
         "gross_margin": {"value": 60}, "oper_margin": {"value": 30},
         "roic": {"value": 25}, "debt_eq": {"value": 0.2},
         "current_ratio": {"value": 2.5}, "altman_z": {"value": 6.0},
         "rev_growth_qoq": {"value": 20}, "eps_growth_next_y": {"value": 25},
         "perf_year": {"value": 20}, "pfcf_ratio": {"value": 10},
         "ps_ratio": {"value": 2}, "red_flags_score": {"value": 100},
         "red_flags_critical": {"value": 0}, "analyst_recom": {"value": 1.5}}
    m.update({k: {"value": v} for k, v in over.items()})
    return m


def test_higher_better():
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 20)
    assert s == 100
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 0)
    assert s == 0
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 10)
    assert 49 <= s <= 51


def test_lower_better_and_band():
    s, _ = apply_rule({"type": "lower_better", "full_at": 12, "floor_at": 40}, 12)
    assert s == 100
    s, _ = apply_rule({"type": "band", "bands": [{"min": 2.6, "score": 90},
                       {"min": 1.8, "score": 55}, {"min": 0, "score": 20}]}, 3.0)
    assert s == 90


def test_missing_metric_is_na():
    s, why = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, None)
    assert s is None and why == "no data"


def test_growth_qualification_accepts_stable_business_at_deep_discount():
    result = growth_qualification(
        {"rev_growth_qoq": 1, "oper_margin": 8},
        {"intrinsic_gap_pct": 35},
    )
    assert result["qualified"] is True
    assert result["path"] == "stable_and_deeply_undervalued"


def test_growth_qualification_accepts_meaningful_numerical_improvement():
    result = growth_qualification(
        {"rev_growth_qoq": 12, "oper_margin": -4},
        {"intrinsic_gap_pct": 10},
    )
    assert result["qualified"] is True
    assert result["path"] == "meaningfully_improving"


def test_growth_qualification_rejects_cheap_but_deteriorating_business():
    result = growth_qualification(
        {"rev_growth_qoq": -8, "oper_margin": -2},
        {"intrinsic_gap_pct": 80},
    )
    assert result["qualified"] is False
    assert result["path"] == "not_yet_qualified"


def test_reproducible():
    """Same inputs -> identical score (deterministic core)."""
    cfg = load_config()
    sec = {"ticker": "T", "company": "Test", "sector": "Technology"}
    metrics = {"price": {"value": 100, "source": "x", "fetched_at": "2026-07-12"},
               "target_price": {"value": 130, "source": "x", "fetched_at": "2026-07-12"},
               "gross_margin": {"value": 50, "source": "x", "fetched_at": "2026-07-12"},
               "debt_eq": {"value": 0.3, "source": "x", "fetched_at": "2026-07-12"},
               "oper_margin": {"value": 25, "source": "x", "fetched_at": "2026-07-12"}}
    med = {"Technology": {"gross_margin": 40}}
    s = {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    r1 = score_ticker(cfg, sec, metrics, med, s)
    r2 = score_ticker(cfg, sec, metrics, med, s)
    assert r1["preliminary"] == r2["preliminary"]
    assert r1["verdict"] == r2["verdict"]
    assert r1["base_score"] > 0


def test_sfa_percent_weights_preserve_the_previous_display_scale_decision():
    """Changing only the display scale must not change any live decision."""
    cfg = load_config()
    current = cfg.scoring["presets"]["backtest_recommended"]
    previous = cfg.scoring["presets"]["pre_sfa_display_scale"]
    current_result = score_ticker(
        cfg, _SEC, _strong_metrics(), _MED, {**_SETTINGS, "weights": current}
    )
    previous_result = score_ticker(
        cfg, _SEC, _strong_metrics(), _MED, {**_SETTINGS, "weights": previous}
    )
    assert current_result["base_score"] == previous_result["base_score"]
    assert current_result["verdict"] == previous_result["verdict"]


def test_decision_trace_reproduces_score_and_explains_every_effect():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(), _MED, _SETTINGS)
    trace = r["decision_trace"]
    assert trace["base_score"] == r["base_score"]
    assert trace["final_score"] == r["score"]
    assert trace["final_verdict"] == r["verdict"]
    assert trace["formula"]
    assert abs(sum(c["contribution"] for c in r["categories"]
                   if c["contribution"] is not None) - r["base_score"]) < 0.11
    for category in r["categories"]:
        for item in category["items"]:
            assert "status" in item
            if item["score"] is not None and item["decision_status"] == "tested":
                assert item["contribution"] is not None
            if item["decision_status"] != "tested":
                assert item["contribution"] is None


def test_ai_or_news_modifier_cannot_change_the_tested_verdict():
    cfg = load_config()
    baseline = score_ticker(cfg, _SEC, _strong_metrics(), _MED, _SETTINGS)
    attempted = score_ticker(
        cfg, _SEC, _strong_metrics(), _MED,
        dict(_SETTINGS, thesis_modifier=100),
    )
    assert attempted["thesis_modifier"] == 0
    assert attempted["score"] == baseline["score"]
    assert attempted["verdict"] == baseline["verdict"]


def test_testing_debt_direction_is_visible_but_has_no_contribution():
    cfg = load_config()
    metrics = _strong_metrics(debt_to_assets_change_yoy_pp=-8,
                              debt_to_assets_pct=20,
                              debt_to_assets_yago_pct=28)
    result = score_ticker(cfg, _SEC, metrics, _MED, _SETTINGS)
    survival = next(c for c in result["categories"] if c["id"] == "survival")
    debt = next(i for i in survival["items"] if i["id"] == "debt_direction")
    assert debt["decision_status"] == "testing"
    assert debt["actual"] == -8
    assert debt["contribution"] is None
    assert result["research_metrics"]["debt_to_assets_pct"] == 20


def test_missing_item_is_traced_and_excluded_not_neutralized():
    cfg = load_config()
    metrics = _strong_metrics()
    metrics.pop("insider_score", None)
    r = score_ticker(cfg, _SEC, metrics, _MED, _SETTINGS)
    confirmation = next(c for c in r["categories"] if c["id"] == "confirmation")
    insider = next(i for i in confirmation["items"] if i["id"] == "insider_buying")
    assert insider["score"] is None
    assert insider["status"] == "unknown"
    assert insider["contribution"] is None
    assert confirmation["missing_item_weight"] >= insider["weight"]


def test_exported_drilldown_carries_raw_breakout_formula_and_provenance():
    cfg = load_config()
    metrics = _strong_metrics(breakout_price_score=90)
    r = score_ticker(cfg, _SEC, metrics, _MED, _SETTINGS)
    breakout = {"factors": [{
        "scoring_metric": "breakout_price_score",
        "actual": "+3.20%",
        "expected": "close at least 2% above resistance",
        "formula": "(latest close / resistance - 1) × 100",
        "evidence": "Latest close cleared prior resistance.",
        "source": "adjusted daily price history",
        "observed_at": "2026-07-18",
        "calculation_version": "breakout_v2",
    }]}
    cats = _export_categories(r, breakout)
    confirmation = next(c for c in cats if c["id"] == "confirmation")
    price = next(i for i in confirmation["items"] if i["id"] == "price_breakout")
    assert price["raw_actual"] == "+3.20%"
    assert price["formula"].startswith("(latest close")
    assert price["source"] == "adjusted daily price history"
    assert price["observed_at"] == "2026-07-18"

    institutional = next(i for i in confirmation["items"] if i["id"] == "inst_flow")
    assert "Finviz Institutional Transactions" in institutional["definition"]
    assert "not FairEntry's curated SEC 13F" in institutional["definition"]
    assert institutional["formula"].startswith("score = clamp")


def test_management_execution_is_a_stable_progressive_disclosure_row():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(), _MED, _SETTINGS)
    r["_breakout_setup"] = {"overall": "building", "factors": [], "counts": {}}
    r["_thesis"] = None
    mapped = _map(r, ["growth"], "quality_growth")
    management = next(f for f in mapped["breakout_setup"]["factors"]
                      if f["id"] == "management_execution")
    assert management["label"] == "Management Execution"
    assert management["status"] == "unknown"
    assert "No specific evidence" in management["evidence"]
    policy = next(f for f in mapped["breakout_setup"]["factors"]
                  if f["id"] == "policy_impact")
    assert policy["status"] == "unknown"
    assert "government action" in policy["expected"]

    r["_thesis"] = {
        "thesis_score": 70,
        "summary": "Management delivered its stated margin plan.",
        "breakout_evidence": [{
            "id": "management_execution", "label": "Management Execution",
            "group": "management", "status": "satisfied",
            "evidence": "Operating margin reached the supplied target.",
            "source": "quarterly results", "date": "2026-07-18",
        }],
    }
    mapped = _map(r, ["growth"], "quality_growth")
    management = next(f for f in mapped["breakout_setup"]["factors"]
                      if f["id"] == "management_execution")
    assert management["status"] == "satisfied"
    assert management["observed_at"] == "2026-07-18"


def test_named_policy_and_expansion_evidence_keep_their_own_rows():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(), _MED, _SETTINGS)
    r["_breakout_setup"] = {"overall": "building", "factors": [], "counts": {}}
    r["_thesis"] = {
        "thesis_score": 70,
        "breakout_evidence": [
            {"id": "policy_impact", "label": "Policy", "group": "catalyst",
             "status": "satisfied", "evidence": "A named agency awarded a tax credit.",
             "source": "Agency release", "date": "2026-07-20"},
            {"id": "investment_expansion", "label": "Expansion", "group": "management",
             "status": "partial", "evidence": "A new plant entered construction.",
             "source": "Company filing", "date": "2026-07-19"},
        ],
    }
    mapped = _map(r, ["growth"], "quality_growth")
    factors = {f["id"]: f for f in mapped["breakout_setup"]["factors"]}
    assert factors["catalyst_visibility"]["status"] == "unknown"
    assert factors["management_execution"]["status"] == "unknown"
    assert factors["policy_impact"]["source"] == "Agency release"
    assert factors["policy_impact"]["status"] == "satisfied"
    assert factors["investment_expansion"]["source"] == "Company filing"
    assert factors["investment_expansion"]["status"] == "partial"
    context = mapped["qualitative_context"]
    assert set(context["categories"]) == {
        "quality", "survival", "growth", "valuation", "confirmation", "catalysts", "risk"
    }
    assert context["score_effect"] == 0
    assert context["verdict_effect"] == "none"
    assert {row["id"] for row in context["categories"]["catalysts"]} >= {
        "policy_impact", "investment_expansion"
    }


def test_score_preserves_country():
    cfg = load_config()
    sec = {"ticker": "T", "company": "Test", "sector": "Technology", "country": "Taiwan"}
    metrics = {"price": {"value": 100, "source": "x", "fetched_at": "2026-07-12"},
               "target_price": {"value": 130, "source": "x", "fetched_at": "2026-07-12"},
               "gross_margin": {"value": 50, "source": "x", "fetched_at": "2026-07-12"}}
    r = score_ticker(cfg, sec, metrics, {"Technology": {"gross_margin": 40}},
                     {"margin_of_safety_pct": 15, "target_upside_pct": 30})
    assert r["country"] == "Taiwan"


def test_non_usa_country_is_tile_label():
    rec = {"country": "Taiwan",
           "price": 90, "verdict": "Buy",
           "valuation": {"upside_pct": 30, "valuation_label": "cheap", "buy_zone": 100},
           "categories": [{"id": "quality", "score": 88, "items": []},
                          {"id": "growth", "score": 80,
                           "items": [{"metric": "rev_growth_qoq", "actual": 31}]}],
           "vetoes": [], "soft_gates": []}
    assert _labels(rec)[0] == ["Taiwan", "info"]


def test_usa_country_is_not_tile_label():
    rec = {"country": "USA",
           "price": 90, "verdict": "Buy",
           "valuation": {"upside_pct": 30, "valuation_label": "cheap", "buy_zone": 100},
           "categories": [], "vetoes": [], "soft_gates": []}
    assert ["USA", "info"] not in _labels(rec)


def test_tile_labels_include_quality_growth_and_entry():
    rec = {"country": "USA", "price": 214, "verdict": "Watch",
           "valuation": {"upside_pct": 12, "valuation_label": "expensive", "buy_zone": 185},
           "categories": [{"id": "quality", "score": 92, "items": []},
                          {"id": "growth", "score": 90,
                           "items": [{"metric": "rev_growth_qoq", "actual": 31}]}],
           "vetoes": [], "soft_gates": [{"id": "expensive", "reason": "Valuation is expensive"}]}
    labels = _labels(rec)
    assert ["Quality: excellent", "good"] in labels
    assert ["Growth +31%", "good"] in labels
    assert ["Entry: stretched", "warn"] in labels
    assert ["Valuation upside +12%", "warn"] in labels


# ---- veto / soft-gate firing -------------------------------------------------

def test_going_concern_is_visible_but_does_not_change_tested_verdict():
    cfg = load_config()
    baseline = score_ticker(cfg, _SEC, _strong_metrics(going_concern=0), _MED, _SETTINGS)
    warned = score_ticker(cfg, _SEC, _strong_metrics(going_concern=1), _MED, _SETTINGS)
    assert warned["verdict"] == baseline["verdict"]
    assert not any(v["id"] == "going_concern" for v in warned["vetoes"])
    assert any(v["id"] == "going_concern" for v in warned["context_warnings"])


def test_no_going_concern_is_not_vetoed():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(going_concern=0), _MED, _SETTINGS)
    assert not any(v["id"] == "going_concern" for v in r["vetoes"])


def test_going_concern_warning_through_store():
    import tempfile, os
    from fairentry.store.db import Store
    cfg = load_config()
    db = tempfile.mktemp(suffix=".db"); s = Store(db)
    s.upsert_security("GC", "GoingConcern Co", "Technology")
    for fid, val in {"price": 10, "target_price": 30, "gross_margin": 60,
                     "oper_margin": 30, "roic": 20, "going_concern": True}.items():
        s.set_metric("GC", fid, val, "test")
    s.commit()
    assert s.metrics_for("GC")["going_concern"]["value"] == 1.0   # stored as float
    r = score_ticker(cfg, s.securities()[0], s.metrics_for("GC"), _MED, _SETTINGS)
    s.close(); os.remove(db)
    assert any(v["id"] == "going_concern" for v in r["context_warnings"])


def test_critical_red_flag_is_information_only_warning():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(red_flags_critical=1), _MED, _SETTINGS)
    assert not any(v["id"] == "critical_red_flag" for v in r["vetoes"])
    assert any(v["id"] == "critical_red_flag" for v in r["context_warnings"])


def test_distress_corroborated_veto():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(altman_z=1.0, debt_eq=3.0), _MED, _SETTINGS)
    assert any(v["id"] == "distress_corroborated" for v in r["vetoes"])
    assert r["verdict"] == "Avoid"


def test_missing_veto_metric_does_not_fire():
    """A veto whose metric isn't present must not fire (unevaluable -> skipped)."""
    cfg = load_config()
    m = _strong_metrics()
    m.pop("altman_z"); m.pop("red_flags_critical")
    r = score_ticker(cfg, _SEC, m, _MED, _SETTINGS)
    assert not r["vetoes"]


def test_analyst_target_is_not_used_as_tested_fair_value():
    cfg = load_config()
    m = _strong_metrics(target_price=110)
    for k in ("pfcf_ratio", "ps_ratio"):
        m.pop(k, None)
    r = score_ticker(cfg, _SEC, m, _MED, _SETTINGS)
    assert round(r["valuation"]["upside_pct"]) == 0
    analyst = next(x for x in r["valuation"]["methods"] if x["key"] == "analyst")
    assert analyst["decision_status"] == "information_only"
    assert any(g["id"] == "upside_below_target" for g in r["soft_gates"])
    assert r["verdict"] != "Buy"


def test_missing_survival_data_caps_buy():
    """Missing survival data is a soft gate, not a free pass to Buy."""
    cfg = load_config()
    weights = {"quality": 100, "survival": 0, "growth": 0, "valuation": 0,
               "confirmation": 0, "catalysts": 0, "risk": 0}
    r = score_ticker(cfg, _SEC,
                     {"price": {"value": 100}, "target_price": {"value": 160},
                      "gross_margin": {"value": 80}, "roic": {"value": 30},
                      "oper_margin": {"value": 30}},
                     {"Technology": {"gross_margin": 40, "roic": 10}},
                     {"weights": weights})
    assert r["preliminary"] >= cfg.verdict_bands["buy"]
    assert r["verdict"] == "Watch"
    assert any(g["id"] == "survival_floor" and "missing data" in g["reason"]
               for g in r["soft_gates"])


# ---- multi-method fair value -------------------------------------------------

def test_fair_value_blends_multiple_methods():
    metrics = {"price": {"value": 100}, "target_price": {"value": 130},
               "fwd_pe": {"value": 10}, "eps_growth_next_y": {"value": 20},
               "ps_ratio": {"value": 2}, "pb_ratio": {"value": 1.5},
               "pfcf_ratio": {"value": 12}}
    sector_med = {"fwd_pe": 15, "ps_ratio": 3, "pb_ratio": 2.5}
    fv = fair_value(metrics, mos_pct=15, sector_med=sector_med)
    assert fv["method_count"] == 3                      # replayable methods contributed
    assert fv["context_method_count"] == 3              # forecasts remain visible
    assert fv["fair_low"] <= fv["fair_base"] <= fv["fair_high"]
    assert fv["upside_pct"] > 0                         # cheap on every method -> upside
    assert fv["valuation_label"] == "cheap"
    assert fv["buy_zone"] < fv["fair_base"]             # MoS discount applied


def test_fair_value_no_price_is_unknown():
    fv = fair_value({"target_price": {"value": 50}}, mos_pct=15)
    assert fv["valuation_label"] == "unknown"
    assert fv["method_count"] == 0


def test_fair_value_expensive_label():
    metrics = {"price": {"value": 100}, "target_price": {"value": 80},
               "fwd_pe": {"value": 40}, "ps_ratio": {"value": 10}}
    sector_med = {"fwd_pe": 15, "ps_ratio": 3}
    fv = fair_value(metrics, sector_med=sector_med)
    assert fv["valuation_label"] == "expensive"
    assert fv["upside_pct"] < 0


def test_method_agreement_is_a_zero_effect_shadow_pass():
    metrics = {
        "price": {"value": 100},
        "ps_ratio": {"value": 2},
        "pfcf_ratio": {"value": 12},
        "pb_ratio": {"value": 1},
        "roe": {"value": 18},
    }
    sector_med = {"ps_ratio": 3, "pb_ratio": 1.5}
    fv = fair_value(
        metrics, sector_med=sector_med,
        sector="Financial Services", industry="Banks - Regional",
    )
    agreement = fv["method_agreement"]
    assert agreement["passes"] is True
    assert agreement["status"] == "pass"
    assert agreement["method_count"] == 3
    assert agreement["dispersion_pct"] <= 75
    assert agreement["score_effect"] == 0
    assert agreement["verdict_effect"] == "none"
    # The original production fair-value calculation is still the baseline.
    assert fv["method_count"] == 3
    assert fv["fair_base"] == 150


def test_method_agreement_excludes_book_for_intangible_business():
    metrics = {
        "price": {"value": 100},
        "ps_ratio": {"value": 2},
        "pfcf_ratio": {"value": 12},
        "pb_ratio": {"value": 1},
        "roe": {"value": 30},
    }
    sector_med = {"ps_ratio": 3, "pb_ratio": 4}
    fv = fair_value(
        metrics, sector_med=sector_med,
        sector="Technology", industry="Software - Infrastructure",
    )
    agreement = fv["method_agreement"]
    assert agreement["methods"] == ["peer_ps", "fcf"]
    assert agreement["excluded_methods"][0]["key"] == "book"
    # Shadow filtering must not rewrite the official baseline range.
    assert fv["method_count"] == 3
    assert fv["fair_high"] == 400
