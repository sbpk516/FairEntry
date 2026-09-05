# FairEntry - Scoring Methodology

_Generated from `config/scoring.yaml`. Do not edit by hand._

**Verdict bands:** Buy >= 72 · Watch >= 50 · else Avoid.

Only factors marked **tested** may affect the verdict.

## Categories and factors

### Business Quality - configured weight 22.5

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Gross margin vs sector | tested | 30 | `gross_margin` | ≥ sector median | `sector_rel` |
| ROIC vs sector | tested | 35 | `roic` | ≥ sector median | `sector_rel` |
| Operating margin | tested | 35 | `oper_margin` | positive & healthy | `higher_better` |

### Financial Strength & Survival - configured weight 6.25

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Altman-Z | tested | 30 | `altman_z` | > 2.6 = safe | `band` |
| Debt / equity | tested | 25 | `debt_eq` | < 0.7 comfortable | `lower_better` |
| Long-term debt burden: change from one year ago | testing | 20 | `debt_to_assets_change_yoy_pp` | negative means improving | `lower_better` |
| Current ratio | tested | 15 | `current_ratio` | > 1.5 | `higher_better` |
| Share-count trend | tested | 10 | `share_count_yoy` | ≤ 0 (no dilution) | `lower_better` |

### Growth & Operating Momentum - configured weight 33.75

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Revenue growth vs sector | tested | 35 | `rev_growth_qoq` | ≥ sector median | `sector_rel` |
| Expected EPS growth next year | information_only | 30 | `eps_growth_next_y` | ≥ 15% | `higher_better` |
| Margin direction | testing | 35 | `margin_trend_score` | stable or improving | `passthrough` |

### Valuation & Margin of Safety - configured weight 18.75

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Intrinsic-value gap | tested | 40 | `intrinsic_gap_pct` | ≥ 12% below fair | `higher_better` |
| P/Free Cash Flow | tested | 30 | `pfcf_ratio` | < 15 cheap | `lower_better` |
| P/S vs sector | tested | 30 | `ps_ratio` | ≤ sector median | `sector_rel` |

### Market Confirmation - configured weight 18.75

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Price above resistance | tested | 25 | `breakout_price_score` | close ≥2% above resistance | `passthrough` |
| Breakout volume | tested | 20 | `breakout_volume_score` | ≥1.5× prior 50-day average | `passthrough` |
| Relative strength | tested | 15 | `relative_strength_score` | outperform sector and SPY | `passthrough` |
| Trend regime | tested | 15 | `trend_regime_score` | supportive moving-average trend | `passthrough` |
| Trading liquidity quality | information_only | 0 | `avg_dollar_volume` | $20M+ average daily dollar volume | `higher_better` |
| Institutional ownership change | information_only | 10 | `inst_trans` | positive change in reported institutional ownership | `higher_better` |
| Smart-money SEC 13F | information_only | 5 | `thirteenf_score` | owned / added by respected tracked funds | `passthrough` |
| Insider buying | information_only | 10 | `insider_score` | fresh / cluster / top-exec buys | `passthrough` |

### Catalysts & Narrative - configured weight 0

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| News review | information_only | 40 | `news_sentiment_score` | specific sourced event | `passthrough` |
| Analyst consensus | information_only | 25 | `analyst_recom` | ≤ 2 (Buy) | `lower_better` |
| Analyst target revisions | information_only | 20 | `estimate_revision_score` | mean analyst price target rising | `passthrough` |
| Earnings and transcript review | information_only | 0 | `earnings_transcript_context` | dated results, guidance changes, management delivery and contradictions | `passthrough` |
| Government policy | information_only | 0 | `government_policy_context` | named enacted or proposed policy with a direct business path | `passthrough` |
| Short interest context | information_only | 15 | `short_float` | elevated but not extreme | `band` |

### Risk, Red Flags & Fragility - configured weight 0

| Factor | Decision use | Weight | Metric | Expected | Rule |
|---|---|--:|---|---|---|
| Forensic / accounting review | information_only | 40 | `red_flags_score` | clean | `passthrough` |
| Short interest (risk) | information_only | 30 | `short_float` | < 10% = low | `lower_better` |
| Macro / beta | information_only | 30 | `beta` | resilient (β near 1) | `lower_better` |
| Operational disruption | information_only | 0 | `operational_disruption_context` | shutdown, component shortage, supply-chain or technology-transition evidence | `passthrough` |
| External events | information_only | 0 | `external_event_context` | specific war, pandemic or natural-disaster exposure | `passthrough` |

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
- Current price is within +/-5% of either the 9-month or 20-month EMA.
- Weekly OBV is above its 20-week EMA.
- No tested hard veto is active.

## Additional tested soft gates

## AI and news review

AI, news, policy, contract, and expansion evidence is information only. It has zero effect on Buy / Watch / Avoid until the same factor can be replayed historically and passes validation.
