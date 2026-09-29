"""Refresh the standalone market valuation panel without scoring or alerts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.adapters.market_valuation import write_market_valuation

if __name__ == "__main__":
    print(write_market_valuation())
