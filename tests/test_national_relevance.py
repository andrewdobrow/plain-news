from plain_engine import classify_national_relevance


def test_federal_story_is_national():
    result = classify_national_relevance(
        text="The Federal Reserve changed its benchmark interest rate after its policy meeting."
    )
    assert result.scope == "us_national"
    assert result.score == 100


def test_multi_state_story_outranks_single_state_story():
    multi = classify_national_relevance(text="Officials in Texas and Louisiana issued emergency orders.")
    single = classify_national_relevance(text="Officials in Texas issued an emergency order.")
    assert multi.scope == "us_multistate"
    assert single.scope == "us_state_regional"
    assert multi.score > single.score


def test_major_world_story_remains_high_value_without_forced_us_angle():
    result = classify_national_relevance(text="Russia and Ukraine agreed to ceasefire talks after missile attacks.")
    assert result.scope == "global_major"
    assert result.score >= 85
