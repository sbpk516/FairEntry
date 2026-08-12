from fairentry.adapters.finnhub import _tag


def test_news_tags_source_topics_without_guessing_sentiment():
    categories = _tag(
        "Government awards contract after new subsidy policy",
        "The company will expand factory capacity.",
    )
    assert {"policy", "contract", "expansion"}.issubset(categories)


def test_policy_speculation_is_only_categorized_not_called_positive():
    categories = _tag("Analysts discuss possible government tariff changes")
    assert "policy" in categories
    assert "positive" not in categories
