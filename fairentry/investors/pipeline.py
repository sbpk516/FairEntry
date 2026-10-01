"""Refresh disclosures and export a research dashboard; never creates trades."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .financials import enrich
from .prices import enrich_prices
from .ledger import Ledger
from .research import fetch_feed, map_cusips, validate_manual
from .sec import SecClient, snapshots, compare

ROOT = Path(__file__).resolve().parents[2]


def email_configuration():
    recipient = os.environ.get('INVESTORS_ALERT_EMAIL')
    transport = 'Resend' if os.environ.get('RESEND_API_KEY') else 'SMTP' if os.environ.get('SMTP_HOST') else None
    return {'enabled': os.environ.get('INVESTORS_EMAIL_ENABLED', '').lower() == 'true',
            'recipient_configured': bool(recipient), 'transport': transport,
            'configured': bool(recipient and transport)}


def sender(event):
    from ..alerts import _send_email
    config = email_configuration()
    if not config['enabled'] or not config['configured']:
        return False
    row = event['payload']
    return _send_email(f"FairEntry Investors: {event['source']} disclosed {row['kind']} — {row['company']}", [
        f"Reporting manager: {event['source']}",
        f"Newly processed public disclosure: {row['published_at']}",
        f"Holdings date: {row['period']}; preceding comparable report: {row['prior_period']}",
        f"{row['company']} | CUSIP {row['cusip']} | {row['share_class']} | {row.get('put_call') or 'no option designation'}",
        f"Reported {row['kind']}: {row['prior_shares']} to {row['shares']} ({row['share_type']})",
        'Share changes do not reveal transaction dates, prices, or every trade. Corporate actions may affect share counts.',
        'Reported position values are not purchase costs. Options values are not option premiums.', row['url'],
    ], recipient=os.environ.get('INVESTORS_ALERT_EMAIL'),
       idempotency_key='investors-' + event['id'])


def refresh_source(source, cfg, ledger, now, sec, force=False):
    key = 'source:' + source['id']
    state = ledger.get(key, {})
    last = state.get('attempted_at')
    if not force and last and (now - datetime.fromisoformat(last)).total_seconds() < cfg['poll_minutes'] * 60:
        return state
    state = {**state, 'attempted_at': now.isoformat()}
    if source.get('cik'):
        try:
            if sec is None:
                raise ValueError('SEC_CONTACT_EMAIL is required; SEC ingestion not configured')
            entity, found = sec.discover(source, now.date(), cfg['history_periods'])
            if not found:
                raise ValueError('No 13F holdings reports found; current ownership unknown')
            existing = ledger.filings(source['id'])
            known = {f['accession'] for f in existing}
            downloaded = [sec.download(source, meta) for meta in found if meta['accessionNumber'] not in known]
            # Do not advance baseline or emit changes after a partial/failed source download.
            combined = existing + downloaded
            reports = snapshots(combined)
            if not reports or any(r.get('incomplete') for r in reports[-2:]):
                raise ValueError('Incomplete amendment chain; no current comparison published')
            ledger.save_filings(source['id'], downloaded)
            initial = not ledger.get('periods:' + source['id'])
            actions = ledger.get('corporate_actions', [])
            for i, report in enumerate(reports):
                changes = compare(report, reports[i - 1] if i else None, actions)
                too_old = (now.date() - datetime.fromisoformat(report['filed']).date()).days > 7
                comparison_report = {**report, 'revised': report.get('revised', False) or (bool(i) and reports[i - 1].get('revised', False))}
                ledger.record_period(source['id'], comparison_report, changes, now.isoformat(),
                                     historical=initial or source.get('historical_only', False) or too_old)
            state.update({'holdings_status': 'available', 'entity_verified': entity,
                          'holdings_checked_at': now.isoformat(), 'holdings_error': None})
        except Exception as exc:
            state.update({'holdings_status': 'unavailable',
                          'holdings_error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
    else:
        state['holdings_status'] = 'not a holdings source'
    if source.get('feed'):
        try:
            posts = fetch_feed(source, cfg['research_aliases'], now)
            ledger.save_research(posts)
            state.update({'research_status': 'available', 'research_checked_at': now.isoformat(), 'research_error': None})
        except Exception as exc:
            state.update({'research_status': 'unavailable', 'research_error': type(exc).__name__})
    else:
        state['research_status'] = 'Original-source links and reviewed imports; no automated feed configured'
    ledger.put(key, state)
    return state


def build_investors(*, root=ROOT, db_path=None, now=None, force=False, send_alerts=False,
                    enrich_limit=20, refresh=True, optional=False):
    now = now or datetime.now(timezone.utc)
    cfg = yaml.safe_load((root / 'config/investors.yaml').read_text(encoding='utf-8'))
    sources = [s for s in cfg['sources'] if optional or not s.get('optional')]
    manual = json.loads((root / 'config/investor_research.json').read_text(encoding='utf-8'))
    imported = validate_manual(manual, {s['id'] for s in sources})
    ledger = Ledger(db_path or root / 'data/investors.db')
    try:
        ledger.put('corporate_actions', manual.get('corporate_actions', []))
        ledger.sync_manual([p for p in imported if p['published_at'][:10] <= now.date().isoformat()])
        try:
            sec = SecClient() if refresh else None
        except ValueError:
            sec = None
        output_sources, all_changes, cusips = [], [], []
        for source in sources:
            state = refresh_source(source, cfg, ledger, now, sec, force) if refresh else ledger.get('source:' + source['id'], {})
            reports = snapshots(ledger.filings(source['id']))
            latest = reports[-1] if reports else None
            age = (now.date() - datetime.fromisoformat(latest['period']).date()).days if latest else None
            historical = bool(source.get('historical_only') or (age is not None and age > cfg['holdings_max_age_days']))
            current = bool(latest and not latest.get('incomplete') and not historical and state.get('holdings_status') == 'available')
            changes = compare(latest, reports[-2] if len(reports) > 1 else None, manual.get('corporate_actions', [])) if latest else []
            all_changes.extend({**c, 'source_id': source['id'], 'historical': historical, 'source_available': current} for c in changes)
            for report in reports:
                cusips.extend(r['cusip'] for r in report['holdings'].values())
            output_sources.append({**source, **state, 'historical': historical, 'current_coverage': current,
                                   'latest_period': latest['period'] if latest else None,
                                   'reports': reports, 'changes': changes})
        mappings = ledger.get('mappings', {})
        mapping_errors = map_cusips(cusips, mappings, now, cfg['mapping_batch_limit']) if refresh else []
        for entry in manual.get('security_mappings', []):
            mappings[entry['cusip']] = {**entry, 'source': 'Reviewed mapping', 'retrieved_at': now.isoformat()}
        ledger.put('mappings', mappings)
        candidates = {}

        def candidate(ticker):
            return candidates.setdefault(ticker, {'ticker': ticker, 'positions': [], 'research': [], 'history': []})

        for source in output_sources:
            for report in source['reports']:
                change_by_id = {r['security_id']: r for r in source['changes']}
                for row in report['holdings'].values():
                    mapping = mappings.get(row['cusip'], {})
                    row['ticker'] = mapping.get('ticker')
                    row['mapping'] = mapping or None
                    row['change'] = change_by_id.get(row['security_id']) if report['period'] == source['latest_period'] else None
                    if not row['ticker'] or row['put_call'] or row['share_type'] != 'SH':
                        continue
                    record = {**row, 'source_id': source['id'], 'period': report['period'],
                              'filed': report['filed'], 'published_at': report['published_at'], 'url': report['url'],
                              'current_coverage': source['current_coverage'], 'retrieved_at': source.get('holdings_checked_at')}
                    candidate(row['ticker'])['history'].append(record)
                    if source['current_coverage'] and report['period'] == source['latest_period']:
                        candidate(row['ticker'])['positions'].append(record)
        research = [p for p in ledger.research() if p['source_id'] in {s['id'] for s in sources}]
        for post in research:
            for ticker in post['tickers']:
                candidate(ticker)['research'].append(post)
        # Historical-only ownership is not a current idea. Research can independently establish an idea.
        candidates = {t: c for t, c in candidates.items() if c['positions'] or c['research']}
        ranked = sorted(candidates, key=lambda t: (-len(candidates[t]['positions']), -len(candidates[t]['research']), t))
        financials, financial_errors = enrich(ranked, ledger, root, now, enrich_limit if refresh else 0)
        price_tickers = set(candidates)
        for source in output_sources:
            for report in source['reports']:
                price_tickers.update(r['ticker'] for r in report['holdings'].values()
                                     if r.get('ticker') and not r.get('put_call') and r.get('share_type') == 'SH')
        price_levels = enrich_prices(price_tickers, ledger, now, refresh=refresh)
        try:
            board = json.loads((root / 'web/data/board.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            board = {}
        board_stocks = {s['ticker']: s for s in board.get('stocks', []) + board.get('emerging_candidates', [])}
        for ticker, stock in candidates.items():
            financial = financials.get(ticker, {})
            stock.update(financial)
            stock['company'] = stock.get('company') or next((p['company'] for p in stock['positions']), ticker)
            existing = board_stocks.get(ticker)
            stock['fairentry'] = ({k: existing.get(k) for k in ('verdict', 'valuation', 'card_summary', 'vetoes', 'business_durability')}
                                 | {'as_of': board.get('meta', {}).get('generated_at')}) if existing else None
        if send_alerts:
            ledger.deliver(sender, now.isoformat())
        events = ledger.events()
        return {'generated_at': now.isoformat(), 'sources': output_sources, 'candidates': list(candidates.values()),
                'price_levels': price_levels,
                'research': sorted(research, key=lambda p: p['published_at'], reverse=True),
                'changes': sorted(all_changes, key=lambda c: c['published_at'], reverse=True),
                'defaults': cfg['defaults'], 'financial_max_age_days': cfg['financial_max_age_days'],
                'research_max_age_days': cfg['research_max_age_days'], 'holdings_max_age_days': cfg['holdings_max_age_days'],
                'poll_minutes': cfg['poll_minutes'], 'email': email_configuration() | {'counts': dict(Counter(e['status'] for e in events))},
                'mapping_errors': mapping_errors, 'financial_errors': financial_errors,
                'score_effect': 0, 'verdict_effect': 'none'}
    finally:
        ledger.close()


def write_investors(output_path=None, **kwargs):
    result = build_investors(**kwargs)
    path = Path(output_path or ROOT / 'web/data/investors.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)
    return path
