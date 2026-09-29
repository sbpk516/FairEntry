"""SEC 13F ingestion with explicit filing identity and amendment semantics."""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import quote

import requests


def xml(text):
    root = ET.fromstring(text)
    for element in root.iter():
        element.tag = element.tag.split('}')[-1].lower()
    return root


def field(root, name):
    return (root.findtext('.//' + name.lower()) or '').strip()


def number(text):
    try:
        value = float(text.replace(',', ''))
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def identity(row):
    return '|'.join(str(row.get(k) or '').strip().upper()
                    for k in ('cusip', 'share_class', 'put_call', 'share_type'))


def parse_table(text, filed):
    root = xml(text)
    rows = []
    # SEC changed the reporting unit on Jan 3, 2023; never infer cost from value.
    multiplier = 1 if filed >= '2023-01-03' else 1000
    for element in root.iter('infotable'):
        row = {'company': field(element, 'nameofissuer'),
               'cusip': field(element, 'cusip').upper(),
               'share_class': field(element, 'titleofclass'),
               'put_call': field(element, 'putcall').upper() or None,
               'share_type': field(element, 'sshprnamttype').upper(),
               'shares': number(field(element, 'sshprnamt')),
               'reported_value': number(field(element, 'value')),
               'purchase_price': None, 'trade_date': None}
        if not re.fullmatch(r'[A-Z0-9*@#]{9}', row['cusip']) or not row['company']:
            raise ValueError('Invalid information-table identity')
        if row['reported_value'] is not None:
            row['reported_value'] *= multiplier
        row['security_id'] = identity(row)
        rows.append(row)
    return rows


def aggregate(rows):
    result = {}
    for row in rows:
        key = row['security_id']
        if key not in result:
            result[key] = dict(row)
        else:
            for f in ('shares', 'reported_value'):
                a, b = result[key][f], row[f]
                result[key][f] = a + b if a is not None and b is not None else None
    return result


def filing_rows(recent, today):
    result = []
    for i, form in enumerate(recent.get('form', [])):
        if form not in ('13F-HR', '13F-HR/A'):
            continue
        row = {key: (recent.get(key) or [''] * len(recent['form']))[i]
               for key in ('accessionNumber', 'filingDate', 'reportDate', 'primaryDocument', 'acceptanceDateTime')}
        if row['filingDate'] > today.isoformat():
            continue
        row['form'] = form
        result.append(row)
    return result


class SecClient:
    def __init__(self):
        contact = os.environ.get('SEC_CONTACT_EMAIL', '').strip()
        if '@' not in contact or contact.endswith('.local'):
            raise ValueError('Configure SEC_CONTACT_EMAIL with a real contact email to enable SEC ingestion')
        self.headers = {'User-Agent': 'FairEntry Investors ' + contact}
        self.session = requests.Session()
        self.last_request = 0.0

    def get(self, url):
        time.sleep(max(0, .25 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        response = self.session.get(url, headers=self.headers, timeout=25)
        response.raise_for_status()
        return response

    def discover(self, source, today, periods=4):
        data = self.get(f"https://data.sec.gov/submissions/CIK{int(source['cik']):010d}.json").json()
        if source['expected_name'] not in data.get('name', '').upper():
            raise ValueError('SEC filer name does not match configured reporting entity')
        recent = data.get('filings', {}).get('recent', {})
        rows = filing_rows(recent, today)
        # Large filers can roll 13Fs out of recent submissions. Read official history files.
        for archive in data.get('filings', {}).get('files', []):
            if len({r['reportDate'] for r in rows if r['reportDate']}) >= periods + 1:
                cutoff = sorted({r['reportDate'] for r in rows if r['reportDate']})[-periods]
                if archive.get('filingTo', '') < cutoff:
                    break
            name = archive['name']
            if not re.fullmatch(r'CIK\d+-submissions-\d+\.json', name):
                continue
            rows.extend(filing_rows(self.get('https://data.sec.gov/submissions/' + name).json(), today))
        dates = sorted({r['reportDate'] for r in rows if r['reportDate']}, reverse=True)[:periods]
        return data['name'], sorted([r for r in rows if r['reportDate'] in dates],
                                   key=lambda r: (r['filingDate'], r['accessionNumber']))

    def download(self, source, meta):
        accession = meta['accessionNumber']
        base = f"https://www.sec.gov/Archives/edgar/data/{int(source['cik'])}/{accession.replace('-', '')}"
        index = self.get(base + '/index.json').json()['directory']['item']
        primary_name = meta['primaryDocument'].split('/')[-1]
        primary_text = self.get(base + '/' + quote(primary_name)).text
        cover = xml(primary_text)
        period = field(cover, 'reportcalendarorquarter') or meta['reportDate']
        if re.fullmatch(r'\d{2}-\d{2}-\d{4}', period):
            month, day, year = period.split('-')
            period = f'{year}-{month}-{day}'
        date.fromisoformat(period)
        if period != meta['reportDate']:
            raise ValueError('Cover and submissions report periods disagree')
        amendment = field(cover, 'amendmenttype').upper()
        if meta['form'].endswith('/A') and amendment not in ('RESTATEMENT', 'NEW HOLDINGS'):
            raise ValueError('Unknown amendment type; comparison withheld')
        entries = number(field(cover, 'tableentrytotal'))
        if entries is None:
            raise ValueError('Cannot verify information-table completeness')
        rows, documents = [], []
        for item in index:
            name = item['name']
            if name == primary_name or not name.lower().endswith('.xml'):
                continue
            text = self.get(base + '/' + quote(name)).text
            parsed = parse_table(text, meta['filingDate'])
            if parsed:
                rows.extend(parsed)
                documents.append(base + '/' + quote(name))
        if len(rows) != entries:
            raise ValueError('Information-table row count does not match cover; incomplete report withheld')
        total = number(field(cover, 'tablevaluetotal'))
        if total is not None and meta['filingDate'] < '2023-01-03':
            total *= 1000
        values = [r['reported_value'] for r in rows]
        if total is not None and all(v is not None for v in values) and abs(sum(values) - total) > max(len(rows), 1):
            raise ValueError('Information-table value total does not match cover')
        return {'accession': accession, 'period': period, 'filed': meta['filingDate'],
                'published_at': meta.get('acceptanceDateTime') or meta['filingDate'],
                'form': meta['form'], 'amendment': amendment or None,
                'report_type': field(cover, 'reporttype'),
                'reported_total': total, 'holdings': aggregate(rows),
                'url': base + '/' + accession + '-index.html', 'documents': documents,
                'content_hash': hashlib.sha256(primary_text.encode()).hexdigest()}


def snapshots(filings):
    """Restatements replace; additions extend. Incomplete/ambiguous periods fail closed."""
    reports = {}
    for filing in sorted(filings, key=lambda f: (f['filed'], f['accession'])):
        period = filing['period']
        previous = reports.get(period)
        if filing['form'] == '13F-HR' or filing['amendment'] == 'RESTATEMENT':
            reports[period] = {**filing, 'holdings': dict(filing['holdings']),
                               'accessions': [filing['accession']], 'revised': filing['form'].endswith('/A')}
        elif filing['amendment'] == 'NEW HOLDINGS':
            if previous is None or previous.get('incomplete'):
                reports[period] = {**filing, 'incomplete': True, 'holdings': {}, 'accessions': [filing['accession']]}
            else:
                overlap = set(previous['holdings']) & set(filing['holdings'])
                if overlap:
                    reports[period] = {**filing, 'incomplete': True, 'holdings': {}, 'accessions': [filing['accession']]}
                else:
                    reports[period] = {**previous, 'holdings': {**previous['holdings'], **filing['holdings']},
                                       'filed': filing['filed'], 'published_at': filing['published_at'],
                                       'url': filing['url'], 'revised': True,
                                       'accessions': previous['accessions'] + [filing['accession']]}
    for report in reports.values():
        values = [r['reported_value'] for r in report['holdings'].values()]
        total = sum(values) if all(v is not None for v in values) else None
        for row in report['holdings'].values():
            row['weight_pct'] = row['reported_value'] / total * 100 if total and row['reported_value'] is not None else None
        report['covered_value'] = total
    return sorted(reports.values(), key=lambda r: r['period'])


def compare(current, previous, actions=()):
    if not previous or current.get('incomplete') or previous.get('incomplete'):
        return []
    a, b = date.fromisoformat(previous['period']), date.fromisoformat(current['period'])
    if not 70 <= (b - a).days <= 100 or current.get('report_type') != previous.get('report_type'):
        return []
    rows = []
    for key in sorted(set(current['holdings']) | set(previous['holdings'])):
        old, new = previous['holdings'].get(key), current['holdings'].get(key)
        before, after = old['shares'] if old else 0, new['shares'] if new else 0
        if before is None or after is None:
            continue
        row = dict(new or old)
        adjustment = next((x for x in actions if x['cusip'] == row['cusip'] and a.isoformat() < x['date'] <= b.isoformat()), None)
        adjusted = before * adjustment['ratio'] if adjustment else before
        if after == adjusted:
            continue
        kind = 'new' if old is None else 'exit' if new is None else 'increase' if after > adjusted else 'reduction'
        rows.append({**row, 'kind': kind, 'prior_shares': before, 'shares': after,
                     'reported_value': new['reported_value'] if new else None,
                     'weight_pct': new.get('weight_pct') if new else None,
                     'share_change_pct': (after / adjusted - 1) * 100 if adjusted else None,
                     'prior_value': old['reported_value'] if old else None,
                     'period': current['period'], 'prior_period': previous['period'],
                     'filed': current['filed'], 'published_at': current['published_at'],
                     'url': current['url'], 'revised': current.get('revised', False) or previous.get('revised', False),
                     'corporate_action': adjustment,
                     'interpretation': 'Reported share-count change; transactions and unreviewed corporate actions are not separable from 13F alone.'})
    return rows
