# FairEntry — Scoring Methodology

_Generated from `config/scoring.yaml`. Do not edit by hand._

**Verdict bands:** Buy ≥ 72 · Watch ≥ 50 · else Avoid.

## Categories & items

### Business Quality — weight 16

| Item | Weight | Metric | Expected | Rule |
|---|--:|---|---|---|
| Gross margin vs sector | 30 | `gross_margin` | ≥ sector median | `sector_rel` |
| ROIC vs sector | 35 | `roic` | ≥ sector median | `sector_rel` |
| Operating margin | 35 | `oper_margin` | positive & healthy | `higher_better` |

### Financial Strength & Survival — weight 18

| Item | Weight | Metric | Expected | Rule |
|---|--:|---|---|---|
| Altman-Z | 35 | `altman_z` | > 2.6 = safe | `band` |
| Debt / equity | 25 | `debt_eq` | < 0.7 comfortable | `lower_better` |
| Current ratio | 20 | `current_ratio` | > 1.5 | `higher_better` |
| Share-count trend | 20 | `share_count_yoy` | ≤ 0 (no dilution) | `lower_better` |

### Growth & Operating Momentum — weight 14

| Item | Weight | Metric | Expected | Rule |
|---|--:|---|---|---|
| Revenue growth vs sector | 35 | `rev_growth_qoq` | ≥ sector median | `sector_rel` |
| EPS growth (next yr) | 30 | `eps_growth_next_y` | ≥ 15% | `higher_better` |
| Margin direction | 35 | `margin_trend_score` | stable or improving | `passthrough` |

### Valuation & Margin of Safety — weight 18

| Item | Weight | Metric | Expected | Rule |
|---|--:|---|---|---|
| Intrinsic-value gap | 40 | `intrinsic_gap_pct` | ≥ 12% below fair | `higher_better` |
| P/Free Cash Flow | 30 | `pfcf_ratio` | < 15 cheap | `lower_better` |
| P/S vs sector | 30 | `ps_ratio` | ≤ sector median | `sector_rel` |

### Market Confirmation — weight 11

| Item | Weight | Metric | Expected | Rule |
|---|--:|---|---|---|
| Price above resistance | 25 | `breakout_price_score` | close ≥2% above resistance | `passthrough` |
| Breakout volume | 20 | `breakout_volume_score` | ≥1.5× prior 50-day average | `passthrough` |
| Relative strength | 15 | `relative_strength_score` | outperform sector and SPY | `passthrough` |
| Trend regime | 15 | `trend_regime_score` | supportive moving-average trend | `passthrough` |
| Institutional ownership change | 10 | `inst_trans` | positive change in reported institutional ownership | `higher_better` |
| Smart-money SEC 13F | 5 | `thirteenf_score` | owned / added by respected tracked funds | `passthrough` |
| Insider buying | 10 | `insider_score` | fresh / cluster / top-exec buys | `passthrough` |

### Catalysts & Narrative — weight 9

| Item | Weight | Metric | Expected | Rule |
|---|--:|---|---|---|
| News sentiment | 40 | `news_sentiment_score` | positive stance | `passthrough` |
| Analyst consensus | 25 | `analyst_recom` | ≤ 2 (Buy) | `lower_better` |
| Analyst target revisions | 20 | `estimate_revision_score` | mean analyst price target rising | `passthrough` |
| Short-squeeze fuel | 15 | `short_float` | elevated but not extreme | `band` |

### Risk, Red Flags & Fragility — weight 14

| Item | Weight | Metric | Expected | Rule |
|---|--:|---|---|---|
| Forensic / accounting | 40 | `red_flags_score` | clean | `passthrough` |
| Short interest (risk) | 30 | `short_float` | < 10% = low | `lower_better` |
| Macro / beta | 30 | `beta` | resilient (β near 1) | `lower_better` |

## Hard vetoes (force Avoid)
- **going_concern** — Going-concern doubt confirmed (`going_concern == True`)
- **distress_corroborated** — Corroborated financial distress (`altman_z < 1.8 and debt_eq > 2`)
- **critical_red_flag** — Critical accounting / fraud flag (`red_flags_critical > 0`)

## Soft gates (cap Buy → Watch)
- **survival_floor** — Survival score below floor (`category_survival < 30`)
- **upside_below_target** — Upside below target (`upside_pct < target_upside`)
- **no_confirmation** — No market confirmation (`category_confirmation < 35`)
- **expensive** — Valuation is expensive (`valuation_label == 'expensive'`)

## Thesis modifier (recovery/growth score → ±base)
- score ≥ 80 → +6
- score ≥ 65 → +3
- score ≥ 50 → +0
- score ≥ 35 → -5
- score ≥ 0 → -10
