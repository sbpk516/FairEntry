"""Reuse canonical metrics; optionally enrich research-only names via Yahoo."""
from __future__ import annotations

import math
from datetime import datetime

from ..store import Store

FIELDS = ('price', 'profit_margin', 'rev_growth_qoq', 'debt_eq', 'pfcf_ratio', 'fwd_pe', 'perf_year')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def from_store(tickers, path):
    if not path.exists():
        return {}
    with Store(path) as store:
        names = {s['ticker']: s['company'] for s in store.securities()}
        return {ticker: {'company': names.get(ticker), 'metrics': {
            k: v for k, v in store.metrics_for(ticker).items() if k in FIELDS and finite(v.get('value'))}}
                for ticker in tickers}


def fetch_yahoo(ticker, now):
    import yfinance as yf
    security = yf.Ticker(ticker)
    history = security.history(period='1y', auto_adjust=True, actions=True)
    if history.empty:
        raise ValueError('No price history')
    history = history[history.index.date <= now.date()]
    if history.empty:
        raise ValueError('No published price history')
    price = float(history['Close'].iloc[-1])
    high = float(history['Close'].max())
    info = security.info
    retrieved = now.isoformat()
    price_date = history.index[-1].date().isoformat()
    values = {'price': price, 'drawdown': (1 - price / high) * 100 if high > 0 else None,
              'profit_margin': info.get('profitMargins', 0) * 100 if info.get('profitMargins') is not None else None,
              'rev_growth_qoq': info.get('revenueGrowth', 0) * 100 if info.get('revenueGrowth') is not None else None,
              'debt_eq': info['debtToEquity'] / 100 if finite(info.get('debtToEquity')) else None,
              'fwd_pe': info.get('forwardPE')}
    fcf, cap = info.get('freeCashflow'), info.get('marketCap')
    values['pfcf_ratio'] = cap / fcf if finite(fcf) and finite(cap) and fcf > 0 else None
    return {'company': info.get('longName') or info.get('shortName'),
            'metrics': {k: {'value': v, 'source': 'Yahoo Finance', 'fetched_at': retrieved,
                            'observed_at': price_date if k in ('price', 'drawdown') else None,
                            'url': 'https://finance.yahoo.com/quote/' + ticker + '/',
                            'basis': 'Trailing adjusted closing-price high over one year' if k == 'drawdown' else 'Provider-reported; fiscal period may differ from retrieval date'}
                        for k, v in values.items() if finite(v)}}


def enrich(tickers, ledger, root, now, limit=20):
    stored = from_store(tickers, root / 'data/fairentry.db')
    cache = ledger.get('financials', {})
    attempted = 0
    errors = []
    for ticker in tickers:
        prior = cache.get(ticker, {})
        age = (now - datetime.fromisoformat(prior['retrieved_at'])).days if prior.get('retrieved_at') else 999
        if attempted < limit and age >= 3:
            attempted += 1
            try:
                cache[ticker] = {**fetch_yahoo(ticker, now), 'retrieved_at': now.isoformat()}
            except Exception as exc:
                errors.append({'ticker': ticker, 'error': type(exc).__name__})
    ledger.put('financials', cache)
    out = {}
    for ticker in tickers:
        source = stored.get(ticker, {})
        yahoo = cache.get(ticker, {})
        metrics = dict(yahoo.get('metrics', {}))
        for k, v in source.get('metrics', {}).items():
            if v.get('fetched_at', '') > metrics.get(k, {}).get('fetched_at', ''):
                metrics[k] = v
        out[ticker] = {'company': source.get('company') or yahoo.get('company'), 'metrics': metrics}
    return out, errors
