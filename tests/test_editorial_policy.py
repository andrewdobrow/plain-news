from plain_engine import EditorialEligibilityEngine, EditorialPolicy


def test_authoritative_and_wire_sources_have_high_trust():
    policy = EditorialPolicy()
    assert policy.source_profile("reuters.com").trust >= 95
    assert policy.source_profile("justice.gov").trust == 100


def test_property_listing_is_rejected():
    decision = EditorialEligibilityEngine().evaluate(
        {"title": "3 bedroom home for rent", "link": "https://www.zillow.com/example"},
        source="Zillow",
    )
    assert not decision.eligible
