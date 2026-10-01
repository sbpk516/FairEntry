"""Dated price context, independent of holdings values and screening verdicts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import math


def summarize(history, now, currency=None):
    import pandas as pd
    close = history['Close'].copy()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    # Exclude today's potentially unfinished session and any future observations.
    close = close[close.index.date < now.date()].sort_index()
    close = close[~close.index.duplicated(keep='last')]
    close = close[close.map(lambda x: math.isfinite(x) and x > 0)]
    if close.empty:
        raise ValueError('No completed daily closes')
    last = close.index[-1]
    weekly = close.resample('W-FRI').last()
    weekly = weekly[weekly.index.date < now.date()].dropna()
    monthly = close.resample(pd.offsets.MonthEnd()).last()
    monthly = monthly[monthly.index.date < now.date()].dropna()
    price = float(close.iloc[-1])
    def average(series, count):
        # Missing whole periods must not silently shorten the requested window.
        window = series.iloc[-count:]
        return float(window.mean()) if len(window) == count and window.notna().all() else None
    # Reindex before checking completeness to retain gaps in the history.
    weekly = weekly.reindex(pd.date_range(weekly.index.min(), weekly.index.max(), freq='W-FRI')) if len(weekly) else weekly
    monthly = monthly.reindex(pd.date_range(monthly.index.min(), monthly.index.max(), freq=pd.offsets.MonthEnd())) if len(monthly) else monthly
    ma200, ma20, ma36 = average(weekly, 200), average(monthly, 20), average(monthly, 36)
    high = float(close.max())
    return {'price': price, 'currency': currency, 'as_of': last.date().isoformat(),
            'history_start': close.index[0].date().isoformat(), 'history_end': last.date().isoformat(),
            'sma_200w': ma200, 'sma_20m': ma20, 'sma_36m': ma36,
            'distance_200w_pct': (price / ma200 - 1) * 100 if ma200 else None,
            'distance_20m_pct': (price / ma20 - 1) * 100 if ma20 else None,
            'distance_36m_pct': (price / ma36 - 1) * 100 if ma36 else None,
            'history_high': high, 'high_date': close.idxmax().date().isoformat(),
            'below_high_pct': (1 - price / high) * 100,
            'basis': 'Split-adjusted daily closes; cash dividends not adjusted. Simple averages of completed weekly/monthly closes. Highest close in available history, not a verified all-time intraday high.'}


def fetch_price(ticker, now):
    import yfinance as yf
    security = yf.Ticker(ticker)
    history = security.history(period='max', auto_adjust=False, timeout=25)
    if history.empty:
        raise ValueError('No price history')
    metadata = security.history_metadata or {}
    return summarize(history, now, metadata.get('currency')) | {
        'source': 'Yahoo Finance', 'url': 'https://finance.yahoo.com/quote/' + ticker + '/history/',
        'retrieved_at': now.isoformat()}


def enrich_prices(tickers, ledger, now, refresh=True):
    cache = ledger.get('price_levels_v1', {})
    def due(ticker):
        stamp = cache.get(ticker, {}).get('attempted_at')
        return not stamp or (now - datetime.fromisoformat(stamp)).total_seconds() >= 86400
    def fetch(ticker):
        try:
            return ticker, fetch_price(ticker, now) | {'attempted_at': now.isoformat()}
        except Exception as exc:
            return ticker, cache.get(ticker, {}) | {'attempted_at': now.isoformat(), 'error': type(exc).__name__}
    if refresh:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for count, (ticker, result) in enumerate(pool.map(fetch, [t for t in sorted(set(tickers)) if due(t)]), 1):
                cache[ticker] = result
                if count % 100 == 0:
                    ledger.put('price_levels_v1', cache)
        ledger.put('price_levels_v1', cache)
    return {ticker: cache.get(ticker, {}) for ticker in tickers}
