# Scoring ablation — 2026-07-17

Point-in-time rolling test on `data/backtest.db`:

- Window: 2023-07-17 through 2026-07-13
- Cohorts: 55
- Holding period: 30 days
- Step: 14 days
- Population: screener-passing candidates
- Alpha: stock return minus its cohort's cross-sectional mean
- Confidence interval: 90% block bootstrap over cohorts

| Variant | Buy observations | Buy alpha | Avoid alpha | Buy−Avoid spread | Buy hit rate | Spread CI90 |
|---|---:|---:|---:|---:|---:|---:|
| Original scoring | 316 | +3.81% | +0.83% | +2.98% | 52.5% | +1.07% to +4.85% |
| P/S fix only | 520 | +3.14% | +1.00% | +2.14% | 51.3% | −0.09% to +4.50% |
| P/S fix + coverage gates | 418 | +3.75% | +1.00% | +2.75% | 51.7% | +0.62% to +4.95% |
| P/S fix + P/B applicability | 561 | +3.04% | +1.04% | +2.00% | 51.5% | −0.25% to +4.44% |
| All changes, including valuation weights | 448 | +3.62% | +1.02% | +2.60% | 52.0% | +0.44% to +4.94% |

## Production decision

- Keep the P/S direction correction: the original behavior is a known logic
  error even though it happened to select a narrower, historically stronger
  bucket in this sample.
- Keep coverage gates: relative to the corrected P/S baseline, they improve the
  Buy−Avoid spread from +2.14% to +2.75% and restore a CI above zero.
- Keep P/B applicability context-only: it reduced the spread to +2.00% and its
  CI crossed zero.
- Keep valuation weighting shadow-only: the all-changes package did not beat
  P/S + coverage (+2.60% versus +2.75%). A dedicated isolated test is required
  before promotion.
- Structured thesis drivers remain context-only and were not part of this test.

## Limitations

No variant was monotonic across Buy, Watch, and Avoid because the Avoid bucket
also had positive average alpha. The universe has survivorship bias, excludes
dividends and costs, and covers primarily the 2023–2026 recovery/bull regime.
Results support the production choice but are not proof of future performance.
