"""Public research discovery and conservative security mapping."""
from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests


def parse_feed(text, source, aliases, now):
    root = ET.fromstring(text)
    posts = []
    for entry in root.findall('./channel/item'):
        title = (entry.findtext('title') or '').strip()
        url = (entry.findtext('link') or '').strip()
        if urlparse(url).scheme != 'https' or urlparse(url).hostname != urlparse(source['feed']).hostname:
            continue
        try:
            published = parsedate_to_datetime(entry.findtext('pubDate') or '').astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if published > now:
            continue
        # Only explicit titles; never mine passing body references, memes or imply a bullish view.
        tickers = [ticker for ticker, names in aliases.items()
                   if any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', title, re.I) for name in names)]
        posts.append({'id': hashlib.sha256(url.encode()).hexdigest()[:24], 'source_id': source['id'],
                      'title': title, 'url': url, 'published_at': published.isoformat(),
                      'tickers': tickers, 'evidence_type': 'research', 'ownership': 'unknown',
                      'thesis': None, 'stance': 'not inferred', 'access': 'Public title/link; article may require subscription',
                      'association': 'Company named in article title' if tickers else 'Unclassified research inbox'})
    return posts


def fetch_feed(source, aliases, now):
    response = requests.get(source['feed'], timeout=25, headers={'User-Agent': 'FairEntry Research/1.0'})
    response.raise_for_status()
    return parse_feed(response.content, source, aliases, now)


def validate_manual(data, sources):
    """Only explicitly public summaries may enter the publishable site artifact."""
    items = []
    for item in data.get('items', []):
        if item.get('source_id') not in sources or item.get('evidence_type') != 'research':
            raise ValueError('Research requires an accepted source and evidence_type=research')
        if not item.get('public_summary'):
            continue
        if not str(item.get('url', '')).startswith('https://') or not item.get('title'):
            raise ValueError('Research requires title and HTTPS original source')
        datetime.fromisoformat(item['published_at'].replace('Z', '+00:00'))
        for field in ('risks', 'invalidation'):
            if item.get(field) is not None and (not isinstance(item[field], list) or
                                                 any(not isinstance(value, str) for value in item[field])):
                raise ValueError(f'Research {field} must be a list of strings')
        if not item.get('tickers') or any(not re.fullmatch(r'[A-Z0-9.\-^]{1,12}', t) for t in item['tickers']):
            raise ValueError('Research requires explicit ticker association')
        items.append({k: item.get(k) for k in ('source_id', 'title', 'url', 'published_at', 'tickers', 'thesis',
                                              'risks', 'invalidation', 'price_decline_reason')}
                     | {'id': hashlib.sha256(item['url'].encode()).hexdigest()[:24],
                        'evidence_type': 'research', 'ownership': 'unknown', 'stance': item.get('stance', 'not inferred'),
                        'access': 'Public summary; consult original source', 'association': 'Explicit reviewed company analysis'})
    for action in data.get('corporate_actions', []):
        if action.get('ratio', 0) <= 0 or not action.get('url', '').startswith('https://'):
            raise ValueError('Corporate actions require a positive split ratio and source URL')
        datetime.fromisoformat(action['date'])
    for mapping in data.get('security_mappings', []):
        if not mapping.get('url', '').startswith('https://') or not mapping.get('cusip') or not mapping.get('ticker'):
            raise ValueError('Security mappings require CUSIP, ticker and source URL')
    return items


def map_cusips(cusips, cache, now, batch_limit=20):
    pending = [c for c in dict.fromkeys(cusips) if c not in cache or
               (now - datetime.fromisoformat(cache[c]['retrieved_at'])).days > 30]
    errors = []
    for offset in range(0, min(len(pending), batch_limit * 5), 5):
        if offset:
            time.sleep(2.5)  # below the public 25-requests/minute allowance
        batch = pending[offset:offset + 5]
        try:
            response = requests.post('https://api.openfigi.com/v3/mapping', timeout=25,
                                     json=[{'idType': 'ID_CUSIP', 'idValue': c, 'exchCode': 'US'} for c in batch])
            response.raise_for_status()
            result = response.json()
            if len(result) != len(batch):
                raise ValueError('Mapping response length mismatch')
            for cusip, item in zip(batch, result):
                choices = {d.get('ticker'): d for d in item.get('data', [])
                           if d.get('marketSector') == 'Equity' and d.get('ticker')}
                selected = next(iter(choices.values())) if len(choices) == 1 else None
                cache[cusip] = {'ticker': selected['ticker'].replace('/', '-') if selected else None,
                                'name': selected.get('name') if selected else None,
                                'retrieved_at': now.isoformat(), 'source': 'OpenFIGI CUSIP mapping',
                                'url': 'https://www.openfigi.com/',
                                'status': 'mapped' if selected else 'ambiguous or unavailable'}
        except Exception as exc:
            errors.append(type(exc).__name__)
            break
    return errors
