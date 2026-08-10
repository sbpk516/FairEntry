#!/usr/bin/env python3
"""Materialize point-in-time replay features in an imported SFA warehouse."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.sharadar import SharadarWarehouse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    args = ap.parse_args()
    with SharadarWarehouse(args.warehouse) as warehouse:
        warehouse._feature_tables()
        warehouse.con.execute("CHECKPOINT")
        print(
            json.dumps(
                {
                    "price_features": warehouse.con.execute(
                        "select count(*) from sfa_price_features"
                    ).fetchone()[0],
                    "art_features": warehouse.con.execute(
                        "select count(*) from sfa_art_features"
                    ).fetchone()[0],
                    "arq_features": warehouse.con.execute(
                        "select count(*) from sfa_arq_features"
                    ).fetchone()[0],
                }
            )
        )


if __name__ == "__main__":
    main()
