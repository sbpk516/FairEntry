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


def test_sma_panel_explains_fundamental_score_abbreviations_and_threshold():
    assert "Q</b> = Business Quality" in INDEX
    assert "FS</b> = Financial Strength &amp; Survival" in INDEX
    assert "G</b> = Growth &amp; Operating Momentum" in INDEX
    assert "Each is scored out of 100 and must be <b>70 or higher</b>" in INDEX
    assert "Fundamentals (0–100)" in INDEX


def test_sma_alerts_do_not_render_as_one_inline_sentence():
    assert "candidates.map(function(a){var z=" not in INDEX
    assert ".wma-alerts span{display:inline-block" not in INDEX


def test_sma_summary_is_visible_only_inside_the_sma_view():
    assert "renderWmaAlerts(META,list);" in INDEX
    assert "if(!WMA_ONLY||MODE==='emerging')" in INDEX
    assert "candidates=candidates.filter(function(a){return visibleTickers[a.ticker];});" in INDEX
    assert "renderWmaAlerts(m);" not in INDEX


def test_opening_sma_view_clears_filters_that_can_hide_all_candidates():
    handler = INDEX.split("$('#wmafilter').addEventListener('click'", 1)[1].split(
        "$('#highfilter').addEventListener", 1
    )[0]
    assert "MODE='all'" in handler
    assert "HIGH_ONLY=false" in handler
    assert "$('#vfilter [data-v=\"all\"]').classList.add('on')" in handler
    assert "$('#search').value=''" in handler
    assert "$('#sector').value='all'" in handler


def test_sma_view_has_individual_zone_filter():
    assert 'id="smazone" style="display:none"' in INDEX
    assert '<option value="all">All SMA zones</option>' in INDEX
    assert '<option value="sma_9month">9-month SMA</option>' in INDEX
    assert '<option value="sma_20month">20-month SMA</option>' in INDEX
    assert '<option value="sma_200week">200-week SMA</option>' in INDEX
    assert "z.id===SMA_ZONE" in INDEX
    assert "SMA_ZONE=this.value;renderBoard();" in INDEX
