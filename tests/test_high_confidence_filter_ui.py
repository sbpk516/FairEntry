from pathlib import Path


INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


def test_not_validated_high_confidence_filter_is_enabled_and_uses_current_contract():
    html = INDEX.read_text(encoding="utf-8")

    button = html.split('id="highfilter"', 1)[1].split("</button>", 1)[0]
    assert " disabled" not in button
    assert "High-confidence research · not proven" in button
    assert "function highConfidenceResearchMatch(s,c)" in html
    assert "tier.passes_financial_strength_rule===true" in html
    assert "tier.id==='financial_strength_qualified'" in html
    assert "if(HIGH_ONLY&&!highConfidenceResearchMatch(o.s,o.c))return false" in html


def test_not_validated_filter_remains_explicitly_information_only():
    html = INDEX.read_text(encoding="utf-8")

    button = html.split('id="highfilter"', 1)[1].split("</button>", 1)[0]
    assert "Research filter only" in button
    assert "does not change any score or recommendation" in button
    assert "has not been proven to improve results" in button
