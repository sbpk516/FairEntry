"""Factor-level Buy success comparison with rolling walk-forward exploration.

Only ratios and signals frozen on or before each Buy date are used. Thresholds
are learned from older episodes and evaluated on the next chronological fold.
The explorer is research-only: its changing fold rules are not deployable and
can never alter production scoring.
"""
from __future__ import annotations

import math
import statistics

from fairentry.analytics.demand_momentum import _SECTOR_ETF
from fairentry.backtest.evidence import _fixed_horizon_evaluation
from fairentry.backtest.eps_growth_challenger import ttm_eps_growth
from fairentry.backtest.research_cycle import _number, _target_summary, factor_value
from fairentry.backtest.sfa_tune import _episode_roots


FACTOR_DEFINITIONS = (
    {
        "id": "model_score",
        "label": "Overall FairEntry score",
        "field": "score",
        "direction": "higher",
        "unit": "/100",
        "theme": "production_model",
        "meaning": "The complete deterministic score recorded on the first Buy date.",
    },
    *(
        {
            "id": f"category_{category_id}",
            "label": f"{label} category score",
            "field": f"category.{category_id}",
            "direction": "higher",
            "unit": "/100",
            "theme": "production_model",
            "meaning": f"The recorded point-in-time {label} category score.",
        }
        for category_id, label in (
            ("quality", "Business Quality"),
            ("survival", "Financial Strength"),
            ("growth", "Growth"),
            ("valuation", "Valuation"),
            ("confirmation", "Market Confirmation"),
        )
    ),
    {
        "id": "growth_deceleration",
        "label": "Revenue growth acceleration / deceleration",
        "field": "research.revenue_growth_change_pp",
        "direction": "higher",
        "unit": "percentage points",
        "theme": "growth_deceleration",
        "meaning": "Current year-over-year quarterly revenue growth minus the prior-year growth rate; negative means deceleration.",
    },
    {
        "id": "revenue_growth_consistency",
        "label": "Revenue-growth consistency",
        "field": "research.revenue_growth_positive_quarters_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "durable_growth",
        "meaning": "Share of the latest four reported quarters whose revenue exceeded the same quarter one year earlier.",
    },
    {
        "id": "revenue_growth_stability",
        "label": "Revenue-growth volatility",
        "field": "research.revenue_growth_volatility_pct",
        "direction": "lower",
        "unit": "percentage points",
        "theme": "durable_growth",
        "meaning": "Variation in year-over-year revenue growth across the latest four reported quarters; lower is steadier.",
    },
    {
        "id": "revenue_growth_floor",
        "label": "Weakest recent revenue growth",
        "field": "research.revenue_growth_min_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "durable_growth",
        "meaning": "Lowest year-over-year revenue growth rate among the latest four reported quarters.",
    },
    {
        "id": "positive_eps_consistency",
        "label": "Positive-EPS consistency",
        "field": "research.positive_eps_quarters_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "earnings_consistency",
        "meaning": "Share of the latest eight reported quarters with positive diluted earnings per share.",
    },
    {
        "id": "improving_eps_consistency",
        "label": "Improving-EPS consistency",
        "field": "research.eps_improving_quarters_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "earnings_consistency",
        "meaning": "Share of the latest four quarters whose diluted EPS exceeded the same quarter one year earlier.",
    },
    {
        "id": "eps_growth_3y_ttm",
        "label": "Three-year TTM diluted-EPS CAGR",
        "field": "research.eps_cagr_3y_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "earnings_growth",
        "meaning": "CAGR of reported trailing-12-month diluted EPS versus the comparable TTM period three years earlier; calculated only when both totals are positive.",
    },
    {
        "id": "margin_direction",
        "label": "Operating-margin direction",
        "field": "research.operating_margin_change_qoq_pp",
        "direction": "higher",
        "unit": "percentage points",
        "theme": "margin_direction",
        "meaning": "Current quarterly operating margin minus the preceding quarter's operating margin.",
    },
    {
        "id": "cash_flow_quality",
        "label": "Free-cash-flow margin",
        "field": "research.fcf_margin_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "cash_flow_quality",
        "meaning": "Trailing free cash flow divided by trailing revenue, using the filing available on the Buy date.",
    },
    {
        "id": "net_profit_margin",
        "label": "Net profit margin",
        "field": "research.net_profit_margin_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "profitability",
        "meaning": "Trailing net income divided by trailing revenue from the filing available on the Buy date.",
    },
    {
        "id": "net_profit_margin_trend",
        "label": "Net profit-margin direction",
        "field": "research.net_profit_margin_change_yoy_pp",
        "direction": "higher",
        "unit": "percentage points",
        "theme": "profitability",
        "meaning": "Current trailing net profit margin minus the comparable trailing margin one year earlier; positive means improving.",
    },
    {
        "id": "gross_profitability",
        "label": "Gross profitability on assets",
        "field": "research.gross_profitability_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "business_quality",
        "category": "Business Quality",
        "meaning": "Trailing gross profit divided by total assets; a point-in-time measure of the business engine's productivity.",
    },
    {
        "id": "cash_conversion",
        "label": "Operating-cash conversion",
        "field": "research.cash_conversion_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "business_quality",
        "category": "Business Quality",
        "meaning": "Trailing operating cash flow divided by positive net income; higher means reported earnings are better supported by cash.",
    },
    {
        "id": "accrual_quality",
        "label": "Accruals relative to assets",
        "field": "research.accruals_to_assets_pct",
        "direction": "lower",
        "unit": "%",
        "theme": "business_quality",
        "category": "Business Quality",
        "meaning": "Trailing net income minus operating cash flow, divided by assets; lower means less reliance on non-cash earnings.",
    },
    {
        "id": "return_on_invested_capital",
        "label": "Return on invested capital",
        "field": "research.roic_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "business_quality",
        "category": "Business Quality",
        "meaning": "Trailing point-in-time ROIC from the filing warehouse; higher can indicate a more durable compounding business.",
    },
    {
        "id": "research_intensity",
        "label": "Research and development intensity",
        "field": "research.rnd_to_revenue_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "capability_investment",
        "category": "Capability Moat",
        "meaning": "Trailing R&D divided by trailing revenue. Spending is evidence of capability investment, not proof that the investment is productive.",
    },
    {
        "id": "capital_intensity",
        "label": "Capital expenditure intensity",
        "field": "research.capex_to_revenue_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "capability_investment",
        "category": "Capability Moat",
        "meaning": "Absolute trailing capital expenditure divided by trailing revenue. It is paired with return measures so heavy spending alone is not rewarded.",
    },
    {
        "id": "net_debt_to_fcf",
        "label": "Net debt to free cash flow",
        "field": "research.net_debt_to_fcf",
        "direction": "lower",
        "unit": "x",
        "theme": "financial_strength",
        "category": "Financial Strength",
        "meaning": "Debt minus cash divided by positive trailing free cash flow; lower indicates more capacity to absorb stress.",
    },
    {
        "id": "interest_coverage",
        "label": "Interest coverage",
        "field": "research.interest_coverage",
        "direction": "higher",
        "unit": "x",
        "theme": "financial_strength",
        "category": "Financial Strength",
        "meaning": "Trailing EBIT divided by absolute interest expense; higher means a larger operating cushion over financing costs.",
    },
    {
        "id": "valuation_to_growth",
        "label": "P/E relative to revenue growth",
        "field": "research.pe_to_revenue_growth",
        "direction": "lower",
        "unit": "x",
        "theme": "valuation_to_growth",
        "meaning": "Point-in-time trailing P/E divided by positive year-over-year revenue growth; lower is less demanding.",
    },
    {
        "id": "trailing_pe",
        "label": "Trailing P/E",
        "field": "research.trailing_pe",
        "direction": "lower",
        "unit": "x",
        "theme": "valuation_extremes",
        "meaning": "Trailing price/earnings ratio recorded on or before the Buy date.",
    },
    {
        "id": "price_to_sales",
        "label": "Price/sales",
        "field": "research.price_to_sales",
        "direction": "lower",
        "unit": "x",
        "theme": "valuation_extremes",
        "meaning": "Price/sales ratio recorded on or before the Buy date.",
    },
    {
        "id": "price_to_fcf",
        "label": "Price/free cash flow",
        "field": "metric.pfcf_ratio",
        "direction": "lower",
        "unit": "x",
        "theme": "valuation_extremes",
        "meaning": "Point-in-time market value divided by trailing free cash flow.",
    },
    {
        "id": "intrinsic_value_gap",
        "label": "Estimated value upside",
        "field": "valuation.upside_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "valuation_extremes",
        "meaning": "FairEntry's frozen point-in-time upside estimate on the original Buy date.",
    },
    {
        "id": "dilution",
        "label": "Share-count change",
        "field": "research.share_count_change_yoy_pct",
        "direction": "lower",
        "unit": "%",
        "theme": "balance_sheet_discipline",
        "meaning": "Basic shares outstanding versus the same quarter one year earlier; lower means less dilution.",
    },
    {
        "id": "debt_change",
        "label": "Debt burden change",
        "field": "research.debt_to_assets_change_yoy_pp",
        "direction": "lower",
        "unit": "percentage points",
        "theme": "balance_sheet_discipline",
        "meaning": "Long-term debt as a share of assets versus the same quarter one year earlier; lower means improving.",
    },
    {
        "id": "market_return",
        "label": "Market three-month return",
        "field": "research.market_return_3m_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "market_conditions",
        "meaning": "SPY's trailing three-month return available on the Buy date.",
    },
    {
        "id": "sector_return",
        "label": "Sector three-month return",
        "field": "research.sector_return_3m_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "sector_conditions",
        "meaning": "The sector ETF's trailing three-month return available on the Buy date.",
    },
    {
        "id": "sector_vs_market",
        "label": "Sector return versus market",
        "field": "research.sector_minus_market_3m_pct",
        "direction": "higher",
        "unit": "percentage points",
        "theme": "sector_conditions",
        "meaning": "Sector ETF three-month return minus SPY's return on the Buy date.",
    },
    {
        "id": "market_trend",
        "label": "Market trend",
        "field": "research.market_trend_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "market_conditions",
        "meaning": "Share of SPY price-above-50DMA, price-above-200DMA and 50DMA-above-200DMA checks passing.",
    },
    {
        "id": "sector_trend",
        "label": "Sector trend",
        "field": "research.sector_trend_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "sector_conditions",
        "meaning": "Share of the sector ETF's principal moving-average trend checks passing.",
    },
    {
        "id": "relative_strength",
        "label": "Relative strength",
        "field": "metric.relative_strength_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "market_confirmation",
        "meaning": "Three-month stock performance relative to its sector benchmark and SPY.",
    },
    {
        "id": "trend_regime",
        "label": "Trend regime",
        "field": "metric.trend_regime_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "market_confirmation",
        "meaning": "Price and 50/200-day moving-average trend checks available on the Buy date.",
    },
    {
        "id": "entry_extension",
        "label": "Entry extension from 50DMA",
        "field": "research.price_to_sma50_abs_pct",
        "direction": "lower",
        "unit": "%",
        "theme": "entry_timing",
        "meaning": "Absolute distance between entry-date price and its 50-day moving average; lower means less extended.",
    },
    {
        "id": "long_term_trend",
        "label": "Price above 200DMA",
        "field": "research.price_above_sma200_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "entry_timing",
        "meaning": "Entry-date price distance above or below its 200-day moving average.",
    },
    {
        "id": "breakout_price",
        "label": "Breakout-price confirmation",
        "field": "metric.breakout_price_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "entry_timing",
        "meaning": "Historical resistance-breakout score frozen on the Buy date.",
    },
    {
        "id": "breakout_volume",
        "label": "Breakout-volume confirmation",
        "field": "metric.breakout_volume_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "entry_timing",
        "meaning": "Entry-date trading volume relative to the prior 50-session average.",
    },
)

UNAVAILABLE_FACTORS = (
    {
        "id": "analyst_estimate_revisions",
        "label": "Analyst estimate revisions",
        "status": "not_backtestable",
        "category": "Catalysts & Narrative",
        "reason": "The SFA warehouse has actual filings but no complete point-in-time analyst-estimate revision history.",
        "production_effect": "none",
    },
    {
        "id": "expected_next_year_eps_growth",
        "label": "Expected next-year EPS growth",
        "status": "not_backtestable",
        "category": "Growth & Operating Momentum",
        "reason": "The replay does not contain a historical as-of series for next-year analyst EPS estimates.",
        "production_effect": "none",
    },
)

COMBINATION_TEMPLATES = (
    ("earnings_revenue_consistency", "Consistent earnings and revenue",
     ("positive_eps_consistency", "revenue_growth_consistency")),
    ("stable_growth_margin", "Stable growth with improving margins",
     ("revenue_growth_stability", "margin_direction")),
    ("growth_cash_value", "Durable growth, cash flow and valuation",
     ("revenue_growth_consistency", "cash_flow_quality", "valuation_to_growth")),
    ("balance_sheet_discipline", "Low dilution and improving debt",
     ("dilution", "debt_change")),
    ("value_not_extended", "Reasonable valuation without an extended entry",
     ("trailing_pe", "entry_extension")),
    ("sector_market_confirmation", "Supportive sector and market",
     ("sector_return", "market_return", "relative_strength")),
    ("quality_entry", "Consistent earnings with disciplined entry timing",
     ("positive_eps_consistency", "margin_direction", "entry_extension")),
    ("quality_cash_discipline", "Productive business with cash-backed earnings",
     ("gross_profitability", "cash_conversion", "accrual_quality")),
    ("resilient_balance_sheet", "Cash-supported balance-sheet resilience",
     ("net_debt_to_fcf", "interest_coverage", "dilution")),
    ("quality_growth_entry", "Quality business, stable growth and disciplined entry",
     ("gross_profitability", "revenue_growth_stability", "entry_extension")),
)

DEFAULT_POLICY = {
    "folds": 5,
    "minimum_training_completed": 40,
    "minimum_training_unique_issuers": 25,
    "minimum_fold_completed": 10,
    "minimum_total_oos_completed": 50,
    "minimum_oos_improvement_pp": 5.0,
    "minimum_oos_success_rate_pct": 55.0,
    "maximum_drawdown_deterioration_pp": 2.0,
    "inner_validation_share": 0.25,
    "minimum_inner_validation_completed": 10,
    "require_nonnegative_inner_improvement": True,
    "quantiles": (0.25, 0.50, 0.75),
}


def _ratio(numerator, denominator, scale=1.0):
    a, b = _number(numerator), _number(denominator)
    return a / b * scale if a is not None and b not in {None, 0} else None


def attach_warehouse_factors(observations: list[dict], connection) -> dict:
    """Attach derived as-of-date ratios to Buy observations in one warehouse query."""
    # Enrich every scored observation. The factor explorer below still studies
    # the production Buy episodes, while separately versioned challengers need
    # the same point-in-time inputs for Watch/Avoid observations too.
    rows = [row for row in observations
            if row.get("ticker") and row.get("decision_date")]
    if not rows:
        return {"observations": 0, "enriched": 0}
    import pandas as pd

    frame = pd.DataFrame({
        "observation_id": [row["observation_id"] for row in rows],
        "ticker": [row["ticker"] for row in rows],
        "decision_date": [row["decision_date"] for row in rows],
        "sector_etf": [_SECTOR_ETF.get(row.get("sector")) for row in rows],
    })
    fundamental_columns = {
        str(column[1]).lower()
        for column in connection.execute("PRAGMA table_info('sfa_fundamentals')").fetchall()
    }
    rnd_expression = "f.rnd" if "rnd" in fundamental_columns else "NULL"
    capex_expression = "f.capex" if "capex" in fundamental_columns else "NULL"
    connection.register("factor_observations", frame)
    try:
        result = connection.execute(f"""
        WITH available_arq AS (
          SELECT o.observation_id,f.ticker,f.datekey,f.reportperiod,f.calendardate,
                 f.revenue,f.opinc,f.epsdil,f.sharesbas,f.debtnc,f.assets,
                 row_number() OVER (
                   PARTITION BY o.observation_id,coalesce(f.calendardate,f.reportperiod)
                   ORDER BY f.datekey DESC,f.reportperiod DESC
                 ) AS revision_rank
          FROM factor_observations o
          JOIN sfa_fundamentals f
            ON f.ticker=o.ticker AND f.dimension='ARQ'
           AND f.datekey<=CAST(o.decision_date AS DATE)
        ), arq_lags AS (
          SELECT observation_id,ticker,datekey,reportperiod,calendardate,
                 revenue,opinc,epsdil,sharesbas,debtnc,assets,
                 lag(revenue) OVER w AS revenue_prev_q,
                 lag(revenue,4) OVER w AS revenue_prev_y,
                 lag(revenue,8) OVER w AS revenue_prev_2y,
                 lag(opinc) OVER w AS opinc_prev_q,
                 lag(epsdil,4) OVER w AS epsdil_prev_y,
                 lag(epsdil,8) OVER w AS epsdil_prev_2y,
                 lag(epsdil,12) OVER w AS epsdil_prev_3y,
                 lag(sharesbas,4) OVER w AS sharesbas_prev_y,
                 lag(debtnc,4) OVER w AS debtnc_prev_y,
                 lag(assets,4) OVER w AS assets_prev_y
          FROM available_arq WHERE revision_rank=1
          WINDOW w AS (
            PARTITION BY observation_id
            ORDER BY coalesce(calendardate,reportperiod),datekey
          )
        ), arq_rates AS (
          SELECT *,
                 CASE WHEN revenue_prev_y>0
                      THEN (revenue-revenue_prev_y)*100.0/revenue_prev_y END AS revenue_growth_yoy_pct,
                 CASE WHEN epsdil_prev_y>0
                      THEN (epsdil-epsdil_prev_y)*100.0/epsdil_prev_y END AS eps_growth_yoy_pct,
                 CASE WHEN epsdil_prev_2y>0
                      THEN (epsdil_prev_y-epsdil_prev_2y)*100.0/epsdil_prev_2y END AS prior_eps_growth_yoy_pct
          FROM arq_lags
        ), arq_history AS (
          SELECT *,
                 avg(CASE WHEN revenue IS NULL OR revenue_prev_y IS NULL THEN NULL
                          WHEN revenue>revenue_prev_y THEN 100.0 ELSE 0.0 END)
                   OVER w4 AS revenue_growth_positive_quarters_pct,
                 stddev_samp(revenue_growth_yoy_pct) OVER w4 AS revenue_growth_volatility_pct,
                 min(revenue_growth_yoy_pct) OVER w4 AS revenue_growth_min_pct,
                 avg(CASE WHEN epsdil IS NULL THEN NULL
                          WHEN epsdil>0 THEN 100.0 ELSE 0.0 END) OVER w8 AS positive_eps_quarters_pct,
                 avg(CASE WHEN epsdil IS NULL OR epsdil_prev_y IS NULL THEN NULL
                          WHEN epsdil>epsdil_prev_y THEN 100.0 ELSE 0.0 END)
                   OVER w4 AS eps_improving_quarters_pct,
                 CASE WHEN count(epsdil) OVER w4=4
                      THEN sum(epsdil) OVER w4 END AS eps_ttm_diluted,
                 CASE WHEN count(epsdil) OVER w4_3y=4
                      THEN sum(epsdil) OVER w4_3y END AS eps_ttm_diluted_prev_3y,
                 row_number() OVER (
                   PARTITION BY observation_id
                   ORDER BY coalesce(calendardate,reportperiod) DESC,datekey DESC
                 ) AS latest_rank
          FROM arq_rates
          WINDOW
            w4 AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey ROWS BETWEEN 3 PRECEDING AND CURRENT ROW),
            w4_3y AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey ROWS BETWEEN 15 PRECEDING AND 12 PRECEDING),
            w8 AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey ROWS BETWEEN 7 PRECEDING AND CURRENT ROW)
        ), available_art AS (
          SELECT o.observation_id,f.ticker,f.datekey,f.reportperiod,f.calendardate,
                 f.revenue,f.opinc,f.fcf,f.gp,f.assets,
                 {rnd_expression} AS rnd,{capex_expression} AS capex,
                 f.ncfo,f.netinc,f.debt,f.cashneq,f.ebit,f.intexp,f.roic,
                 row_number() OVER (
                   PARTITION BY o.observation_id,coalesce(f.calendardate,f.reportperiod)
                   ORDER BY f.datekey DESC,f.reportperiod DESC
                 ) AS revision_rank
          FROM factor_observations o
          JOIN sfa_fundamentals f
            ON f.ticker=o.ticker AND f.dimension='ART'
           AND f.datekey<=CAST(o.decision_date AS DATE)
        ), art_history AS (
          SELECT *,
                 lag(revenue,4) OVER w AS revenue_prev_y,
                 lag(revenue,12) OVER w AS revenue_prev_3y,
                 lag(fcf,4) OVER w AS fcf_prev_y,
                 lag(fcf,12) OVER w AS fcf_prev_3y,
                 lag(roic,12) OVER w AS roic_prev_3y,
                 lag(opinc,4) OVER w AS opinc_prev_y,
                 lag(netinc,4) OVER w AS netinc_prev_y,
                 median(roic) OVER w20 AS roic_5y_median,
                 stddev_samp(roic) OVER w20 AS roic_5y_stddev,
                 count(roic) OVER w20 AS roic_5y_observations,
                 avg(CASE WHEN fcf IS NULL THEN NULL WHEN fcf>0 THEN 100.0 ELSE 0.0 END)
                   OVER w12 AS positive_fcf_history_pct,
                 count(fcf) OVER w12 AS fcf_history_observations,
                 row_number() OVER (
                   PARTITION BY observation_id
                   ORDER BY coalesce(calendardate,reportperiod) DESC,datekey DESC
                 ) AS latest_rank
          FROM available_art WHERE revision_rank=1
          WINDOW
            w AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey),
            w12 AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey ROWS BETWEEN 11 PRECEDING AND CURRENT ROW),
            w20 AS (PARTITION BY observation_id ORDER BY coalesce(calendardate,reportperiod),datekey ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
        ), benchmark_history AS (
          SELECT o.observation_id,'sector' AS kind,p.date,p.closeadj
          FROM factor_observations o
          JOIN sfa_fund_prices p ON p.ticker=o.sector_etf
           AND p.date<=CAST(o.decision_date AS DATE)
           AND p.date>=CAST(o.decision_date AS DATE)-INTERVAL '400 days'
          UNION ALL
          SELECT o.observation_id,'market' AS kind,p.date,p.closeadj
          FROM factor_observations o
          JOIN sfa_fund_prices p ON p.ticker='SPY'
           AND p.date<=CAST(o.decision_date AS DATE)
           AND p.date>=CAST(o.decision_date AS DATE)-INTERVAL '400 days'
        ), benchmark_ranked AS (
          SELECT *,row_number() OVER (
            PARTITION BY observation_id,kind ORDER BY date DESC
          ) AS recency_rank
          FROM benchmark_history
        ), benchmark_features AS (
          SELECT observation_id,kind,
                 max(CASE WHEN recency_rank=1 THEN closeadj END) AS close,
                 max(CASE WHEN recency_rank=64 THEN closeadj END) AS close_3m,
                 avg(CASE WHEN recency_rank<=50 THEN closeadj END) AS sma50,
                 avg(CASE WHEN recency_rank<=200 THEN closeadj END) AS sma200
          FROM benchmark_ranked WHERE recency_rank<=200
          GROUP BY observation_id,kind
        )
        SELECT o.observation_id,
               arq.revenue,arq.revenue_prev_q,arq.revenue_prev_y,arq.revenue_prev_2y,
               arq.opinc,arq.opinc_prev_q,
               arq.epsdil,arq.epsdil_prev_y,arq.epsdil_prev_3y,
               arq.eps_ttm_diluted,arq.eps_ttm_diluted_prev_3y,
               arq.eps_growth_yoy_pct,arq.prior_eps_growth_yoy_pct,
               arq.positive_eps_quarters_pct,arq.eps_improving_quarters_pct,
               arq.revenue_growth_positive_quarters_pct,
               arq.revenue_growth_volatility_pct,arq.revenue_growth_min_pct,
               arq.sharesbas,arq.sharesbas_prev_y,
               arq.debtnc,arq.assets,arq.debtnc_prev_y,arq.assets_prev_y,
               art.revenue art_revenue,art.revenue_prev_y art_revenue_prev_y,
               art.revenue_prev_3y art_revenue_prev_3y,
               art.fcf,art.fcf_prev_y,art.fcf_prev_3y,
               art.opinc art_opinc,art.gp,art.assets art_assets,
               art.rnd,art.capex,
               art.ncfo,art.netinc,art.netinc_prev_y,
               art.debt,art.cashneq,art.ebit,art.intexp,art.roic,
               art.roic_prev_3y,art.roic_5y_median,art.roic_5y_stddev,
               art.roic_5y_observations,art.positive_fcf_history_pct,
               art.fcf_history_observations,art.opinc_prev_y,
               daily.pe,daily.ps,daily.pb,
               own.close,own.sma50,own.sma200,
               sector.close,sector.close_3m,sector.sma50,sector.sma200,
               market.close,market.close_3m,market.sma50,market.sma200
        FROM factor_observations o
        LEFT JOIN LATERAL (
          SELECT * FROM arq_history a
          WHERE a.observation_id=o.observation_id AND a.latest_rank=1
        ) arq ON true
        LEFT JOIN LATERAL (
          SELECT * FROM art_history a
          WHERE a.observation_id=o.observation_id AND a.latest_rank=1
        ) art ON true
        LEFT JOIN LATERAL (
          SELECT pe,ps,pb FROM sfa_daily d
          WHERE d.ticker=o.ticker AND d.date<=CAST(o.decision_date AS DATE)
          ORDER BY d.date DESC LIMIT 1
        ) daily ON true
        LEFT JOIN LATERAL (
          SELECT close,sma50,sma200 FROM sfa_price_features p
          WHERE p.ticker=o.ticker AND p.date<=CAST(o.decision_date AS DATE)
          ORDER BY p.date DESC LIMIT 1
        ) own ON true
        LEFT JOIN benchmark_features sector
          ON sector.observation_id=o.observation_id AND sector.kind='sector'
        LEFT JOIN benchmark_features market
          ON market.observation_id=o.observation_id AND market.kind='market'
        """).fetchall()
    finally:
        connection.unregister("factor_observations")
    by_id = {}
    for (observation_id, revenue, revenue_prev_q, revenue_prev_y, revenue_prev_2y,
         opinc, opinc_prev_q, epsdil, epsdil_prev_y, epsdil_prev_3y,
         eps_ttm_diluted, eps_ttm_diluted_prev_3y,
         eps_growth_yoy, prior_eps_growth_yoy,
         positive_eps_quarters, eps_improving_quarters,
         revenue_positive_quarters, revenue_growth_volatility, revenue_growth_min,
         sharesbas, sharesbas_prev_y, debtnc, assets, debtnc_prev_y, assets_prev_y,
         art_revenue, art_revenue_prev_y, art_revenue_prev_3y,
         fcf, fcf_prev_y, fcf_prev_3y, art_opinc, gp, art_assets, rnd, capex,
         ncfo, netinc, netinc_prev_y,
         debt, cashneq, ebit, intexp, roic, roic_prev_3y, roic_5y_median,
         roic_5y_stddev, roic_5y_observations, positive_fcf_history,
         fcf_history_observations, opinc_prev_y, pe, ps, pb,
         own_close, own_sma50, own_sma200,
         sector_close, sector_close_3m, sector_sma50, sector_sma200,
         market_close, market_close_3m, market_sma50, market_sma200) in result:
        revenue_number = _number(revenue)
        revenue_prev_y_number = _number(revenue_prev_y)
        revenue_prev_2y_number = _number(revenue_prev_2y)
        current_growth = (_ratio(revenue_number - revenue_prev_y_number,
                                 revenue_prev_y_number, 100)
                          if None not in (revenue_number, revenue_prev_y_number) else None)
        prior_growth = (_ratio(revenue_prev_y_number - revenue_prev_2y_number,
                               revenue_prev_2y_number, 100)
                        if None not in (revenue_prev_y_number, revenue_prev_2y_number) else None)
        current_margin = _ratio(opinc, revenue, 100)
        prior_margin = _ratio(opinc_prev_q, revenue_prev_q, 100)
        annual_margin = _ratio(opinc_prev_y, art_revenue_prev_y, 100)
        fcf_margin = _ratio(fcf, art_revenue, 100)
        gross_margin = _ratio(gp, art_revenue, 100)
        gross_profitability = _ratio(gp, art_assets, 100)
        rnd_intensity = _ratio(rnd, art_revenue, 100)
        capex_number = _number(capex)
        capex_intensity = _ratio(abs(capex_number), art_revenue, 100) \
            if capex_number is not None else None
        netinc_number = _number(netinc)
        ncfo_number = _number(ncfo)
        net_profit_margin = _ratio(netinc_number, art_revenue, 100)
        prior_net_profit_margin = _ratio(netinc_prev_y, art_revenue_prev_y, 100)
        net_profit_margin_change = (
            net_profit_margin - prior_net_profit_margin
            if None not in (net_profit_margin, prior_net_profit_margin) else None
        )
        cash_conversion = (_ratio(ncfo_number, netinc_number, 100)
                           if netinc_number is not None and netinc_number > 0 else None)
        accruals_to_assets = (
            _ratio(netinc_number - ncfo_number, art_assets, 100)
            if None not in (netinc_number, ncfo_number) else None
        )
        fcf_number = _number(fcf)
        debt_number = _number(debt)
        cash_number = _number(cashneq)
        net_debt_to_fcf = (
            _ratio(debt_number - cash_number, fcf_number)
            if None not in (debt_number, cash_number, fcf_number) and fcf_number > 0 else None
        )
        interest_expense = _number(intexp)
        interest_coverage = (
            _ratio(ebit, abs(interest_expense))
            if interest_expense not in {None, 0} else None
        )
        roic_pct = _number(roic) * 100 if _number(roic) is not None else None
        roic_prior_3y_pct = (_number(roic_prev_3y) * 100
                             if _number(roic_prev_3y) is not None else None)
        roic_median_pct = (_number(roic_5y_median) * 100
                           if _number(roic_5y_median) is not None else None)
        roic_stddev_pct = (_number(roic_5y_stddev) * 100
                           if _number(roic_5y_stddev) is not None else None)
        pe_number = _number(pe)
        ps_number = _number(ps)
        pb_number = _number(pb)
        share_change = (_ratio(_number(sharesbas) - _number(sharesbas_prev_y),
                               sharesbas_prev_y, 100)
                        if None not in (_number(sharesbas), _number(sharesbas_prev_y)) else None)
        debt_now = _ratio(debtnc, assets, 100)
        debt_prior = _ratio(debtnc_prev_y, assets_prev_y, 100)
        sector_return = (_ratio(_number(sector_close) - _number(sector_close_3m),
                                sector_close_3m, 100)
                         if None not in (_number(sector_close), _number(sector_close_3m)) else None)
        market_return = (_ratio(_number(market_close) - _number(market_close_3m),
                                market_close_3m, 100)
                         if None not in (_number(market_close), _number(market_close_3m)) else None)
        eps_ttm_growth = ttm_eps_growth(
            _number(eps_ttm_diluted), _number(eps_ttm_diluted_prev_3y)
        )

        def cagr(current, prior, years=3):
            current, prior = _number(current), _number(prior)
            if current is None or prior is None or current <= 0 or prior <= 0:
                return None
            return ((current / prior) ** (1 / years) - 1) * 100

        art_revenue_yoy = (_ratio(_number(art_revenue) - _number(art_revenue_prev_y),
                                  art_revenue_prev_y, 100)
                           if None not in (_number(art_revenue),
                                           _number(art_revenue_prev_y)) else None)
        fcf_yoy = (_ratio(_number(fcf) - _number(fcf_prev_y), fcf_prev_y, 100)
                   if None not in (_number(fcf), _number(fcf_prev_y))
                   and _number(fcf_prev_y) > 0 else None)

        def trend_score(close, sma50, sma200):
            close, sma50, sma200 = _number(close), _number(sma50), _number(sma200)
            checks = []
            if None not in (close, sma50):
                checks.append(close >= sma50)
            if None not in (close, sma200):
                checks.append(close >= sma200)
            if None not in (sma50, sma200):
                checks.append(sma50 >= sma200)
            return sum(checks) / len(checks) * 100 if checks else None

        price_to_sma50 = (_ratio(_number(own_close) - _number(own_sma50),
                                 own_sma50, 100)
                          if None not in (_number(own_close), _number(own_sma50)) else None)
        price_above_sma200 = (_ratio(_number(own_close) - _number(own_sma200),
                                    own_sma200, 100)
                              if None not in (_number(own_close), _number(own_sma200)) else None)
        by_id[observation_id] = {
            "revenue_growth_yoy_pct": round(current_growth, 4) if current_growth is not None else None,
            "prior_revenue_growth_yoy_pct": round(prior_growth, 4) if prior_growth is not None else None,
            "revenue_growth_change_pp": round(current_growth - prior_growth, 4)
            if None not in (current_growth, prior_growth) else None,
            "operating_margin_change_qoq_pp": round(current_margin - prior_margin, 4)
            if None not in (current_margin, prior_margin) else None,
            "operating_margin_pct": round(_ratio(opinc, revenue, 100), 4)
            if _ratio(opinc, revenue, 100) is not None else None,
            "operating_margin_change_yoy_pp": round(
                _ratio(art_opinc, art_revenue, 100) - annual_margin, 4
            ) if None not in (_ratio(art_opinc, art_revenue, 100), annual_margin) else None,
            "revenue_cagr_3y_pct": round(cagr(art_revenue, art_revenue_prev_3y), 4)
            if cagr(art_revenue, art_revenue_prev_3y) is not None else None,
            "revenue_ttm_growth_yoy_pct": round(art_revenue_yoy, 4)
            if art_revenue_yoy is not None else None,
            "eps_growth_yoy_pct": round(_number(eps_growth_yoy), 4)
            if _number(eps_growth_yoy) is not None else None,
            "eps_ttm_diluted": round(_number(eps_ttm_diluted), 4)
            if _number(eps_ttm_diluted) is not None else None,
            "eps_ttm_diluted_3y_ago": round(_number(eps_ttm_diluted_prev_3y), 4)
            if _number(eps_ttm_diluted_prev_3y) is not None else None,
            "eps_recovery": eps_ttm_growth["state"] == "recovery",
            "eps_deterioration": eps_ttm_growth["state"] == "deterioration",
            "eps_growth_state": eps_ttm_growth["state"],
            "eps_cagr_3y_pct": eps_ttm_growth["cagr_pct"],
            "eps_growth_change_pp": round(
                _number(eps_growth_yoy) - _number(prior_eps_growth_yoy), 4
            ) if None not in (_number(eps_growth_yoy),
                              _number(prior_eps_growth_yoy)) else None,
            "fcf_growth_yoy_pct": round(fcf_yoy, 4) if fcf_yoy is not None else None,
            "fcf_recovery": bool(_number(fcf) is not None and _number(fcf) > 0
                                 and _number(fcf_prev_y) is not None
                                 and _number(fcf_prev_y) <= 0),
            "fcf_cagr_3y_pct": round(cagr(fcf, fcf_prev_3y), 4)
            if cagr(fcf, fcf_prev_3y) is not None else None,
            "positive_fcf_history_pct": round(_number(positive_fcf_history), 4)
            if _number(positive_fcf_history) is not None else None,
            "fcf_history_observations": int(fcf_history_observations)
            if _number(fcf_history_observations) is not None else 0,
            "fcf_margin_pct": round(fcf_margin, 4) if fcf_margin is not None else None,
            "net_profit_margin_pct": round(net_profit_margin, 4)
            if net_profit_margin is not None else None,
            "net_profit_margin_change_yoy_pp": round(net_profit_margin_change, 4)
            if net_profit_margin_change is not None else None,
            "gross_margin_pct": round(gross_margin, 4) if gross_margin is not None else None,
            "gross_profitability_pct": round(gross_profitability, 4)
            if gross_profitability is not None else None,
            "rnd_to_revenue_pct": round(rnd_intensity, 4)
            if rnd_intensity is not None else None,
            "capex_to_revenue_pct": round(capex_intensity, 4)
            if capex_intensity is not None else None,
            "cash_conversion_pct": round(cash_conversion, 4)
            if cash_conversion is not None else None,
            "accruals_to_assets_pct": round(accruals_to_assets, 4)
            if accruals_to_assets is not None else None,
            "net_debt_to_fcf": round(net_debt_to_fcf, 4)
            if net_debt_to_fcf is not None else None,
            "interest_coverage": round(interest_coverage, 4)
            if interest_coverage is not None else None,
            "roic_pct": round(roic_pct, 4) if roic_pct is not None else None,
            "roic_5y_median_pct": round(roic_median_pct, 4)
            if roic_median_pct is not None else None,
            "roic_5y_stddev_pct": round(roic_stddev_pct, 4)
            if roic_stddev_pct is not None else None,
            "roic_5y_observations": int(roic_5y_observations)
            if _number(roic_5y_observations) is not None else 0,
            "roic_change_3y_pp": round(roic_pct - roic_prior_3y_pct, 4)
            if None not in (roic_pct, roic_prior_3y_pct) else None,
            "pe_to_revenue_growth": round(pe_number / current_growth, 4)
            if pe_number is not None and pe_number > 0
            and current_growth is not None and current_growth > 0 else None,
            "positive_eps_quarters_pct": round(_number(positive_eps_quarters), 4)
            if _number(positive_eps_quarters) is not None else None,
            "eps_improving_quarters_pct": round(_number(eps_improving_quarters), 4)
            if _number(eps_improving_quarters) is not None else None,
            "revenue_growth_positive_quarters_pct": round(_number(revenue_positive_quarters), 4)
            if _number(revenue_positive_quarters) is not None else None,
            "revenue_growth_volatility_pct": round(_number(revenue_growth_volatility), 4)
            if _number(revenue_growth_volatility) is not None else None,
            "revenue_growth_min_pct": round(_number(revenue_growth_min), 4)
            if _number(revenue_growth_min) is not None else None,
            "trailing_pe": round(pe_number, 4) if pe_number is not None and pe_number > 0 else None,
            "price_to_sales": round(ps_number, 4) if ps_number is not None and ps_number > 0 else None,
            "price_to_book": round(pb_number, 4) if pb_number is not None and pb_number > 0 else None,
            "share_count_change_yoy_pct": round(share_change, 4) if share_change is not None else None,
            "debt_to_assets_change_yoy_pp": round(debt_now - debt_prior, 4)
            if None not in (debt_now, debt_prior) else None,
            "sector_return_3m_pct": round(sector_return, 4) if sector_return is not None else None,
            "market_return_3m_pct": round(market_return, 4) if market_return is not None else None,
            "sector_minus_market_3m_pct": round(sector_return - market_return, 4)
            if None not in (sector_return, market_return) else None,
            "sector_trend_score": round(trend_score(
                sector_close, sector_sma50, sector_sma200), 4)
            if trend_score(sector_close, sector_sma50, sector_sma200) is not None else None,
            "market_trend_score": round(trend_score(
                market_close, market_sma50, market_sma200), 4)
            if trend_score(market_close, market_sma50, market_sma200) is not None else None,
            "price_to_sma50_abs_pct": round(abs(price_to_sma50), 4)
            if price_to_sma50 is not None else None,
            "price_above_sma200_pct": round(price_above_sma200, 4)
            if price_above_sma200 is not None else None,
        }
    enriched = 0
    for row in rows:
        factors = by_id.get(row["observation_id"])
        if factors:
            row["research_factors"] = factors
            enriched += 1
    return {"observations": len(rows), "enriched": enriched}


def _percentile(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def _completed(rows: list[dict]):
    output = []
    for row in rows:
        state = _fixed_horizon_evaluation(row, 30, 365)["result"]
        if state in {"success", "failure"}:
            output.append((row, state == "success"))
    return output


def _distribution(values: list[float]):
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2) if values else None,
        "p25": round(_percentile(values, .25), 2) if values else None,
        "p75": round(_percentile(values, .75), 2) if values else None,
    }


def compare_factors(episodes: list[dict], factors=FACTOR_DEFINITIONS) -> list[dict]:
    completed = _completed(episodes)
    output = []
    for factor in factors:
        success, failure = [], []
        for row, won in completed:
            value = factor_value(row, factor["field"])
            if isinstance(value, (int, float)):
                (success if won else failure).append(float(value))
        covered = len(success) + len(failure)
        success_median = statistics.median(success) if success else None
        failure_median = statistics.median(failure) if failure else None
        difference = (success_median - failure_median
                      if None not in (success_median, failure_median) else None)
        aligned = (difference is not None and
                   (difference > 0 if factor["direction"] == "higher" else difference < 0))
        output.append({
            **factor,
            "completed_episodes": len(completed),
            "covered_episodes": covered,
            "coverage_pct": round(covered / len(completed) * 100, 1) if completed else None,
            "successes_with_data": len(success),
            "failures_with_data": len(failure),
            "success_distribution": _distribution(success),
            "failure_distribution": _distribution(failure),
            "median_difference_success_minus_failure": round(difference, 2)
            if difference is not None else None,
            "direction_matches_hypothesis": aligned,
            "descriptive_only": True,
        })
    return output


def _fold_date_sets(episodes: list[dict], folds: int):
    dates = sorted({row.get("decision_date") or row.get("entry_date") for row in episodes})
    if len(dates) < folds:
        folds = max(3, len(dates))
    chunks = []
    for index in range(folds):
        start = round(index * len(dates) / folds)
        end = round((index + 1) * len(dates) / folds)
        if dates[start:end]:
            chunks.append(set(dates[start:end]))
    return chunks


def _condition(factor: dict, threshold: float):
    return {"factor_id": factor["id"], "field": factor["field"],
            "op": ">=" if factor["direction"] == "higher" else "<=",
            "value": round(float(threshold), 4)}


def _passes(row: dict, conditions: list[dict]):
    for condition in conditions:
        value = factor_value(row, condition["field"])
        if not isinstance(value, (int, float)):
            return False
        if condition["op"] == ">=" and value < condition["value"]:
            return False
        if condition["op"] == "<=" and value > condition["value"]:
            return False
    return True


def _candidate_catalog(training: list[dict], factors: tuple[dict, ...], quantiles):
    factor_by_id = {factor["id"]: factor for factor in factors}
    thresholds = {}
    for factor in factors:
        values = [factor_value(row, factor["field"]) for row, _won in _completed(training)]
        values = [float(value) for value in values if isinstance(value, (int, float))]
        conditions = [
            _condition(factor, value) for q in quantiles
            if (value := _percentile(values, q)) is not None
        ]
        thresholds[factor["id"]] = list({
            (condition["op"], condition["value"]): condition
            for condition in conditions
        }.values())
    candidates = []
    for factor in factors:
        for condition in thresholds[factor["id"]]:
            candidates.append({"id": factor["id"], "label": factor["label"],
                               "kind": "single_factor", "conditions": [condition]})
    # Pre-declared economic hypotheses avoid brute-force mining of every pair.
    # Use a permissive quartile for each leg so multi-factor samples remain useful.
    for template_id, label, factor_ids in COMBINATION_TEMPLATES:
        if not all(thresholds.get(factor_id) for factor_id in factor_ids):
            continue
        conditions = []
        for factor_id in factor_ids:
            factor = factor_by_id[factor_id]
            options = thresholds[factor_id]
            conditions.append(options[0] if factor["direction"] == "higher" else options[-1])
        candidates.append({"id": template_id, "label": label,
                           "kind": "predeclared_combination", "conditions": conditions})
    return candidates


def _rule_summary(rows: list[dict], conditions: list[dict]):
    return _target_summary([row for row in rows if _passes(row, conditions)], 30, 365)


def _inner_training_split(rows: list[dict], validation_share: float):
    dates = sorted({row.get("decision_date") or row.get("entry_date") for row in rows})
    if len(dates) < 2:
        return rows, []
    boundary = max(1, min(len(dates) - 1, round(len(dates) * (1 - validation_share))))
    development_dates, validation_dates = set(dates[:boundary]), set(dates[boundary:])
    return (
        [row for row in rows
         if (row.get("decision_date") or row.get("entry_date")) in development_dates],
        [row for row in rows
         if (row.get("decision_date") or row.get("entry_date")) in validation_dates],
    )


def walk_forward_explorer(episodes: list[dict], *, factors=FACTOR_DEFINITIONS,
                          policy: dict | None = None):
    policy = {**DEFAULT_POLICY, **(policy or {})}
    chunks = _fold_date_sets(episodes, int(policy["folds"]))
    fold_reports, aggregate_selected, aggregate_baseline = [], [], []
    family_oos: dict[str, dict] = {}
    for index in range(2, len(chunks)):
        training_dates = set().union(*chunks[:index])
        test_dates = chunks[index]
        training = [row for row in episodes
                    if (row.get("decision_date") or row.get("entry_date")) in training_dates]
        testing = [row for row in episodes
                   if (row.get("decision_date") or row.get("entry_date")) in test_dates]
        development, inner_validation = _inner_training_split(
            training, float(policy["inner_validation_share"])
        )
        candidates = _candidate_catalog(development, tuple(factors), policy["quantiles"])
        ranked = []
        baseline_development = _target_summary(development, 30, 365)
        baseline_inner = _target_summary(inner_validation, 30, 365)
        for candidate in candidates:
            development_summary = _rule_summary(development, candidate["conditions"])
            inner_summary = _rule_summary(inner_validation, candidate["conditions"])
            if (development_summary["completed_episodes"] < policy["minimum_training_completed"]
                    or development_summary["unique_issuers"] < policy["minimum_training_unique_issuers"]
                    or inner_summary["completed_episodes"] < policy["minimum_inner_validation_completed"]):
                continue
            development_improvement = (
                development_summary["success_rate_pct"] - baseline_development["success_rate_pct"]
                if None not in (development_summary.get("success_rate_pct"),
                                baseline_development.get("success_rate_pct")) else None
            )
            inner_improvement = (
                inner_summary["success_rate_pct"] - baseline_inner["success_rate_pct"]
                if None not in (inner_summary.get("success_rate_pct"),
                                baseline_inner.get("success_rate_pct")) else None
            )
            if (policy["require_nonnegative_inner_improvement"]
                    and (development_improvement is None or inner_improvement is None
                         or development_improvement < 0 or inner_improvement < 0)):
                continue
            development_ci = development_summary.get("success_rate_ci90_pct") or [-1, -1]
            inner_ci = inner_summary.get("success_rate_ci90_pct") or [-1, -1]
            rank = (min(development_ci[0], inner_ci[0]),
                    min(development_summary.get("success_rate_pct") or -1,
                        inner_summary.get("success_rate_pct") or -1),
                    inner_summary["completed_episodes"])
            ranked.append((rank, candidate, {
                "development": development_summary,
                "inner_validation": inner_summary,
                "development_improvement_pp": round(development_improvement, 1),
                "inner_validation_improvement_pp": round(inner_improvement, 1),
            }))
        baseline_training = _target_summary(training, 30, 365)
        baseline_test = _target_summary(testing, 30, 365)
        if not ranked:
            fold_reports.append({
                "fold": index - 1, "status": "no_eligible_training_rule",
                "training": {"first": min(training_dates), "last": max(training_dates)},
                "test": {"first": min(test_dates), "last": max(test_dates)},
                "baseline_training": baseline_training, "baseline_test": baseline_test,
                "baseline_development": baseline_development,
                "baseline_inner_validation": baseline_inner,
            })
            aggregate_baseline.extend(testing)
            continue
        ranked.sort(key=lambda item: item[0], reverse=True)
        best_by_family = {}
        for ranked_candidate in ranked:
            family_id = ranked_candidate[1]["id"]
            if family_id not in best_by_family:
                best_by_family[family_id] = ranked_candidate
        fold_family_results = []
        for _family_rank, family_candidate, family_selection in best_by_family.values():
            family_test_rows = [
                row for row in testing if _passes(row, family_candidate["conditions"])
            ]
            family_test = _target_summary(family_test_rows, 30, 365)
            family_improvement = (
                round(family_test["success_rate_pct"] - baseline_test["success_rate_pct"], 1)
                if None not in (family_test.get("success_rate_pct"),
                                baseline_test.get("success_rate_pct")) else None
            )
            family = family_oos.setdefault(family_candidate["id"], {
                "id": family_candidate["id"], "label": family_candidate.get("label"),
                "kind": family_candidate.get("kind"), "selected_rows": [],
                "baseline_rows": [], "folds": [],
            })
            family["selected_rows"].extend(family_test_rows)
            family["baseline_rows"].extend(testing)
            compact_fold = {
                "fold": index - 1, "conditions": family_candidate["conditions"],
                "selected_test": family_test,
                "test_improvement_pp": family_improvement,
                "inner_validation_improvement_pp": family_selection["inner_validation_improvement_pp"],
            }
            family["folds"].append(compact_fold)
            fold_family_results.append({
                "id": family_candidate["id"], "label": family_candidate.get("label"),
                "kind": family_candidate.get("kind"),
                "completed_episodes": family_test["completed_episodes"],
                "success_rate_pct": family_test["success_rate_pct"],
                "test_improvement_pp": family_improvement,
            })
        _rank, selected, selected_training = ranked[0]
        selected_test_rows = [row for row in testing if _passes(row, selected["conditions"])]
        selected_test = _target_summary(selected_test_rows, 30, 365)
        improvement = (round(selected_test["success_rate_pct"] - baseline_test["success_rate_pct"], 1)
                       if None not in (selected_test.get("success_rate_pct"),
                                       baseline_test.get("success_rate_pct")) else None)
        fold_reports.append({
            "fold": index - 1,
            "status": "evaluated",
            "training": {"first": min(training_dates), "last": max(training_dates),
                         "completed_episodes": baseline_training["completed_episodes"]},
            "test": {"first": min(test_dates), "last": max(test_dates)},
            "selected_rule": selected,
            "selection_result": selected_training,
            "baseline_development": baseline_development,
            "baseline_inner_validation": baseline_inner,
            "baseline_test": baseline_test,
            "selected_test": selected_test,
            "test_improvement_pp": improvement,
            "eligible_family_results": sorted(
                fold_family_results,
                key=lambda row: (row.get("test_improvement_pp") is not None,
                                 row.get("test_improvement_pp") or -999),
                reverse=True,
            ),
        })
        aggregate_selected.extend(selected_test_rows)
        aggregate_baseline.extend(testing)
    baseline_oos = _target_summary(aggregate_baseline, 30, 365)
    selected_oos = _target_summary(aggregate_selected, 30, 365)
    improvement = (round(selected_oos["success_rate_pct"] - baseline_oos["success_rate_pct"], 1)
                   if None not in (selected_oos.get("success_rate_pct"),
                                   baseline_oos.get("success_rate_pct")) else None)
    drawdown_change = (round(selected_oos["median_max_drawdown_pct"]
                             - baseline_oos["median_max_drawdown_pct"], 1)
                       if None not in (selected_oos.get("median_max_drawdown_pct"),
                                       baseline_oos.get("median_max_drawdown_pct")) else None)
    evaluated = [fold for fold in fold_reports if fold["status"] == "evaluated"]
    nonworse = sum(
        isinstance(fold.get("test_improvement_pp"), (int, float))
        and fold["test_improvement_pp"] >= 0
        for fold in evaluated
    )
    checks = {
        "enough_oos_episodes": selected_oos["completed_episodes"] >= policy["minimum_total_oos_completed"],
        "minimum_fold_sample": bool(evaluated) and all(
            fold["selected_test"]["completed_episodes"] >= policy["minimum_fold_completed"]
            for fold in evaluated
        ),
        "oos_improvement": improvement is not None and improvement >= policy["minimum_oos_improvement_pp"],
        "oos_success_rate": selected_oos.get("success_rate_pct") is not None
        and selected_oos["success_rate_pct"] >= policy["minimum_oos_success_rate_pct"],
        "fold_stability": bool(evaluated) and nonworse / len(evaluated) >= 2 / 3,
        "drawdown": drawdown_change is not None
        and drawdown_change >= -policy["maximum_drawdown_deterioration_pp"],
    }
    leaderboard = []
    for family in family_oos.values():
        family_baseline = _target_summary(family["baseline_rows"], 30, 365)
        family_selected = _target_summary(family["selected_rows"], 30, 365)
        family_improvement = (
            round(family_selected["success_rate_pct"] - family_baseline["success_rate_pct"], 1)
            if None not in (family_selected.get("success_rate_pct"),
                            family_baseline.get("success_rate_pct")) else None
        )
        family_drawdown = (
            round(family_selected["median_max_drawdown_pct"]
                  - family_baseline["median_max_drawdown_pct"], 1)
            if None not in (family_selected.get("median_max_drawdown_pct"),
                            family_baseline.get("median_max_drawdown_pct")) else None
        )
        nonworse_folds = sum(
            isinstance(fold.get("test_improvement_pp"), (int, float))
            and fold["test_improvement_pp"] >= 0 for fold in family["folds"]
        )
        family_checks = {
            "enough_oos_episodes": family_selected["completed_episodes"]
            >= policy["minimum_total_oos_completed"],
            "minimum_fold_sample": bool(family["folds"]) and all(
                fold["selected_test"]["completed_episodes"] >= policy["minimum_fold_completed"]
                for fold in family["folds"]
            ),
            "all_walk_forward_folds": len(family["folds"]) == len(evaluated),
            "oos_improvement": family_improvement is not None
            and family_improvement >= policy["minimum_oos_improvement_pp"],
            "oos_success_rate": family_selected.get("success_rate_pct") is not None
            and family_selected["success_rate_pct"] >= policy["minimum_oos_success_rate_pct"],
            "fold_stability": bool(family["folds"])
            and nonworse_folds / len(family["folds"]) >= 2 / 3,
            "drawdown": family_drawdown is not None
            and family_drawdown >= -policy["maximum_drawdown_deterioration_pp"],
        }
        leaderboard.append({
            "id": family["id"], "label": family["label"], "kind": family["kind"],
            "folds_evaluated": len(family["folds"]),
            "folds_nonworse": nonworse_folds,
            "out_of_sample_baseline": family_baseline,
            "out_of_sample_selected": family_selected,
            "out_of_sample_improvement_pp": family_improvement,
            "median_drawdown_change_pp": family_drawdown,
            "folds": family["folds"],
            "research_checks": family_checks,
            "research_signal": all(family_checks.values()),
            "descriptive_only": True,
        })
    leaderboard.sort(
        key=lambda row: (row["folds_evaluated"],
                         row.get("out_of_sample_improvement_pp")
                         if row.get("out_of_sample_improvement_pp") is not None else -999,
                         row["out_of_sample_selected"].get("completed_episodes") or 0),
        reverse=True,
    )
    return {
        "method": "rolling_origin_with_inner_validation_factor_explorer_v3",
        "production_effect": "none",
        "deployable_rule": False,
        "policy": policy,
        "folds": fold_reports,
        "out_of_sample_baseline": baseline_oos,
        "out_of_sample_selected": selected_oos,
        "out_of_sample_improvement_pp": improvement,
        "median_drawdown_change_pp": drawdown_change,
        "folds_nonworse": nonworse,
        "folds_evaluated": len(evaluated),
        "research_checks": checks,
        "research_signal": all(checks.values()),
        "hypothesis_leaderboard": leaderboard,
        "best_descriptive_hypothesis": leaderboard[0] if leaderboard else None,
        "interpretation": (
            "Thresholds are learned on the oldest data, checked on an inner validation slice, "
            "then evaluated on the next unseen fold. A research signal only nominates factor "
            "families for a new frozen rule; it never alters production."
        ),
    }


def run_factor_explorer(observations: list[dict], *, step_days: int = 30,
                        policy: dict | None = None):
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _episode_roots(observations, {}, {"buy": 0, "watch": 0}, max_gap,
                              use_recorded_verdict=True)
    return {
        "ok": True,
        "version": 3,
        "objective": "Identify entry-date factors separating +30%-within-one-year successes and failures.",
        "information_boundary": "Point-in-time values available on or before the first Buy date only.",
        "production_effect": "none",
        "episodes": len(episodes),
        "factor_comparison": compare_factors(episodes),
        "unavailable_factors": list(UNAVAILABLE_FACTORS),
        "combination_hypotheses": [
            {"id": template_id, "label": label, "factor_ids": list(factor_ids)}
            for template_id, label, factor_ids in COMBINATION_TEMPLATES
        ],
        "walk_forward": walk_forward_explorer(episodes, policy=policy),
    }
