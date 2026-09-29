"""Refresh the Investors dashboard; --alerts processes the authorized outbox."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fairentry.investors.pipeline import write_investors
from fairentry.investors.pipeline import ROOT, email_configuration, sender
from fairentry.investors.ledger import Ledger
from datetime import datetime, timezone

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--alerts', action='store_true')
    ap.add_argument('--force', action='store_true', help='Bypass the hourly source polling cache')
    ap.add_argument('--offline', action='store_true', help='Export cached disclosures without fetching')
    ap.add_argument('--optional', action='store_true', help='Include Keith Gill; ownership remains unknown without evidence')
    ap.add_argument('--enrich-limit', type=int, default=20)
    delivery = ap.add_mutually_exclusive_group()
    delivery.add_argument('--reserve-alerts', metavar='RUN_TOKEN', help='Reserve outbox before CI persists its delivery guard')
    delivery.add_argument('--dispatch-alerts', metavar='RUN_TOKEN', help='Send only a previously persisted reservation')
    args = ap.parse_args()
    if args.reserve_alerts or args.dispatch_alerts:
        ledger = Ledger(ROOT / 'data/investors.db')
        config = email_configuration()
        now = datetime.now(timezone.utc).isoformat()
        try:
            if config['enabled'] and config['configured']:
                if args.reserve_alerts:
                    ledger.reserve(args.reserve_alerts, now)
                else:
                    ledger.dispatch_reserved(args.dispatch_alerts, sender, now)
        finally:
            ledger.close()
        print(write_investors(refresh=False,enrich_limit=0))
        sys.exit(0)
    print(write_investors(force=args.force, send_alerts=args.alerts, refresh=not args.offline,
                          optional=args.optional, enrich_limit=max(0, args.enrich_limit)))
