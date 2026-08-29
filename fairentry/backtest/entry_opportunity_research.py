"""Earliest-Buy and conservative-upside research without look-ahead.

This module does not create a second score or change an official verdict.  It
uses the first recorded Buy in each continuous episode, freezes every input at
that date, and asks whether progressively larger conservative valuation gaps
were associated with better forward outcomes.
"""
from __future__ import annotations

import math
from datetime import date

from fairentry.backtest.research_cycle import (
    _partition,
    _split_dates,
    _target_summary,
)
from fairentry.backtest.sfa_tune import _episode_roots
from fairentry.backtest.valuation_research import POLICIES, evaluate_policy


VERSION = 1
UPSIDE_THRESHOLDS_PCT = (30, 45, 50, 60)
OUTCOME_CONTRACTS = (
    ("primary_30_within_one_year", 30, 365),
    ("secondary_50_within_two_years", 50, 730),
    ("long_term_100_within_three_years", 100, 1095),
)
LOOKBACK_DAYS = 120
MAX_FAIR_VALUE_DECLINE_PCT = 10.0
MAX_SCORE_DECLINE_POINTS = 10.0
CATEGORY_IDS = ("quality", "survival", "growth")
METHOD_AGREEMENT_POLICY = next(
    policy for policy in POLICIES if policy["id"] == "method_agreement"
)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _category_scores(row: dict) -> dict[str, float | None]:
    available = {
        category.get("id"): _number(category.get("score"))
        for category in row.get("categories") or []
    }
    return {category_id: available.get(category_id) for category_id in CATEGORY_IDS}


def _valuation_snapshot(row: dict) -> dict:
    """Return the lowest relevant fair value under the tested agreement policy."""
    agreement = evaluate_policy(row, METHOD_AGREEMENT_POLICY)
    retained = set(agreement.get("methods") or [])
    values = []
    for method in (row.get("valuation") or {}).get("methods") or []:
        fair = _number(method.get("fair"))
        if method.get("key") in retained and fair is not None and fair > 0:
            values.append(fair)
    price = _number(row.get("entry_price"))
    controlled = (
        len(values) >= 2
        and _number(agreement.get("dispersion_pct")) is not None
        and float(agreement["dispersion_pct"]) <= 75.0
    )
    conservative = min(values) if controlled else None
    upside = (
        (conservative / price - 1) * 100
        if conservative is not None and price is not None and price > 0 else None
    )
    reason = (
        None if controlled else
        "Fewer than two relevant methods are available."
        if len(values) < 2 else
        "Relevant valuation methods are more than 75% apart."
    )
    return {
        "available": controlled,
        "reason": reason,
        "method_count": len(values),
        "methods": sorted(retained),
        "dispersion_pct": agreement.get("dispersion_pct"),
        "conservative_fair_value": round(conservative, 2)
        if conservative is not None else None,
        "conservative_upside_pct": round(upside, 2) if upside is not None else None,
    }


def attach_entry_opportunity_factors(
    observations: list[dict],
    *,
    lookback_days: int = LOOKBACK_DAYS,
) -> dict:
    """Attach point-in-time valuation stability and thesis-change evidence.

    The nearest earlier observation is the only comparison point.  Missing
    evidence is never silently promoted to a stable result.
    """
    grouped: dict[str, list[dict]] = {}
    for row in observations:
        key = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
        observed = row.get("decision_date") or row.get("entry_date")
        if key and observed:
            grouped.setdefault(str(key), []).append(row)

    attached = 0
    stable = 0
    for rows in grouped.values():
        prior_rows: list[tuple[dict, dict]] = []
        for row in sorted(rows, key=lambda item: item.get("decision_date") or item["entry_date"]):
            current_date = date.fromisoformat(
                str(row.get("decision_date") or row.get("entry_date"))[:10]
            )
            valuation = _valuation_snapshot(row)
            prior_row = prior_snapshot = None
            prior_days = None
            for candidate, snapshot in reversed(prior_rows):
                candidate_date = date.fromisoformat(
                    str(candidate.get("decision_date") or candidate.get("entry_date"))[:10]
                )
                gap = (current_date - candidate_date).days
                if gap > lookback_days:
                    break
                if gap > 0 and snapshot.get("available"):
                    prior_row, prior_snapshot, prior_days = candidate, snapshot, gap
                    break

            fair_change = None
            if prior_snapshot and valuation.get("conservative_fair_value") is not None:
                old_fair = _number(prior_snapshot.get("conservative_fair_value"))
                if old_fair and old_fair > 0:
                    fair_change = (
                        float(valuation["conservative_fair_value"]) / old_fair - 1
                    ) * 100

            current_categories = _category_scores(row)
            prior_categories = _category_scores(prior_row or {})
            category_changes = {
                category_id: (
                    round(current_categories[category_id] - prior_categories[category_id], 2)
                    if current_categories[category_id] is not None
                    and prior_categories[category_id] is not None else None
                )
                for category_id in CATEGORY_IDS
            }
            current_score = _number(row.get("score"))
            prior_score = _number((prior_row or {}).get("score"))
            score_change = (
                round(current_score - prior_score, 2)
                if current_score is not None and prior_score is not None else None
            )
            growth_qualified = (
                (row.get("growth_qualification") or {}).get("qualified") is True
            )
            stable_value = (
                fair_change is not None
                and fair_change >= -MAX_FAIR_VALUE_DECLINE_PCT
            )
            change_evidence_complete = (
                score_change is not None
                and all(value is not None for value in category_changes.values())
            )
            no_material_score_decline = (
                change_evidence_complete
                and score_change >= -MAX_SCORE_DECLINE_POINTS
                and all(value >= -MAX_SCORE_DECLINE_POINTS
                        for value in category_changes.values())
            )
            no_hard_veto = not bool(row.get("vetoes"))
            no_thesis_deterioration = bool(
                growth_qualified and no_hard_veto and no_material_score_decline
            )
            evidence_complete = bool(
                valuation.get("available") and prior_snapshot
                and stable_value is not None and change_evidence_complete
            )
            passes = bool(
                evidence_complete and stable_value and no_thesis_deterioration
            )
            reasons = []
            if not valuation.get("available"):
                reasons.append(valuation.get("reason") or "Conservative valuation is unavailable.")
            if not prior_snapshot:
                reasons.append(f"No comparable prior valuation within {lookback_days} days.")
            elif stable_value is False:
                reasons.append(
                    f"Conservative fair value fell more than {MAX_FAIR_VALUE_DECLINE_PCT:g}%."
                )
            if not growth_qualified:
                reasons.append("The point-in-time growth qualification did not pass.")
            if not no_hard_veto:
                reasons.append("A tested hard veto was present.")
            if not change_evidence_complete:
                reasons.append("Prior official score/category evidence is incomplete.")
            elif not no_material_score_decline:
                reasons.append(
                    f"An official score or key category fell more than {MAX_SCORE_DECLINE_POINTS:g} points."
                )

            row["entry_opportunity_factors"] = {
                "as_of": str(row.get("decision_date") or row.get("entry_date"))[:10],
                "valuation": valuation,
                "prior_snapshot_date": (
                    str(prior_row.get("decision_date") or prior_row.get("entry_date"))[:10]
                    if prior_row else None
                ),
                "prior_snapshot_days": prior_days,
                "prior_conservative_fair_value": (
                    prior_snapshot.get("conservative_fair_value")
                    if prior_snapshot else None
                ),
                "conservative_fair_value_change_pct": (
                    round(fair_change, 2) if fair_change is not None else None
                ),
                "official_score_change_points": score_change,
                "category_change_points": category_changes,
                "checks": {
                    "stable_business_value": stable_value,
                    "growth_qualified": growth_qualified,
                    "no_hard_veto": no_hard_veto,
                    "no_material_score_or_category_decline": no_material_score_decline,
                },
                "evidence_complete": evidence_complete,
                "stable_value_and_no_thesis_deterioration": passes,
                "reasons": reasons,
                "score_effect": 0,
                "verdict_effect": "none",
            }
            attached += 1
            stable += passes
            prior_rows.append((row, valuation))

    return {
        "observations": len(observations),
        "attached": attached,
        "stable_value_and_no_thesis_deterioration": stable,
        "lookback_days": lookback_days,
    }


def _outcomes(rows: list[dict]) -> dict:
    return {
        key: _target_summary(rows, target, horizon)
        for key, target, horizon in OUTCOME_CONTRACTS
    }


def _detail(row: dict) -> dict:
    factors = row.get("entry_opportunity_factors") or {}
    valuation = factors.get("valuation") or {}
    milestone = row.get("return_milestones") or {}
    first_hits = milestone.get("first_hit_days") or {}
    return {
        "ticker": row.get("ticker"),
        "company": row.get("company"),
        "issuer_key": row.get("issuer_key"),
        "strategy": row.get("strategy_key"),
        "earliest_qualifying_date": row.get("entry_date"),
        "entry_price": row.get("entry_price"),
        "official_score": row.get("score"),
        "conservative_fair_value": valuation.get("conservative_fair_value"),
        "conservative_upside_pct": valuation.get("conservative_upside_pct"),
        "relevant_method_count": valuation.get("method_count"),
        "valuation_spread_pct": valuation.get("dispersion_pct"),
        "prior_snapshot_date": factors.get("prior_snapshot_date"),
        "conservative_fair_value_change_pct": factors.get(
            "conservative_fair_value_change_pct"
        ),
        "official_score_change_points": factors.get("official_score_change_points"),
        "category_change_points": factors.get("category_change_points"),
        "checks": factors.get("checks"),
        "evidence_complete": factors.get("evidence_complete"),
        "stable_value_and_no_thesis_deterioration": factors.get(
            "stable_value_and_no_thesis_deterioration"
        ),
        "reasons": factors.get("reasons") or [],
        "days_to_30_pct": first_hits.get("30"),
        "days_to_50_pct": first_hits.get("50"),
        "days_to_100_pct": first_hits.get("100"),
    }


def run_entry_opportunity_research(
    observations: list[dict],
    *,
    step_days: int = 30,
    upside_thresholds: tuple[int, ...] = UPSIDE_THRESHOLDS_PCT,
) -> dict:
    """Backtest predeclared upside thresholds from the earliest official Buy."""
    enrichment = attach_entry_opportunity_factors(observations)
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _episode_roots(
        observations, {}, {"buy": 0, "watch": 0}, max_gap,
        use_recorded_verdict=True,
    )
    split = _split_dates(episodes, {"development_share": .60, "validation_share": .20})
    test_dates = split["test"] if split else set()
    final_episodes = _partition(episodes, test_dates) if split else []
    baseline_all = _outcomes(episodes)
    baseline_final = _outcomes(final_episodes)
    baseline_final_primary = baseline_final["primary_30_within_one_year"]

    def selected(rows: list[dict], threshold: int, stable_only: bool) -> list[dict]:
        output = []
        for row in rows:
            factors = row.get("entry_opportunity_factors") or {}
            valuation = factors.get("valuation") or {}
            upside = _number(valuation.get("conservative_upside_pct"))
            if upside is None or upside < threshold:
                continue
            if stable_only and not factors.get(
                "stable_value_and_no_thesis_deterioration"
            ):
                continue
            output.append(row)
        return output

    thresholds = []
    for threshold in upside_thresholds:
        upside_only = selected(episodes, threshold, False)
        stable_thesis = selected(episodes, threshold, True)
        final_upside_only = selected(final_episodes, threshold, False)
        final_stable = selected(final_episodes, threshold, True)
        all_upside_outcomes = _outcomes(upside_only)
        all_stable_outcomes = _outcomes(stable_thesis)
        final_upside_outcomes = _outcomes(final_upside_only)
        final_stable_outcomes = _outcomes(final_stable)
        final_primary = final_stable_outcomes["primary_30_within_one_year"]
        baseline_rate = _number(baseline_final_primary.get("success_rate_pct"))
        candidate_rate = _number(final_primary.get("success_rate_pct"))
        improvement = (
            round(candidate_rate - baseline_rate, 1)
            if candidate_rate is not None and baseline_rate is not None else None
        )
        baseline_drawdown = _number(
            baseline_final_primary.get("median_max_drawdown_pct")
        )
        candidate_drawdown = _number(final_primary.get("median_max_drawdown_pct"))
        enough_final_cases = final_primary.get("completed_episodes", 0) >= 30
        drawdown_protected = bool(
            baseline_drawdown is not None and candidate_drawdown is not None
            and candidate_drawdown >= baseline_drawdown - 2
        )
        research_signal = bool(
            enough_final_cases and improvement is not None and improvement >= 5
            and drawdown_protected
        )
        thresholds.append({
            "minimum_conservative_upside_pct": threshold,
            "all_history": {
                "upside_only": all_upside_outcomes,
                "stable_value_and_thesis": all_stable_outcomes,
            },
            "final_unseen": {
                "upside_only": final_upside_outcomes,
                "stable_value_and_thesis": final_stable_outcomes,
            },
            "final_unseen_primary_improvement_pp": improvement,
            "research_signal": research_signal,
            "research_status": (
                "Eligible for a separately frozen confirmation test"
                if research_signal else
                "Too few completed newest-period cases"
                if not enough_final_cases else
                "No reliable newest-period improvement"
            ),
        })

    exclusive = []
    bounds = list(upside_thresholds)
    for index, lower in enumerate(bounds):
        upper = bounds[index + 1] if index + 1 < len(bounds) else None
        rows = [
            row for row in episodes
            if (row.get("entry_opportunity_factors") or {}).get(
                "stable_value_and_no_thesis_deterioration"
            )
            and _number(((row.get("entry_opportunity_factors") or {})
                         .get("valuation") or {}).get("conservative_upside_pct")) is not None
            and float(row["entry_opportunity_factors"]["valuation"]
                      ["conservative_upside_pct"]) >= lower
            and (upper is None or float(row["entry_opportunity_factors"]["valuation"]
                                        ["conservative_upside_pct"]) < upper)
        ]
        exclusive.append({
            "minimum_pct": lower,
            "maximum_exclusive_pct": upper,
            "label": f"{lower}% to under {upper}%" if upper else f"{lower}% or more",
            "outcomes": _outcomes(rows),
        })

    details = [_detail(row) for row in episodes]
    coverage = {
        "earliest_buy_episodes": len(episodes),
        "with_controlled_conservative_valuation": sum(
            bool((row.get("entry_opportunity_factors") or {}).get("valuation", {}).get("available"))
            for row in episodes
        ),
        "with_prior_snapshot": sum(
            bool((row.get("entry_opportunity_factors") or {}).get("prior_snapshot_date"))
            for row in episodes
        ),
        "with_complete_stability_evidence": sum(
            bool((row.get("entry_opportunity_factors") or {}).get("evidence_complete"))
            for row in episodes
        ),
        "stable_value_and_no_thesis_deterioration": sum(
            bool((row.get("entry_opportunity_factors") or {}).get(
                "stable_value_and_no_thesis_deterioration"
            )) for row in episodes
        ),
    }
    return {
        "ok": True,
        "version": VERSION,
        "production_effect": "none",
        "one_official_score": True,
        "primary_contract": "+30% within 365 days from the earliest official Buy",
        "secondary_contracts": [
            "+50% within 730 days",
            "+100% within 1,095 days",
        ],
        "earliest_entry_rule": (
            "The first recorded official Buy in each continuous Buy episode starts the clock; "
            "later Buy signals do not restart it."
        ),
        "conservative_value_rule": (
            "Lowest fair value from at least two relevant point-in-time methods after "
            "sector-inappropriate P/B is removed; method spread must be 75% or less."
        ),
        "stability_rule": {
            "lookback_days": LOOKBACK_DAYS,
            "maximum_conservative_fair_value_decline_pct": MAX_FAIR_VALUE_DECLINE_PCT,
            "maximum_official_score_or_category_decline_points": MAX_SCORE_DECLINE_POINTS,
            "required_categories": list(CATEGORY_IDS),
            "requires_growth_qualification": True,
            "requires_no_hard_veto": True,
            "missing_evidence_policy": "exclude from the stable-thesis subset",
        },
        "chronological_split": {
            "development_pct": 60,
            "validation_pct": 20,
            "final_unseen_pct": 20,
            "final_unseen_dates": (
                [min(test_dates), max(test_dates)] if test_dates else None
            ),
        },
        "coverage": coverage,
        "enrichment": enrichment,
        "baseline": {"all_history": baseline_all, "final_unseen": baseline_final},
        "thresholds": thresholds,
        "exclusive_bands": exclusive,
        "episode_details": details,
        "research_conclusion": (
            "At least one predeclared band produced a research signal for a separately frozen test; production remains unchanged."
            if any(row["research_signal"] for row in thresholds) else
            "No conservative-upside band reliably improved the newest unseen +30%-within-one-year result."
        ),
        "interpretation": (
            "These are predeclared research comparisons. A larger estimated upside is not "
            "treated as a higher probability unless the chronological unseen results confirm it."
        ),
    }
