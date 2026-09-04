from plain_engine.archive_identity import find_matching_entry


def test_story_id_beats_changed_headline_and_source_url():
    archive = [{
        "slug": "2026-09-01-original-headline",
        "headline": "Original headline",
        "source_url": "https://example.com/old",
        "story_id": "story_000123",
    }]
    found = find_matching_entry(
        "Completely rewritten update headline",
        archive,
        "https://different.example/new",
        "story_000123",
    )
    assert found["slug"] == "2026-09-01-original-headline"
