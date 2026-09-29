"""Persistent filing, research and alert state. Baseline imports never notify."""
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


class Ledger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS filings(source TEXT, accession TEXT, payload TEXT,
                PRIMARY KEY(source,accession));
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE IF NOT EXISTS research(id TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, source TEXT, period TEXT,
                status TEXT, payload TEXT, updated_at TEXT);
        ''')

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute('SELECT payload FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, allow_nan=False)))

    def filings(self, source):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM filings WHERE source=?', (source,))]

    def save_filings(self, source, filings):
        with self.db:
            for filing in filings:
                self.db.execute('INSERT OR IGNORE INTO filings VALUES(?,?,?)',
                                (source, filing['accession'], json.dumps(filing, allow_nan=False)))

    def save_research(self, posts):
        with self.db:
            for item in posts:
                self.db.execute('INSERT OR REPLACE INTO research VALUES(?,?)', (item['id'], json.dumps(item)))

    def research(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM research')]

    def sync_manual(self, posts):
        old = self.get('manual-research-ids', [])
        with self.db:
            self.db.executemany('DELETE FROM research WHERE id=?', [(key,) for key in old])
        self.save_research(posts)
        self.put('manual-research-ids', [p['id'] for p in posts])

    def events(self):
        return [dict(r) | {'payload': json.loads(r['payload'])} for r in
                self.db.execute('SELECT * FROM alerts ORDER BY updated_at DESC')]

    def expire(self, now):
        today = datetime.fromisoformat(now).date()
        with self.db:
            for event in self.events():
                if event['status'] in ('pending', 'reserved') and (today - datetime.fromisoformat(event['payload']['filed']).date()).days > 7:
                    self.db.execute("UPDATE alerts SET status='expired',updated_at=? WHERE id=?", (now,event['id']))

    def record_period(self, source, report, changes, now, historical=False):
        key = 'periods:' + source
        seen = self.get(key, [])
        period = report['period']
        # Seen periods include amendment-only checks, preventing retrospective duplicate trades.
        if period in seen:
            # Cancel unsent alerts for this period when revisions alter either comparison report.
            if report.get('revised') or any(c.get('revised') for c in changes):
                with self.db:
                    self.db.execute("UPDATE alerts SET status='superseded',updated_at=? WHERE source=? AND period=? AND status='pending'",
                                    (now, source, period))
            return
        baseline = not seen
        should_alert = not baseline and not historical and not report.get('revised') and period > max(seen)
        with self.db:
            for change in changes:
                event_id = hashlib.sha256(f"{source}|{period}|{change['security_id']}".encode()).hexdigest()
                status = 'pending' if should_alert and not change.get('revised') else 'suppressed'
                self.db.execute('INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,?)',
                                (event_id, source, period, status, json.dumps(change), now))
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',
                            (key, json.dumps(sorted(set(seen + [period])))))

    def deliver(self, sender, now):
        self.expire(now)
        # An interrupted send is ambiguous: do not retry it automatically and risk duplicates.
        with self.db:
            self.db.execute("UPDATE alerts SET status='delivery_unknown' WHERE status='sending'")
        for event in self.events():
            if event['status'] != 'pending':
                continue
            with self.db:
                self.db.execute("UPDATE alerts SET status='sending',updated_at=? WHERE id=?", (now, event['id']))
            try:
                sent = sender(event)
                status = 'sent' if sent else 'pending'
            except Exception:
                status = 'delivery_unknown'
            with self.db:
                self.db.execute('UPDATE alerts SET status=?,updated_at=? WHERE id=?', (status, now, event['id']))
            if status != 'sent':
                break  # configuration/transport failure must not churn through the entire outbox

    def reserve(self, token, now):
        """Persist BEFORE remote delivery; CI must upload this database before dispatch."""
        self.expire(now)
        key = 'delivery-reservation:' + token
        if self.get(key) is not None:
            return
        with self.db:
            self.db.execute("UPDATE alerts SET status='delivery_unknown' WHERE status IN ('sending','reserved')")
            ids = [r[0] for r in self.db.execute("SELECT id FROM alerts WHERE status='pending'")]
            self.db.execute("UPDATE alerts SET status='reserved',updated_at=? WHERE status='pending'", (now,))
            self.db.execute('INSERT INTO state VALUES(?,?)', (key, json.dumps(ids)))

    def dispatch_reserved(self, token, sender, now):
        self.expire(now)
        ids = set(self.get('delivery-reservation:' + token, []))
        for event in self.events():
            if event['id'] not in ids or event['status'] != 'reserved':
                continue
            with self.db:
                self.db.execute("UPDATE alerts SET status='sending',updated_at=? WHERE id=?", (now,event['id']))
            try:
                status = 'sent' if sender(event) else 'pending'
            except Exception:
                status = 'delivery_unknown'
            with self.db:
                self.db.execute('UPDATE alerts SET status=?,updated_at=? WHERE id=?', (status,now,event['id']))
            if status != 'sent':
                break
