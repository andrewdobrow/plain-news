from pathlib import Path

from scripts.editorial_runtime import apply_editorial_engine


def _item(headline, link, body, published="Fri, 04 Sep 2026 16:00:00 GMT"):
    return {
        "headline": headline,
        "source_title": headline,
        "source_summary": body,
        "source_published_raw": published,
        "body": body,
        "teaser": body,
        "link": link,
        "urgency_score": 8,
    }


def test_enforce_deduplicates_same_story_inside_category(tmp_path: Path):
    link = "https://www.reuters.com/world/us/fed-rate-cut-2026-09-04/"
    body = "The Federal Reserve cut its benchmark interest rate by a quarter point after its policy meeting."
    categories = [{
        "category_key": "us",
        "category_label": "U.S.",
        "hero": _item("Federal Reserve cuts benchmark rate", link, body),
        "cards": [
            _item("Fed lowers benchmark interest rate", link, body),
            _item(
                "NASA launches climate satellite",
                "https://www.nasa.gov/missions/climate-satellite-launch/",
                "NASA launched a climate satellite to collect atmospheric measurements for researchers.",
            ),
        ],
    }]

    audit = apply_editorial_engine(categories, output_dir=tmp_path, mode="enforce")
    assert audit["counts"]["same_category_duplicates"] == 1
    assert len(categories[0]["cards"]) == 1
    assert categories[0]["hero"]["story_id"]
    assert (tmp_path / "story-registry.json").exists()
    assert (tmp_path / "editorial-state.json").exists()
    assert (tmp_path / "editorial-audit.json").exists()
    assert (tmp_path / "editorial-observability.json").exists()


def test_same_story_keeps_persistent_id_across_runs(tmp_path: Path):
    link = "https://www.reuters.com/world/us/fed-rate-cut-2026-09-04/"
    body = "The Federal Reserve cut its benchmark interest rate by a quarter point after its policy meeting."
    first = [{"category_key": "business", "category_label": "Business", "hero": _item("Fed cuts rates", link, body), "cards": []}]
    apply_editorial_engine(first, output_dir=tmp_path, mode="enforce")
    story_id = first[0]["hero"]["story_id"]

    second = [{"category_key": "business", "category_label": "Business", "hero": _item("Federal Reserve lowers rates", link, body), "cards": []}]
    apply_editorial_engine(second, output_dir=tmp_path, mode="enforce")
    assert second[0]["hero"]["story_id"] == story_id
    assert second[0]["hero"]["editorial_action"] == "ignore"
