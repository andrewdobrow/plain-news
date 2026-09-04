from pathlib import Path

from plain_engine import write_homepage_ranking_recommendations
from scripts.editorial_runtime import apply_editorial_engine


def _item(headline: str, link: str, body: str, score: int) -> dict:
    return {
        "headline": headline,
        "source_title": headline,
        "source_summary": body,
        "source_published_raw": "Fri, 04 Sep 2026 16:00:00 GMT",
        "body": body,
        "teaser": body,
        "link": link,
        "urgency_score": score,
    }


def test_ranking_shadow_resolves_runtime_story_identity(tmp_path: Path):
    categories = [
        {
            "category_key": "politics",
            "category_label": "Politics",
            "hero": _item(
                "Senate passes federal voting bill",
                "https://www.reuters.com/world/us/senate-voting-bill-2026-09-04/",
                "The U.S. Senate passed federal voting legislation after a final vote in Washington.",
                10,
            ),
            "cards": [],
        },
        {
            "category_key": "tech",
            "category_label": "Tech & Science",
            "hero": _item(
                "NASA launches new climate satellite",
                "https://www.nasa.gov/missions/climate-satellite-launch/",
                "NASA launched a climate satellite designed to collect atmospheric measurements.",
                8,
            ),
            "cards": [],
        },
    ]
    apply_editorial_engine(categories, output_dir=tmp_path, mode="enforce")

    cards = []
    for category in categories:
        row = dict(category["hero"])
        row.update(
            cat_key=category["category_key"],
            category_key=category["category_key"],
            source_url=row["link"],
            published_raw=row["source_published_raw"],
        )
        cards.append(row)

    report = write_homepage_ranking_recommendations(
        cards,
        cards[0],
        registry_path=tmp_path / "story-registry.json",
        archive=[],
        output_path=tmp_path / "ranking-recommendations.json",
        review_path=tmp_path / "ranking-review.md",
    )

    assert len(report["items"]) == 2
    assert report["summary"]["registry_match_rate"] == 1.0
    assert report["summary"]["high_confidence_match_rate"] == 1.0
    assert (tmp_path / "ranking-recommendations.json").exists()
    assert (tmp_path / "ranking-review.md").exists()
