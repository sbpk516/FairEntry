"""Generate docs/methodology.md directly from the scoring configuration."""
from __future__ import annotations

from pathlib import Path

from .config import load_config

OUT = Path(__file__).resolve().parent.parent / "docs" / "methodology.md"


def generate() -> str:
    cfg = load_config()
    lines = [
        "# FairEntry - Scoring Methodology",
        "",
        "_Generated from `config/scoring.yaml`. Do not edit by hand._",
        "",
        f"**Verdict bands:** Buy >= {cfg.verdict_bands['buy']} · "
        f"Watch >= {cfg.verdict_bands['watch']} · else Avoid.",
        "",
        "Only factors marked **tested** may affect the verdict.",
        "",
        "## Categories and factors",
        "",
    ]
    for category in cfg.categories.values():
        lines.extend([
            f"### {category['label']} - configured weight {category['weight']}",
            "",
            "| Factor | Decision use | Weight | Metric | Expected | Rule |",
            "|---|---|--:|---|---|---|",
        ])
        for item in category["items"]:
            lines.append(
                f"| {item['label']} | {item.get('decision_status', 'tested')} | "
                f"{item['weight']} | `{item['metric']}` | "
                f"{item.get('expected', '')} | `{item['rule'].get('type')}` |"
            )
        lines.append("")

    lines.append("## Tested hard vetoes")
    for veto in cfg.scoring.get("vetoes", []):
        if veto.get("decision_status", "tested") == "tested":
            lines.append(f"- **{veto['id']}** - {veto['reason']} (`{veto['when']}`)")
    lines.extend(["", "## Information-only safety warnings"])
    for veto in cfg.scoring.get("vetoes", []):
        if veto.get("decision_status", "tested") != "tested":
            lines.append(f"- **{veto['id']}** - {veto['reason']} (`{veto['when']}`)")
    lines.extend(["", "## Tested soft gates"])
    for gate in cfg.scoring.get("soft_gates", []):
        if gate.get("decision_status", "tested") == "tested":
            lines.append(f"- **{gate['id']}** - {gate['reason']} (`{gate['when']}`)")
    lines.extend([
        "",
        "## AI and news review",
        "",
        "AI, news, policy, contract, and expansion evidence is information only. "
        "It has zero effect on Buy / Watch / Avoid until the same factor can be "
        "replayed historically and passes validation.",
    ])
    return "\n".join(lines) + "\n"


def write():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generate(), encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print("wrote", write())
