"""Controlled research cycle for improving Buy precision without look-ahead.

The LLM may propose hypotheses and explain completed failures.  This module is
the deterministic boundary: predictive rules can reference only fields frozen
on the Buy date, candidates are selected on development data, and validation /
test results are opened only after selection.  Nothing here mutates scoring or
production configuration.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from fairentry.backtest.evidence import _fixed_horizon_evaluation
from fairentry.backtest.sfa_tune import _episode_roots


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES = ROOT / "config" / "predictive_rules.json"

DEFAULT_POLICY = {
    "development_share": 0.60,
    "validation_share": 0.20,
    "primary_target_pct": 30,
    "primary_horizon_days": 365,
    "minimum_completed_episodes": 100,
    "minimum_unique_issuers": 50,
    "minimum_final_test_episodes": 20,
    "minimum_validation_improvement_pp": 5.0,
    "minimum_test_improvement_pp": 5.0,
    "minimum_test_success_rate_pct": 60.0,
    "maximum_drawdown_deterioration_pp": 2.0,
}

FORBIDDEN_PREFIXES = (
    "outcome", "horizons", "return_milestones", "exit", "terminal",
    "fixed_", "days_to_", "highest_gain", "target_failure",
)


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _category(row: dict, category_id: str):
    for category in row.get("categories") or []:
        if category.get("id") == category_id:
            return category
    return None


def _metric(row: dict, metric_id: str):
    for category in row.get("categories") or []:
        for item in category.get("items") or []:
            if metric_id in {item.get("id"), item.get("metric")}:
                return _number(item.get("actual"))
    return None


def factor_value(row: dict, field: str):
    """Return a point-in-time input. Outcome fields are deliberately absent."""
    if field == "no_hard_veto":
        return not bool(row.get("vetoes"))
    if field == "growth_qualified":
        return bool((row.get("growth_qualification") or {}).get("qualified"))
    if field.startswith("category."):
        category = _category(row, field.split(".", 1)[1])
        return _number(category.get("score")) if category else None
    if field.startswith("metric."):
        return _metric(row, field.split(".", 1)[1])
    if field.startswith("valuation."):
        return _number((row.get("valuation") or {}).get(field.split(".", 1)[1]))
    if field.startswith("debt."):
        return _number((row.get("debt_direction") or {}).get(field.split(".", 1)[1]))
    if field.startswith("research."):
        return _number((row.get("research_factors") or {}).get(field.split(".", 1)[1]))
    if field in {"score", "avg_dollar_volume"}:
        return _number(row.get(field))
    if field in {"regime", "sector", "strategy_key"}:
        return row.get(field)
    raise ValueError(f"unsupported predictive field: {field}")


def validate_rules(rules: list[dict]) -> list[dict]:
    """Reject future/outcome fields before any candidate is evaluated."""
    clean = []
    seen = set()
    for rule in rules:
        rule_id = str(rule.get("id") or "").strip()
        if not rule_id or rule_id in seen:
            raise ValueError("every predictive rule needs a unique id")
        seen.add(rule_id)
        conditions = rule.get("conditions") or []
        if not conditions:
            raise ValueError(f"{rule_id}: at least one condition is required")
        for condition in conditions:
            field = str(condition.get("field") or "")
            if not field or field.startswith(FORBIDDEN_PREFIXES):
                raise ValueError(f"{rule_id}: future/outcome field is forbidden: {field}")
            if condition.get("op") not in {">=", ">", "<=", "<", "==", "!=", "in"}:
                raise ValueError(f"{rule_id}: unsupported operator")
            # Resolve the namespace now, without needing a real observation.
            if not (field in {"no_hard_veto", "growth_qualified", "score",
                              "avg_dollar_volume", "regime", "sector", "strategy_key"}
                    or field.startswith(("category.", "metric.", "valuation.", "debt.",
                                         "research."))):
                raise ValueError(f"{rule_id}: unsupported predictive field: {field}")
        clean.append({
            "id": rule_id,
            "label": rule.get("label") or rule_id.replace("_", " ").title(),
            "hypothesis": rule.get("hypothesis") or "",
            "conditions": conditions,
        })
    return clean


def load_rules(path: str | Path = DEFAULT_RULES) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_rules(payload.get("rules") or [])


def _matches(row: dict, rule: dict) -> bool:
    for condition in rule["conditions"]:
        actual = factor_value(row, condition["field"])
        if actual is None:
            return False
        expected, op = condition.get("value"), condition["op"]
        if op == "in":
            passed = actual in expected
        elif op == "==":
            passed = actual == expected
        elif op == "!=":
            passed = actual != expected
        else:
            left, right = _number(actual), _number(expected)
            if left is None or right is None:
                return False
            passed = {">=": left >= right, ">": left > right,
                      "<=": left <= right, "<": left < right}[op]
        if not passed:
            return False
    return True


def _wilson(successes: int, total: int, z: float = 1.6448536269514722):
    if not total:
        return None
    p = successes / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return [round(max(0, centre - margin) * 100, 1),
            round(min(1, centre + margin) * 100, 1)]


def _target_summary(episodes: list[dict], target: int, horizon: int) -> dict:
    reached, failed, active, excluded, days, drawdowns = 0, 0, 0, 0, [], []
    issuers = set()
    for row in episodes:
        result = _fixed_horizon_evaluation(row, target, horizon)
        state = result["result"]
        if state == "success":
            reached += 1
            first = ((row.get("return_milestones") or {}).get("first_hit_days") or {}).get(str(target))
            if isinstance(first, (int, float)):
                days.append(float(first))
        elif state == "failure":
            failed += 1
        elif state == "active":
            active += 1
        else:
            excluded += 1
        if state in {"success", "failure"}:
            issuers.add(row.get("issuer_key") or row.get("ticker"))
            drawdown = ((row.get("return_milestones") or {})
                        .get("max_drawdown_pct_by_horizon", {}).get(str(horizon)))
            if isinstance(drawdown, (int, float)):
                drawdowns.append(float(drawdown))
    completed = reached + failed
    return {
        "target_pct": target,
        "horizon_days": horizon,
        "episodes": len(episodes),
        "completed_episodes": completed,
        "unique_issuers": len(issuers),
        "reached": reached,
        "failed": failed,
        "active": active,
        "excluded": excluded,
        "success_rate_pct": round(reached / completed * 100, 1) if completed else None,
        "success_rate_ci90_pct": _wilson(reached, completed),
        "median_days_to_hit": round(statistics.median(days), 1) if days else None,
        "median_max_drawdown_pct": round(statistics.median(drawdowns), 1) if drawdowns else None,
        "worst_max_drawdown_pct": round(min(drawdowns), 1) if drawdowns else None,
    }


def _summary(episodes: list[dict]) -> dict:
    matrix = {
        str(target): {
            str(horizon): _target_summary(episodes, target, horizon)
            for horizon in (365, 730, 1095)
        }
        for target in (20, 25, 30)
    }
    return {
        "episodes": len(episodes),
        "unique_issuers": len({row.get("issuer_key") or row.get("ticker") for row in episodes}),
        "primary": matrix["30"]["365"],
        "target_matrix": matrix,
    }


def _split_dates(episodes: list[dict], policy: dict):
    dates = sorted({row.get("decision_date") or row.get("entry_date") for row in episodes})
    if len(dates) < 3:
        return None
    dev_end = max(1, min(len(dates) - 2, int(len(dates) * policy["development_share"])))
    validation_end = max(dev_end + 1, min(len(dates) - 1,
                                          int(len(dates) * (policy["development_share"] + policy["validation_share"]))))
    return {
        "development": set(dates[:dev_end]),
        "validation": set(dates[dev_end:validation_end]),
        "test": set(dates[validation_end:]),
    }


def _partition(episodes: list[dict], dates: set[str]):
    return [row for row in episodes
            if (row.get("decision_date") or row.get("entry_date")) in dates]


def _check(check_id: str, label: str, passed: bool, baseline, candidate, rule: str):
    return {"id": check_id, "label": label, "passed": bool(passed),
            "baseline": baseline, "candidate": candidate, "rule": rule}


def run_predictive_rule_research(observations: list[dict], *, step_days: int = 30,
                                 rules: list[dict] | None = None,
                                 policy: dict | None = None) -> dict:
    """Select on development data, then evaluate once on validation and test."""
    policy = {**DEFAULT_POLICY, **(policy or {})}
    rules = validate_rules(rules) if rules is not None else load_rules()
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _episode_roots(observations, {}, {"buy": 0, "watch": 0}, max_gap,
                              use_recorded_verdict=True)
    split_dates = _split_dates(episodes, policy)
    if not split_dates:
        return {"ok": False, "reason": "fewer than three historical Buy dates"}
    baseline = {name: _summary(_partition(episodes, dates))
                for name, dates in split_dates.items()}
    baseline["all"] = _summary(episodes)

    development_results = []
    for rule in rules:
        selected = [row for row in episodes if _matches(row, rule)]
        development = _summary(_partition(selected, split_dates["development"]))
        primary = development["primary"]
        rank = ((primary.get("success_rate_ci90_pct") or [-1])[0],
                primary.get("success_rate_pct") or -1,
                primary.get("completed_episodes") or 0)
        development_results.append((rank, rule, selected, development))
    development_results.sort(key=lambda row: row[0], reverse=True)
    _, selected_rule, selected_episodes, selected_development = development_results[0]
    candidate = {
        "development": selected_development,
        "validation": _summary(_partition(selected_episodes, split_dates["validation"])),
        "test": _summary(_partition(selected_episodes, split_dates["test"])),
        "all": _summary(selected_episodes),
    }

    bpv, cpv = baseline["validation"]["primary"], candidate["validation"]["primary"]
    bpt, cpt = baseline["test"]["primary"], candidate["test"]["primary"]
    bpa, cpa = baseline["all"]["primary"], candidate["all"]["primary"]
    def improvement(new, old):
        if None in (new.get("success_rate_pct"), old.get("success_rate_pct")):
            return None
        return round(new["success_rate_pct"] - old["success_rate_pct"], 1)
    validation_gain, test_gain = improvement(cpv, bpv), improvement(cpt, bpt)
    drawdown_change = (round(cpt["median_max_drawdown_pct"] - bpt["median_max_drawdown_pct"], 1)
                       if None not in (cpt.get("median_max_drawdown_pct"),
                                       bpt.get("median_max_drawdown_pct")) else None)
    checks = [
        _check("all_sample", "At least the required completed episodes",
               cpa["completed_episodes"] >= policy["minimum_completed_episodes"],
               bpa["completed_episodes"], cpa["completed_episodes"],
               f">= {policy['minimum_completed_episodes']}"),
        _check("unique_issuers", "Enough different companies",
               cpa["unique_issuers"] >= policy["minimum_unique_issuers"],
               bpa["unique_issuers"], cpa["unique_issuers"],
               f">= {policy['minimum_unique_issuers']}"),
        _check("test_sample", "Enough final-test episodes",
               cpt["completed_episodes"] >= policy["minimum_final_test_episodes"],
               bpt["completed_episodes"], cpt["completed_episodes"],
               f">= {policy['minimum_final_test_episodes']}"),
        _check("validation_improvement", "Improves on unseen validation dates",
               validation_gain is not None and validation_gain >= policy["minimum_validation_improvement_pp"],
               bpv.get("success_rate_pct"), cpv.get("success_rate_pct"),
               f">= +{policy['minimum_validation_improvement_pp']} percentage points"),
        _check("test_improvement", "Improves on final unseen dates",
               test_gain is not None and test_gain >= policy["minimum_test_improvement_pp"],
               bpt.get("success_rate_pct"), cpt.get("success_rate_pct"),
               f">= +{policy['minimum_test_improvement_pp']} percentage points"),
        _check("test_precision", "Final-test success is practically meaningful",
               cpt.get("success_rate_pct") is not None
               and cpt["success_rate_pct"] >= policy["minimum_test_success_rate_pct"],
               bpt.get("success_rate_pct"), cpt.get("success_rate_pct"),
               f">= {policy['minimum_test_success_rate_pct']}%"),
        _check("drawdown", "Median drawdown does not materially worsen",
               drawdown_change is not None
               and drawdown_change >= -policy["maximum_drawdown_deterioration_pp"],
               bpt.get("median_max_drawdown_pct"), cpt.get("median_max_drawdown_pct"),
               f"no more than {policy['maximum_drawdown_deterioration_pp']}pp worse"),
    ]
    eligible = bool(checks) and all(check["passed"] for check in checks)
    compact_development = [
        {"rule": rule, "development": development}
        for _rank, rule, _episodes, development in development_results
    ]
    return {
        "ok": True,
        "version": 1,
        "method": "chronological_point_in_time_filter_research_v1",
        "objective": "Improve +30% within one year precision without future information.",
        "production_effect": "none",
        "promotion": "manual_only",
        "default_changed": False,
        "policy": policy,
        "leakage_audit": {
            "passed": True,
            "allowed_information": "Values frozen on or before the original Buy decision date.",
            "forbidden_information": "Later prices, outcomes, failure causes, later filings, later estimates, and later news.",
        },
        "split": {name: {"first": min(dates), "last": max(dates), "cohorts": len(dates)}
                  for name, dates in split_dates.items()},
        "baseline": baseline,
        "development_candidates": compact_development,
        "selected_rule": selected_rule,
        "candidate": candidate,
        "validation_improvement_pp": validation_gain,
        "test_improvement_pp": test_gain,
        "promotion_checks": checks,
        "promotion_eligible": eligible,
        "decision": "Ready for manual review" if eligible else "Keep current production rules",
        "llm_roles": {
            "failure_researcher": {
                "may_use_later_evidence": True,
                "purpose": "Explain a completed miss and normalize its cause; never score it retrospectively.",
            },
            "predictive_rule_researcher": {
                "may_use_later_evidence": False,
                "purpose": "Propose entry-date warning signals for deterministic chronological testing.",
            },
        },
    }
