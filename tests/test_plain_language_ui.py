from pathlib import Path


INDEX = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(
    encoding="utf-8"
)
HIGH_CONVICTION = (
    Path(__file__).resolve().parents[1] / "fairentry" / "analytics" / "high_conviction.py"
).read_text(encoding="utf-8")


def test_high_conviction_details_use_plain_language():
    assert "combined rule still requires chronological validation" not in INDEX
    assert "Fresh usable price" not in INDEX
    assert "unseen-period" not in INDEX
    assert "Current price is recent enough" in HIGH_CONVICTION
    assert "not older than {price_freshness_limit_hours:g} hours" in HIGH_CONVICTION
    assert "Can this single check be tested on old dates?" in INDEX
    assert "Changes today\\'s recommendation?" in INDEX
    assert "This check is deliberately outside the official formula" in INDEX


def test_watch_cards_cannot_create_a_purchase():
    assert "c.verdict==='Buy'?'<button class=\"addbtn\"" in INDEX
    assert "purchase disabled" in INDEX
    assert "but do not buy until the official recommendation becomes Buy" in INDEX


def test_generic_business_description_is_not_presented_as_sourced_segment_data():
    assert "Typical revenue stream" in INDEX
    assert "simple sector-based explanation, not company segment data" in INDEX


def test_technical_hurdle_is_not_presented_as_a_forecast():
    assert "It is not a stock-specific forecast" in INDEX
    assert "tracking levels, not guaranteed returns" in INDEX


def test_research_rows_are_not_described_as_official_score_points():
    assert "Ignored by the official score" in INDEX
    assert "High-Conviction Research Candidate · 0 points" not in INDEX


def test_mixed_research_evidence_is_not_mislabeled_as_missing_data():
    assert "the others are mixed or lack enough data" in INDEX
    assert "return /unavailable|incomplete|missing" in INDEX
