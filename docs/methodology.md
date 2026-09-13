# FairEntry - Scoring Methodology

_Generated from `config/scoring.yaml`. Do not edit by hand._

**Score bands (not final Buy eligibility):** Buy >= 72 · Watch >= 50 · else Avoid.

Only factors marked **tested** may affect the verdict.

## Categories and factors

### Business Quality - configured weight 22.5

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Gross margin vs sector | tested | 30 | `gross_margin` | at least the sector median | `sector_rel` |
| Return on invested capital (ROIC) vs sector | tested | 35 | `roic` | at least the sector median | `sector_rel` |
| Operating profit margin | tested | 35 | `oper_margin` | positive; 20% or more receives full credit | `higher_better` |

### Financial Strength & Survival - configured weight 6.25

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Altman Z financial-distress check | tested | 30 | `altman_z` | 2.6 or more is the safer range | `band` |
| Debt compared with shareholder equity | tested | 25 | `debt_eq` | below 0.7 is comfortable; 0.3 or less receives full credit | `lower_better` |
| Long-term debt burden: change from one year ago | testing | 20 | `debt_to_assets_change_yoy_pp` | a negative change means debt burden improved | `lower_better` |
| Current ratio (short-term bill coverage) | tested | 15 | `current_ratio` | above 1.5; 1.8 or more receives full credit | `higher_better` |
| Share-count change | tested | 10 | `share_count_yoy` | zero or negative means no dilution | `lower_better` |

### Growth & Operating Momentum - configured weight 33.75

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Latest-quarter sales growth vs sector | tested | 35 | `rev_growth_qoq` | at least the sector median | `sector_rel` |
| Expected EPS growth next year | information_only | 30 | `eps_growth_next_y` | ≥ 15% | `higher_better` |
| Margin direction | testing | 35 | `margin_trend_score` | stable or improving | `passthrough` |

### Valuation & Margin of Safety - configured weight 18.75

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Discount to estimated fair value | tested | 40 | `intrinsic_gap_pct` | at least 12% below fair value | `higher_better` |
| Price / free cash flow | tested | 30 | `pfcf_ratio` | below 15 is relatively inexpensive | `lower_better` |
| Price / sales vs sector | tested | 30 | `ps_ratio` | no higher than the sector median | `sector_rel` |

### Market Confirmation - configured weight 18.75

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Price above prior resistance | tested | 25 | `breakout_price_score` | closing price at least 2% above prior resistance | `passthrough` |
| Volume supporting the price move | tested | 20 | `breakout_volume_score` | at least 1.5 times the prior 50-day average | `passthrough` |
| Three-month return vs sector and S&P 500 | tested | 15 | `relative_strength_score` | stock outperforms both its sector fund and SPY | `passthrough` |
| Daily moving-average trend | tested | 15 | `trend_regime_score` | most moving-average checks are positive | `passthrough` |
| Trading liquidity quality | information_only | 0 | `avg_dollar_volume` | $20M+ average daily dollar volume | `higher_better` |
| Institutional ownership change | information_only | 10 | `inst_trans` | positive change in reported institutional ownership | `higher_better` |
| Large-investor SEC filings | information_only | 5 | `thirteenf_score` | tracked investors own or recently added shares | `passthrough` |
| Company-insider buying | information_only | 10 | `insider_score` | recent purchases by several insiders or a senior executive | `passthrough` |

### News & Possible Catalysts - configured weight 0

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| News review | information_only | 40 | `news_sentiment_score` | specific sourced event | `passthrough` |
| Analyst consensus | information_only | 25 | `analyst_recom` | 2 or lower on the provider's 1-to-5 scale | `lower_better` |
| Analyst target revisions | information_only | 20 | `estimate_revision_score` | mean analyst price target rising | `passthrough` |
| Earnings and transcript review | information_only | 0 | `earnings_transcript_context` | dated results, guidance changes, and evidence that management did or did not deliver | `passthrough` |
| Government policy | information_only | 0 | `government_policy_context` | a named proposed or enacted policy with a direct effect on the business | `passthrough` |
| Short interest context | information_only | 15 | `short_float` | moderate but not extreme | `band` |

### Risks & Accounting Warnings - configured weight 0

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Accounting warning review | information_only | 40 | `red_flags_score` | no major accounting warnings | `passthrough` |
| Short interest risk | information_only | 30 | `short_float` | below 10% is relatively low | `lower_better` |
| Sensitivity to market moves (beta) | information_only | 30 | `beta` | near or below 1 means no more volatile than the market | `lower_better` |
| Operational disruption | information_only | 0 | `operational_disruption_context` | evidence of a shutdown, shortage, supply-chain problem, or difficult technology transition | `passthrough` |
| External events | information_only | 0 | `external_event_context` | a specific company exposure to war, a pandemic, or a natural disaster | `passthrough` |

## Tested hard vetoes
- **distress_corroborated** - Corroborated financial distress (`altman_z < 1.8 and debt_eq > 2`)
- **substantial_dilution** - Substantial dilution: share count increased more than 10% year over year (`share_count_yoy > 10`)

## Information-only safety warnings
- **going_concern** - Going-concern doubt confirmed (`going_concern == True`)
- **critical_red_flag** - Critical accounting / fraud flag (`red_flags_critical > 0`)

## Production Buy-entry alignment

Every condition below must pass; any missing required value blocks Buy:

- Business Quality, Financial Strength, and Growth each >= 70.
- Current price is at or below the fair-value base from at least 1 tested valuation method.
- Moving-average proximity is an optional dashboard filter, not a Buy requirement.
- Weekly OBV is above its 20-week EMA.
- No tested hard veto is active.
- Recent ROIC direction must pass; missing or stale history blocks Buy.
- See the generated Screening & Buy criteria page for current eligibility and evidence limitations.

## Additional tested soft gates

## AI and news review

AI, news, policy, contract, and expansion evidence is information only. It has zero effect on Buy / Watch / Avoid until the same factor can be replayed historically and passes validation.
