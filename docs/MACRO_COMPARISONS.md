# Historical macro comparisons

The macro page applies the same comparison controls to all eleven charts: nine economic indicators plus Buffett market-cap/GDP and conventional Shiller CAPE. Economic history is fetched from FRED from 1989 so annual inflation changes are available from 1990. Exports retain observations from 1990 onward. Valuation coverage remains 1997 onward to preserve the existing public-equity definition.

## What the controls mean

- Chart window: 2, 5, 10 years or all available observations. Every source observation is plotted; extreme events are retained. Each chart has its own labeled vertical scale.
- Reference: the ten complete calendar years preceding the latest observation's year, or the fixed 2010–2019 decade. Reference values do not change when only the chart window changes. The alternative decade is context, not a claim that that period was ideal.
- A shaded area shows the 25th–75th percentile range. A dashed line shows 2% for PCE, zero for GDP/curves/NFCI, or the historical median for other series.
- Direction tables show 1-, 2-, 5- and 10-year endpoint changes, actual values and dates. The earlier observation is the last valid one on or before the anniversary. Maximum date gaps are 10 days for daily, 15 weekly, 45 monthly and 100 quarterly data; missing history is not extrapolated. These are endpoint changes, not regression slopes.

## Reference statistics and interpretation

Statistics are calculated from source-frequency observations in the selected decade. Mean is arithmetic; median and quartiles use linear interpolation at index `(n−1) × q`. Historical percentile is `100 × count(value ≤ latest) / n`. At least 20 observations and coverage near both reference boundaries are required; no short-history substitute is used. Counts are not reweighted for changing population. Revised history is used, not historical release vintages.

Claims and unemployment use quartiles to describe relatively low (green), typical (amber), or elevated (red) labor stress. Buffett and CAPE use the same boundaries for valuation levels. These are FairEntry display conventions, not official healthy ranges or validated predictors. GDP below zero is contraction; positive growth below its lower reference quartile is amber, and growth at or above that boundary is green. Faster growth is not necessarily sustainable.

For PCE and core PCE, colors compare the absolute distance from 2% with the historical distribution of absolute distances: below/equal to its 25th percentile is green; above that through the 75th percentile is amber; above that is red. These are explicitly not Fed tolerance bands. The [Fed's formal longer-run 2% target applies to headline PCE](https://www.federalreserve.gov/monetarypolicy/monetary-policy-what-are-its-goals-how-does-it-work.htm); core uses it only as context. Both above-target inflation and below-target inflation can be undesirable. Movement toward/away from 2% is distinguished from rising/falling inflation.

Yield curves use zero to identify inversion, and show the spread in percentage points and basis points. Positive slope is green only on the inversion dimension, not a recession all-clear. [NFCI zero is its historical mean and one index point is one standard deviation](https://www.chicagofed.org/research/data/nfci/current-data); positive is tighter and negative is looser. The federal funds rate has a neutral badge because it has no universal good/bad cutoff.

Percentile is not a probability and not a percent-good/bad score. Cards show the exact magnitude relative to the reference, using percentage points for rates and percentage changes for claims. Stock scores remain unaffected. Failed/stale observations retain their original dates and lose current-condition colors.

## Inspectability and checks

Each card contains its color rules, benchmark dates, sample count, minimum and maximum, original source links, formulas, retrieval date, a paginated table of every exported observation and a CSV download. Charts can be changed without losing access to the full data. Existing Buffett/CAPE methodology and coverage limitations remain visible.

Refresh with `python scripts/macro.py`. The versioned macro cache upgrades earlier short histories even if already fetched that day. A failed upgrade retains the old data with an explicit failure status; missing reference coverage prevents derived classifications.

Run `python -m pytest tests/test_macro.py tests/test_market_valuation.py` and `node tests/macro-model.test.cjs`. Tests cover long-history cache migration, transformation, missing data, reference windows, quantile boundaries, dated comparisons, leap-day handling, direction interpretation and rules across indicator families. Browser verification checks all eleven shared chart controls and evidence-table pagination. No deployment is performed by these checks.
