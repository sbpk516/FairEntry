"""Research-only gate based on reported three-year TTM diluted-EPS growth."""
from __future__ import annotations

from numbers import Number


def ttm_eps_growth(current_ttm, prior_ttm, years: int = 3) -> dict:
    """Classify and, when valid, calculate reported diluted-EPS growth."""
    current = float(current_ttm) if isinstance(current_ttm, Number) else None
    prior = float(prior_ttm) if isinstance(prior_ttm, Number) else None
    if current is None or prior is None:
        return {"cagr_pct": None, "state": "missing"}
    if current > 0 and prior <= 0:
        return {"cagr_pct": None, "state": "recovery"}
    if current <= 0 and prior > 0:
        return {"cagr_pct": None, "state": "deterioration"}
    if current <= 0 and prior <= 0:
        return {"cagr_pct": None, "state": "non_positive"}
    cagr = ((current / prior) ** (1 / years) - 1) * 100
    return {"cagr_pct": round(cagr, 4),
            "state": "growth" if cagr >= 0 else "deterioration"}


def attach_eps_ttm_factors(observations: list[dict], connection) -> dict:
    """Attach exact point-in-time TTM diluted-EPS factors in one lean query."""
    rows = [row for row in observations
            if row.get("ticker") and row.get("decision_date")]
    if not rows:
        return {"observations": 0, "enriched": 0}
    import pandas as pd

    frame = pd.DataFrame({
        "observation_id": [row["observation_id"] for row in rows],
        "ticker": [row["ticker"] for row in rows],
        "decision_date": [row["decision_date"] for row in rows],
    })
    connection.register("eps_observations", frame)
    try:
        results = connection.execute("""
        WITH available AS (
          SELECT o.observation_id,f.datekey,f.reportperiod,f.calendardate,f.epsdil,
                 row_number() OVER (
                   PARTITION BY o.observation_id,coalesce(f.calendardate,f.reportperiod)
                   ORDER BY f.datekey DESC,f.reportperiod DESC
                 ) AS revision_rank
          FROM eps_observations o
          JOIN sfa_fundamentals f
            ON f.ticker=o.ticker AND f.dimension='ARQ'
           AND f.datekey<=CAST(o.decision_date AS DATE)
        ), history AS (
          SELECT *,
                 CASE WHEN count(epsdil) OVER w4=4
                      THEN sum(epsdil) OVER w4 END AS current_ttm,
                 CASE WHEN count(epsdil) OVER w4_3y=4
                      THEN sum(epsdil) OVER w4_3y END AS prior_ttm,
                 row_number() OVER (
                   PARTITION BY observation_id
                   ORDER BY coalesce(calendardate,reportperiod) DESC,datekey DESC
                 ) AS latest_rank
          FROM available WHERE revision_rank=1
          WINDOW
            w4 AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey ROWS BETWEEN 3 PRECEDING AND CURRENT ROW),
            w4_3y AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey ROWS BETWEEN 15 PRECEDING AND 12 PRECEDING)
        )
        SELECT observation_id,current_ttm,prior_ttm
        FROM history WHERE latest_rank=1
        """).fetchall()
    finally:
        connection.unregister("eps_observations")

    by_id = {observation_id: (current, prior)
             for observation_id, current, prior in results}
    enriched = 0
    states: dict[str, int] = {}
    for row in rows:
        current, prior = by_id.get(row["observation_id"], (None, None))
        result = ttm_eps_growth(current, prior)
        factors = row.setdefault("research_factors", {})
        factors.update({
            "eps_ttm_diluted": float(current) if current is not None else None,
            "eps_ttm_diluted_3y_ago": float(prior) if prior is not None else None,
            "eps_cagr_3y_pct": result["cagr_pct"],
            "eps_growth_state": result["state"],
            "eps_recovery": result["state"] == "recovery",
            "eps_deterioration": result["state"] == "deterioration",
        })
        states[result["state"]] = states.get(result["state"], 0) + 1
        enriched += current is not None or prior is not None
    return {"observations": len(rows), "enriched": enriched, "states": states}


def eps_cagr_3y(row: dict) -> float | None:
    """Return point-in-time three-year TTM diluted-EPS CAGR, when calculable."""
    value = (row.get("research_factors") or {}).get("eps_cagr_3y_pct")
    if isinstance(value, bool) or not isinstance(value, Number):
        return None
    return float(value)


def verdict(row: dict, minimum_cagr_pct: float) -> str:
    """Cap an existing Buy to Watch unless reported EPS clears the gate.

    This challenger never promotes a stock and never changes Avoid to Watch.
    A missing CAGR fails the experimental gate because the screen cannot verify
    durable growth. Production verdicts are not mutated.
    """
    baseline = row.get("verdict") or "Unknown"
    if baseline != "Buy":
        return baseline
    growth = eps_cagr_3y(row)
    return "Buy" if growth is not None and growth >= minimum_cagr_pct else "Watch"


def eps_growth_score(row: dict) -> float | None:
    """Map the agreed TTM EPS-growth bands to a restrained 0-100 score."""
    factors = row.get("research_factors") or {}
    growth = eps_cagr_3y(row)
    state = factors.get("eps_growth_state")
    if growth is None:
        return 0.0 if state in {"deterioration", "non_positive"} else None
    if growth >= 20:
        return 100.0
    if growth >= 10:
        return 75.0
    if growth >= 5:
        return 50.0
    if growth >= 0:
        return 25.0
    return 0.0


def score_challenger(row: dict, eps_weight_pct: float = 20) -> dict:
    """Blend reported EPS into Growth and recompute the tested verdict."""
    categories = {item.get("id"): item.get("score")
                  for item in row.get("categories", [])}
    production_growth = categories.get("growth")
    eps_score = eps_growth_score(row)
    if not isinstance(production_growth, Number):
        new_growth = None
    elif eps_score is None:
        new_growth = float(production_growth)
    else:
        share = max(0.0, min(100.0, float(eps_weight_pct))) / 100
        new_growth = (1 - share) * float(production_growth) + share * eps_score
    categories["growth"] = new_growth

    configured_weights = row.get("weights") or {
        item.get("id"): item.get("weight") for item in row.get("categories", [])
    }
    available = {
        key: (float(configured_weights[key]), float(value))
        for key, value in categories.items()
        if isinstance(value, Number) and key in configured_weights
        and isinstance(configured_weights[key], Number)
        and float(configured_weights[key]) > 0
    }
    denominator = sum(weight for weight, _ in available.values())
    score = (round(sum(weight * value for weight, value in available.values())
                   / denominator, 1) if denominator else 0.0)
    band = "Buy" if score >= 72 else "Watch" if score >= 50 else "Avoid"
    final = ("Avoid" if row.get("vetoes") else
             "Watch" if band == "Buy" and row.get("soft_gates") else band)
    return {
        "production_effect": "none",
        "eps_weight_within_growth_pct": eps_weight_pct,
        "eps_score": eps_score,
        "production_growth_score": float(production_growth)
        if isinstance(production_growth, Number) else None,
        "challenger_growth_score": round(new_growth, 1)
        if new_growth is not None else None,
        "score": score,
        "verdict": final,
    }
