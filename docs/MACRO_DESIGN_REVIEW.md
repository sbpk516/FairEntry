# Macro page: first-time-reader review

## Problems addressed

The previous page gave equal visual weight to technical methods, source caveats and headline conditions. Each card repeated its interpretation, benchmark statistics and four expanded historical comparisons before the user could move to the next indicator. Narrow charts and financial shorthand made scanning difficult. A reader had to assemble the overall economic picture themselves.

## Implemented direction

1. A plain-English opening briefing describes the current picture. Four representative measures sit alongside it: GDP, unemployment, headline inflation and financial conditions. The headline quantifies inflation's gap where applicable. Missing or stale measures produce an incomplete picture.
2. Three evidence-linked takeaways identify inflation, growth and unemployment claims as things to inspect. Their titles and wording follow the actual data; they do not assume those conditions are good or bad.
3. All eleven indicators remain accessible in a two-column desktop grid and a single-column phone layout. Topic filters let readers focus on jobs/growth, prices/rates, financial conditions or valuation. Cards have friendly titles alongside the original technical names and dated readings.
4. Current condition and two-year direction have separate labels. A historically low reading can still be worsening. Inflation direction compares distance from 2%, so falling inflation below target is not automatically an improvement. Valuations use “more/less expensive,” not a market forecast.
5. “Why this status?” opens a native dialog with the original thresholds, methods, charts, historical comparisons, data pagination, source links and CSV download. Escape dismisses the dialog and returns focus. All-history and alternative benchmark controls are retained.
6. The visual design uses warm neutral surfaces, forest green accents, restrained amber/red indicators, larger typography, explicit spacing and a clearer hierarchy. The new stylesheet is scoped to the macro page; the stock and Investors pages retain their layouts. No external fonts or design-service dependency were introduced.

## Summary rule

The briefing is an editorial summary of four existing classifications, not a new economic or investment model. Three or more adverse classifications produce “Several signs of strain”; all four supportive classifications produce “Mostly supportive readings”; other complete combinations produce “Mixed conditions.” Incomplete core readings produce “Incomplete picture.” Valuation and duplicate curve measures are not counted. The rule is disclosed in the page's methodology dialog and covered by tests. It does not change stock scoring.

## Review and validation

Checked desktop and phone-sized layouts visually, all eleven cards, topic filters, original-source links, data pagination, dialog dismissal/focus restoration and horizontal overflow. Unit tests cover missing/stale evidence, neutral boundaries, worsening deflation, equal-distance inflation changes, relative changes with zero denominators and summary classification without double-counting. Existing macro comparison tests remain applicable.

Commands: `node tests/macro-brief.test.cjs`, `node tests/macro-model.test.cjs`, and `python -m pytest tests/test_macro.py tests/test_market_valuation.py -q`.

The implementation is available in the local preview. Production publication is a separate step.
