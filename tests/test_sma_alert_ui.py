from pathlib import Path


INDEX = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(
    encoding="utf-8"
)


def test_sma_alerts_render_as_a_structured_responsive_table():
    assert 'class="wma-alert-table"' in INDEX
    assert "Nearest support zone" in INDEX
    assert "Fundamentals" in INDEX
    assert "wma-alert-table-wrap{overflow-x:auto" in INDEX
    assert "candidates.map(function(a)" in INDEX


def test_sma_alerts_do_not_render_as_one_inline_sentence():
    assert "candidates.map(function(a){var z=" not in INDEX
    assert ".wma-alerts span{display:inline-block" not in INDEX
