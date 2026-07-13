"""Load + validate all FairEntry configuration.

Four YAML files under config/ are the single sources of truth:
  catalog.yaml   every data field to pull (id, source, adapter, cadence, ...)
  sectors.yaml   sector universe + liquidity floor
  scoring.yaml   categories/items/weights/rules/bands/vetoes/gates/presets
  defaults.yaml  default user-editable settings

`load_config()` returns a validated Config object; a typo or missing key fails
loudly with a clear message (Requirement: config validation).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

# Metrics produced by later pipeline stages (not fetched by an adapter). Scoring
# rules may reference these; validation treats them as known.
COMPUTED_METRICS = {
    "intrinsic_gap_pct", "news_sentiment_score", "red_flags_score",
    "red_flags_critical", "upside_pct", "valuation_label", "sector_median",
}

VALID_CADENCES = {"twice_daily", "daily", "weekly", "filing_based", "event_based"}
VALID_ENTITIES = {"company", "security", "sector", "market"}
VALID_RULE_TYPES = {"higher_better", "lower_better", "sector_rel", "band",
                    "bool_good", "passthrough"}


class ConfigError(ValueError):
    """Raised when configuration is malformed. Message names the exact problem."""


def _read_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{name}: invalid YAML — {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{name}: top level must be a mapping")
    return data


@dataclass
class Config:
    catalog: dict
    sectors: dict
    scoring: dict
    defaults: dict

    # -- convenience accessors -------------------------------------------------
    @property
    def fields(self) -> list[dict]:
        return self.catalog["fields"]

    def field(self, field_id: str) -> dict:
        for f in self.fields:
            if f["id"] == field_id:
                return f
        raise KeyError(f"unknown catalog field: {field_id}")

    @property
    def enabled_sectors(self) -> list[dict]:
        return [s for s in self.sectors["sectors"] if s.get("enabled", True)]

    @property
    def categories(self) -> dict:
        return self.scoring["categories"]

    @property
    def verdict_bands(self) -> dict:
        return self.scoring["verdict_bands"]

    def fields_due_metrics(self) -> set[str]:
        return {f["id"] for f in self.fields}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def _validate_catalog(cat: dict) -> list[str]:
    errs: list[str] = []
    fields = cat.get("fields")
    if not isinstance(fields, list) or not fields:
        return ["catalog.yaml: 'fields' must be a non-empty list"]
    seen = set()
    required = {"id", "entity", "adapter", "cadence"}
    for i, f in enumerate(fields):
        where = f.get("id", f"#{i}")
        missing = required - set(f)
        if missing:
            errs.append(f"catalog field {where}: missing {sorted(missing)}")
            continue
        if f["id"] in seen:
            errs.append(f"catalog: duplicate field id '{f['id']}'")
        seen.add(f["id"])
        if f["entity"] not in VALID_ENTITIES:
            errs.append(f"catalog field {f['id']}: bad entity '{f['entity']}'")
        if f["cadence"] not in VALID_CADENCES:
            errs.append(f"catalog field {f['id']}: bad cadence '{f['cadence']}'")
    return errs


def _validate_scoring(sc: dict, known_metrics: set[str]) -> list[str]:
    errs: list[str] = []
    cats = sc.get("categories")
    if not isinstance(cats, dict) or not cats:
        return ["scoring.yaml: 'categories' must be a non-empty mapping"]
    total_w = 0
    for cid, cat in cats.items():
        w = cat.get("weight")
        if not isinstance(w, (int, float)):
            errs.append(f"scoring category {cid}: missing/invalid weight")
            continue
        total_w += w
        items = cat.get("items") or []
        if not items:
            errs.append(f"scoring category {cid}: no items")
        iw = 0
        for it in items:
            for req in ("id", "weight", "metric", "rule"):
                if req not in it:
                    errs.append(f"scoring {cid}.{it.get('id','?')}: missing '{req}'")
            iw += it.get("weight", 0)
            rule = it.get("rule", {})
            if rule.get("type") not in VALID_RULE_TYPES:
                errs.append(f"scoring {cid}.{it.get('id','?')}: bad rule type '{rule.get('type')}'")
            m = it.get("metric")
            if m and m not in known_metrics and m not in COMPUTED_METRICS:
                errs.append(f"scoring {cid}.{it.get('id','?')}: metric '{m}' not in catalog or computed set")
        if iw <= 0:
            errs.append(f"scoring category {cid}: item weights sum to {iw}")
    if abs(total_w - 100) > 0.5:
        errs.append(f"scoring: category weights sum to {total_w}, expected 100")
    vb = sc.get("verdict_bands", {})
    if "buy" not in vb or "watch" not in vb:
        errs.append("scoring: verdict_bands must define 'buy' and 'watch'")
    elif vb["buy"] <= vb["watch"]:
        errs.append("scoring: buy band must be > watch band")
    return errs


def _validate_sectors(se: dict) -> list[str]:
    errs: list[str] = []
    secs = se.get("sectors")
    if not isinstance(secs, list) or not secs:
        errs.append("sectors.yaml: 'sectors' must be a non-empty list")
    else:
        for s in secs:
            if "id" not in s or "finviz" not in s:
                errs.append(f"sectors: entry {s} missing id/finviz")
    return errs


def load_config(strict: bool = True) -> Config:
    catalog = _read_yaml("catalog.yaml")
    sectors = _read_yaml("sectors.yaml")
    scoring = _read_yaml("scoring.yaml")
    defaults = _read_yaml("defaults.yaml")

    known_metrics = {f["id"] for f in catalog.get("fields", []) if "id" in f}
    errs = (_validate_catalog(catalog)
            + _validate_sectors(sectors)
            + _validate_scoring(scoring, known_metrics))
    if errs and strict:
        raise ConfigError("configuration invalid:\n  - " + "\n  - ".join(errs))

    cfg = Config(catalog=catalog, sectors=sectors, scoring=scoring, defaults=defaults)
    cfg._errors = errs  # type: ignore[attr-defined]
    return cfg


if __name__ == "__main__":
    import sys
    try:
        c = load_config()
    except ConfigError as e:
        print("INVALID CONFIG\n" + str(e))
        sys.exit(1)
    print("config OK")
    print(f"  catalog fields : {len(c.fields)}")
    print(f"  sectors        : {[s['id'] for s in c.enabled_sectors]}")
    print(f"  categories     : {list(c.categories)}  (weights sum "
          f"{sum(x['weight'] for x in c.categories.values())})")
    print(f"  verdict bands  : {c.verdict_bands}")
