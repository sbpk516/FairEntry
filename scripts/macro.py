"""Refresh macro context without stock scoring or alerts."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fairentry.adapters.macro import write_macro

if __name__ == "__main__":
    print(write_macro())
