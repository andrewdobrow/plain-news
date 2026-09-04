from plain_engine import StoryImportanceEngine


def test_single_local_death_does_not_outrank_federal_policy():
    engine = StoryImportanceEngine()
    local = engine.score({
        "titles": ["One person killed in city crash"],
        "facts": ["One person died after a crash"],
        "audience_relevance": {"scope": "us_state_regional", "score": 82},
    })
    federal = engine.score({
        "titles": ["Federal Reserve cuts interest rate"],
        "facts": ["The Federal Reserve cut its benchmark interest rate"],
        "audience_relevance": {"scope": "us_national", "score": 100},
    })
    assert federal.score > local.score


def test_mass_casualty_event_can_be_high_importance():
    result = StoryImportanceEngine().score({
        "titles": ["Earthquake leaves 75 people dead"],
        "facts": ["75 people killed after an earthquake"],
        "audience_relevance": {"scope": "global_major", "score": 88},
    })
    assert result.score >= 70
