"""Generate docs/methodology.md from the scoring config — the methodology is
never out of sync with the code because it IS the config.
"""
from __future__ import annotations

from pathlib import Path

from .config import load_config

OUT = Path(__file__).resolve().parent.parent / "docs" / "methodology.md"


def generate() -> str:
    c = load_config()
    L = ["# FairEntry — Scoring Methodology", "",
         "_Generated from `config/scoring.yaml`. Do not edit by hand._", "",
         f"**Verdict bands:** Buy ≥ {c.verdict_bands['buy']} · "
         f"Watch ≥ {c.verdict_bands['watch']} · else Avoid.", "",
         "## Categories & items", ""]
    for cid, cat in c.categories.items():
        L.append(f"### {cat['label']} — weight {cat['weight']}")
        L.append("")
        L.append("| Item | Weight | Metric | Expected | Rule |")
        L.append("|---|--:|---|---|---|")
        for it in cat["items"]:
            L.append(f"| {it['label']} | {it['weight']} | `{it['metric']}` | "
                     f"{it.get('expected','')} | `{it['rule'].get('type')}` |")
        L.append("")
    L.append("## Hard vetoes (force Avoid)")
    for v in c.scoring.get("vetoes", []):
        L.append(f"- **{v['id']}** — {v['reason']} (`{v['when']}`)")
    L.append("")
    L.append("## Soft gates (cap Buy → Watch)")
    for g in c.scoring.get("soft_gates", []):
        L.append(f"- **{g['id']}** — {g['reason']} (`{g['when']}`)")
    L.append("")
    L.append("## Thesis modifier (recovery/growth score → ±base)")
    for b in c.scoring.get("thesis_modifier", []):
        L.append(f"- score ≥ {b['min']} → {b['mod']:+d}")
    return "\n".join(L) + "\n"


def write():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generate(), encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print("wrote", write())
