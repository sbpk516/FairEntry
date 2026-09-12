# Entry-price audit — 2026-09-12

## Scope and result

The deployed `https://sbpk516.github.io/FairEntry/data/backtest-sfa.json` was fetched and matched the local artifact byte for byte (32,246,881 bytes). Its identity is run `sfa-285a70da186f`, snapshot `20260810T132048Z`, implementation `38abe5e94264396c`.

All 296 displayed Buy episodes were joined to `sfa_prices` by ticker and entry date using the local Sharadar DuckDB warehouse. All 296 had a matching price, and all displayed entries matched `round(close * 1.0015, 2)` within $0.011. No missing rows or discrepancies were found. This verifies reproduction from this vendor snapshot, not independent truth of every vendor price.

## BBOX: November 27, 2000

The private observation identifies US NASDAQ BLACK BOX CORP, Sharadar security ID 198114, CUSIP 091826107, SEC CIK 0000849547, currency USD. Its decision date is November 24; the simulated entry is the next trading session, November 27.

| Item | Value |
|---|---:|
| Warehouse close | $61.88 |
| Warehouse unadjusted close | $61.88 |
| Warehouse dividend-adjusted close | $50.919 |
| Entry slippage plus transaction cost | 0.15% |
| Calculated entry | $61.97282 |
| Displayed entry | $61.97 |

The code applies the cost to `close` for the displayed entry (`fairentry/backtest/sfa_replay.py`, `adjusted_entry`). Fixed-horizon returns use `closeadj`, with entry and exit costs. These price bases must be named in the UI rather than all described as an ordinary quoted price.

The next session, November 28, has a close of $58.88. It is not this episode's entry date. The current `First Buy` UI column comes from the episode's entry date, not its decision date; this should be made explicit.

## Independent corroboration and remaining uncertainty

[Digrin's BBOX monthly history](https://www.digrin.com/stocks/detail/BBOX/price) shows November 2000 at $55.88 unadjusted and $45.89 adjusted. The warehouse's November 30 close is also $55.88. This supports the approximate historical price level and month-end value, but does not verify the exact November 27 daily close. The page also contains stale post-delisting rows, so it is supporting evidence only.

The user's approximately $10 TradingView quote has not been reconciled. The exact TradingView symbol/exchange and adjustment settings are needed. [NSE:BBOX](https://www.tradingview.com/symbols/NSE-BBOX/) is Black Box Limited in India, priced in INR; [LSE:BBOX](https://www.tradingview.com/symbols/LSE-BBOX/) is Tritax Big Box REIT, priced in GBX. Neither is the historical NASDAQ security used here. Do not assume the user selected either one without seeing their link.

## What would make the prices reviewable

1. Display exchange, currency, historical company/security identity, decision date, and execution date.
2. Show source close and simulated entry cost separately, including an explicit adjustment basis.
3. Carry the source snapshot and raw price provenance into episode details.
4. Continue the all-episode warehouse reconciliation, but add independent daily-price checks for suspicious rows. VectorBT uses the same warehouse and therefore cannot independently prove the vendor prices.
5. Check split/dividend treatment and ticker identity before comparing different chart providers. Do not replace a stored price just because another symbol with the same ticker has a different quote.

This audit does not change scoring, backtest outputs, website text, or deployment. No live replay or external trading action was performed.
