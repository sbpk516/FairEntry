from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from fairentry.investors.sec import parse_table, snapshots, compare, aggregate, filing_rows, SecClient
from fairentry.investors.ledger import Ledger
from fairentry.investors.research import parse_feed, validate_manual
from fairentry.investors.pipeline import refresh_source, email_configuration


def table(shares='10', value='100', put='', cls='COM', cusip='123456789'):
    return f'''<informationTable xmlns="urn:sec"><infoTable><nameOfIssuer>Example</nameOfIssuer>
    <titleOfClass>{cls}</titleOfClass><cusip>{cusip}</cusip><value>{value}</value>
    <shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <putCall>{put}</putCall></infoTable></informationTable>'''


def filing(period, shares='10', accession='a', amendment=None, value='100', cusip='123456789'):
    return {'period': period, 'filed': '2026-08-14' if period == '2026-06-30' else '2026-05-14',
            'published_at': '2026-08-14T16:00:00Z', 'accession': accession,
            'form': '13F-HR/A' if amendment else '13F-HR', 'amendment': amendment,
            'report_type': '13F HOLDINGS REPORT', 'url': 'https://www.sec.gov/example',
            'holdings': aggregate(parse_table(table(shares,value,cusip=cusip), '2026-08-14'))}


def test_parser_preserves_classes_options_unknowns_and_units():
    a = parse_table(table(), '2022-11-01')[0]
    b = parse_table(table(), '2023-02-01')[0]
    assert a['reported_value'] == 100_000 and b['reported_value'] == 100
    assert a['security_id'] != parse_table(table(put='PUT'), '2023-02-01')[0]['security_id']
    assert a['security_id'] != parse_table(table(cls='CL A'), '2023-02-01')[0]['security_id']
    unknown = parse_table(table(shares='', value=''), '2026-08-14')[0]
    assert unknown['shares'] is None and unknown['reported_value'] is None
    assert unknown['purchase_price'] is None and unknown['trade_date'] is None


def test_price_changes_are_not_share_changes_and_missing_comparison_is_not_new():
    previous, current = snapshots([filing('2026-03-31'),filing('2026-06-30',value='200',accession='b')])
    assert compare(current,previous) == []
    assert compare(current,None) == []
    current['holdings'][next(iter(current['holdings']))]['shares'] = None
    assert compare(current,previous) == []


def test_changes_new_increase_reduction_exit_and_split():
    previous, current = snapshots([filing('2026-03-31'),filing('2026-06-30',shares='20',accession='b')])
    assert compare(current,previous)[0]['kind'] == 'increase'
    assert compare(current,previous,[{'cusip':'123456789','date':'2026-05-01','ratio':2,'url':'https://example.com/split'}]) == []
    current['holdings'][next(iter(current['holdings']))]['shares'] = 5
    assert compare(current,previous)[0]['kind'] == 'reduction'
    previous2, current2 = snapshots([filing('2026-03-31'),filing('2026-06-30',accession='b',cusip='987654321')])
    changes = compare(current2,previous2)
    assert {r['kind'] for r in changes} == {'new','exit'}
    assert next(r for r in changes if r['kind']=='exit')['reported_value'] is None


def test_amendments_replace_or_extend_period_not_new_quarter():
    original = filing('2026-06-30',accession='a')
    replacement = filing('2026-06-30',shares='15',accession='b',amendment='RESTATEMENT')
    addition = filing('2026-06-30',accession='c',amendment='NEW HOLDINGS',cusip='987654321')
    report = snapshots([original,replacement,addition])
    assert len(report) == 1 and len(report[0]['holdings']) == 2
    assert report[0]['holdings'][next(iter(original['holdings']))]['shares'] == 15
    assert sum(r['weight_pct'] for r in report[0]['holdings'].values()) == pytest.approx(100)
    assert report[0]['revised']
    assert snapshots([addition])[0]['incomplete']
    collision=deepcopy(addition);collision['holdings']=original['holdings']
    assert snapshots([original,collision])[0]['incomplete']


def test_nonconsecutive_reports_do_not_invent_quarterly_change():
    reports=snapshots([filing('2025-09-30'),filing('2026-06-30',shares='20',accession='b')])
    assert compare(reports[1],reports[0]) == []


def test_ledger_baseline_dedup_amendment_and_delivery(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db')
    previous,current=snapshots([filing('2026-03-31'),filing('2026-06-30',shares='20',accession='b')])
    changes=compare(current,previous)
    ledger.record_period('fund',previous,[],'2026-05-15')
    ledger.record_period('fund',current,changes,'2026-08-15')
    ledger.record_period('fund',current,changes,'2026-08-15')
    assert len(ledger.events()) == 1 and ledger.events()[0]['status']=='pending'
    calls=[]
    ledger.deliver(lambda e: calls.append(e['id']) or True,'2026-08-15')
    ledger.deliver(lambda e: calls.append(e['id']) or True,'2026-08-15')
    assert len(calls)==1 and ledger.events()[0]['status']=='sent'
    ledger.record_period('initial',current,changes,'2026-08-15',historical=True)
    assert next(e for e in ledger.events() if e['source']=='initial')['status']=='suppressed'
    ledger.record_period('amended',previous,[],'2026-05-15')
    ledger.record_period('amended',current,changes,'2026-08-15')
    ledger.record_period('amended',{**current,'revised':True},changes,'2026-08-16')
    assert next(e for e in ledger.events() if e['source']=='amended')['status']=='superseded'
    ledger.close()


def test_ambiguous_delivery_is_never_automatically_retried(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db')
    previous,current=snapshots([filing('2026-03-31'),filing('2026-06-30',shares='20',accession='b')])
    ledger.record_period('fund',previous,[],'2026-05-15')
    ledger.record_period('fund',current,compare(current,previous),'2026-08-15')
    def fail(event): raise TimeoutError()
    ledger.deliver(fail,'2026-08-15')
    ledger.deliver(lambda e: pytest.fail('must not retry ambiguous send'),'2026-08-16')
    assert ledger.events()[0]['status']=='delivery_unknown'
    ledger.close()


def test_feed_explicit_title_association_no_ownership_or_body_inference():
    feed='''<rss><channel><item><title>Uber: An Analysis</title><link>https://example.com/uber</link><pubDate>Mon, 28 Sep 2026 01:00:00 GMT</pubDate></item><item><title>A portfolio change</title><description>Uber Tesla</description><link>https://example.com/change</link><pubDate>Mon, 28 Sep 2026 01:00:00 GMT</pubDate></item></channel></rss>'''
    result=parse_feed(feed,{'id':'mbi','feed':'https://example.com/feed'},{'UBER':['Uber']},datetime(2026,9,28,12,tzinfo=timezone.utc))
    assert result[0]['tickers']==['UBER'] and result[0]['ownership']=='unknown' and result[0]['thesis'] is None
    assert result[1]['tickers']==[]
    assert validate_manual({'items':[{'source_id':'mbi','evidence_type':'research','public_summary':False}]},{'mbi'})==[]


def test_sec_requires_actual_contact(monkeypatch):
    monkeypatch.delenv('SEC_CONTACT_EMAIL',raising=False)
    with pytest.raises(ValueError,match='real contact'): SecClient()
    monkeypatch.delenv('INVESTORS_ALERT_EMAIL',raising=False)
    monkeypatch.setenv('FAIRENTRY_ALERT_EMAIL','unrelated@example.com')
    assert not email_configuration()['recipient_configured']


def test_partial_source_failure_does_not_seed_baseline(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db')
    class Broken:
        def discover(self,*args):return 'Fund',[{'accessionNumber':'a'}]
        def download(self,*args):raise ValueError('Missing table')
    result=refresh_source({'id':'fund','cik':'1'}, {'poll_minutes':60,'history_periods':4},ledger,
                          datetime(2026,9,28,tzinfo=timezone.utc),Broken())
    assert result['holdings_status']=='unavailable'
    assert not ledger.get('periods:fund') and not ledger.filings('fund')
    ledger.close()


def test_first_successful_multi_period_import_suppresses_all_then_new_period_notifies(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db')
    reports=[filing('2026-03-31'),filing('2026-06-30',shares='20',accession='b')]
    class Client:
        def discover(self,*args):return 'Fund',[{'accessionNumber':f['accession']} for f in reports]
        def download(self,source,meta):return next(f for f in reports if f['accession']==meta['accessionNumber'])
    source={'id':'fund','cik':'1'}
    cfg={'poll_minutes':60,'history_periods':4}
    refresh_source(source,cfg,ledger,datetime(2026,8,15,tzinfo=timezone.utc),Client(),True)
    assert all(e['status']=='suppressed' for e in ledger.events())
    new=filing('2026-09-30',shares='30',accession='c')
    new['filed']='2026-11-14';new['published_at']='2026-11-14T16:00:00Z'
    reports.append(new)
    refresh_source(source,cfg,ledger,datetime(2026,11,15,tzinfo=timezone.utc),Client(),True)
    assert sum(e['status']=='pending' for e in ledger.events())==1
    refresh_source(source,cfg,ledger,datetime(2026,11,15,tzinfo=timezone.utc),Client(),True)
    assert sum(e['status']=='pending' for e in ledger.events())==1
    ledger.close()


def test_persisted_delivery_reservation_prevents_ci_replay_after_crash(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db')
    previous,current=snapshots([filing('2026-03-31'),filing('2026-06-30',shares='20',accession='b')])
    ledger.record_period('fund',previous,[],'2026-05-15')
    ledger.record_period('fund',current,compare(current,previous),'2026-08-15')
    ledger.reserve('run-1-attempt-1','2026-08-15')
    assert ledger.events()[0]['status']=='reserved'
    # The persisted pre-send guard is restored after a runner was interrupted.
    ledger.reserve('run-1-attempt-2','2026-08-15')
    ledger.dispatch_reserved('run-1-attempt-2',lambda e:pytest.fail('Do not replay uncertain delivery'),'2026-08-15')
    assert ledger.events()[0]['status']=='delivery_unknown'
    ledger.close()


def test_old_pending_disclosures_expire_without_sending(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    previous, current = snapshots([filing('2026-03-31'), filing('2026-06-30', shares='20', accession='b')])
    ledger.record_period('fund', previous, [], '2026-05-15')
    ledger.record_period('fund', current, compare(current, previous), '2026-08-15')
    ledger.deliver(lambda e: pytest.fail('Old backlog must not send'), '2026-09-01')
    assert ledger.events()[0]['status'] == 'expired'
    ledger.close()


def test_previous_report_revision_cancels_change_even_when_difference_disappears(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    reports = [filing('2026-03-31'), filing('2026-06-30', shares='20', accession='b')]
    class Client:
        def discover(self, *args): return 'Fund', [{'accessionNumber': f['accession']} for f in reports]
        def download(self, source, meta): return next(f for f in reports if f['accession'] == meta['accessionNumber'])
    # Seed only the older report, then receive the current report.
    ledger.record_period('fund', snapshots(reports)[0], [], '2026-05-15')
    source, cfg = {'id': 'fund', 'cik': '1'}, {'poll_minutes': 60, 'history_periods': 4}
    refresh_source(source, cfg, ledger, datetime(2026, 8, 15, tzinfo=timezone.utc), Client(), True)
    assert ledger.events()[0]['status'] == 'pending'
    amendment = filing('2026-03-31', shares='20', accession='c', amendment='RESTATEMENT')
    amendment['filed'] = '2026-08-16'
    amendment['published_at'] = '2026-08-16T16:00:00Z'
    reports.append(amendment)
    refresh_source(source, cfg, ledger, datetime(2026, 8, 17, tzinfo=timezone.utc), Client(), True)
    assert ledger.events()[0]['status'] == 'superseded'
    ledger.close()
