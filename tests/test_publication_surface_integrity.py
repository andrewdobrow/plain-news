"""Regression coverage for the exact corruption observed in Plain's live edition."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from plain_engine.article_quality import publication_quality
from plain_engine.publication_surface import (
    assistant_response_reasons, canonical_reuse_is_corroborated,
    category_fit_reasons, headline_body_reasons, surface_errors,
    verify_category_heroes,
)


REFUSAL = (
    "I appreciate you sharing this task, but I don't have access to the "
    "actual content of these source articles. You've only provided the "
    "headlines and source names, not the article text itself. "
    "To rewrite the card accurately using only confirmed facts from the "
    "sources, I would need you to provide the full text or detailed excerpts. "
    "Without seeing what they actually say, I can't responsibly rewrite it. "
    "Could you share the source material text so I can proceed with the rewrite?"
)


def article(headline, *, story_id="", text=None):
    content = text or (
        headline + ". Federal officials described the findings and cited "
        "published records supporting the reporting. The decision followed "
        "a series of confirmed public actions involving the relevant agency. "
        "The official statement identified specific consequences and described "
        "what the public should expect next. "
    ) * 3
    body = content + "\n\n" + (
        "The latest verified reporting describes the official response in detail. "
        "The responsible institutions identified a timetable for further review "
        "and explained how the developments affect their respective jurisdictions. "
    ) * 4
    return {
        "headline": headline, "body": body, "article_text": body,
        "source_title": headline, "source_summary": body,
        "link": "https://example.org/news/" + str(story_id or "one"),
        "story_id": story_id, "source_quality": "full",
        "publication_quality_ok": True, "editorial_eligible": True,
    }


def section(key, hero):
    return {"category_key": key, "category_label": key.title(),
            "hero": hero, "cards": []}


def test_ai_refusal_is_unpublishable_even_when_paragraphs_and_word_count_look_valid():
    body = REFUSAL + " " + ("Officials described the incident as developing. " * 40)
    item = article("Texas Attorney General Ken Paxton enters Senate race")
    item["body"] = body + "\n\n" + ("Additional text unrelated to news coverage. " * 12)
    ok, reasons = publication_quality(item, hero=True)
    assert not ok
    assert "ai_refusal_or_task_dialogue_in_article" in reasons
    assert assistant_response_reasons(item["body"])


def test_ai_refusal_cannot_pass_short_card_quality_contract():
    item = article("Federal agency outlines nationwide safety updates")
    item["body"] = REFUSAL + "\n\nDetails were not provided in the source."
    ok, reasons = publication_quality(item, hero=False)
    assert not ok
    assert "ai_refusal_or_task_dialogue_in_article" in reasons


def test_israel_headline_louisiana_body_fails_geographic_coherence():
    item = article("Gunman kills one, wounds five in shooting in central Israel")
    item["body"] = ("A shooting during a Louisiana parade wounded several "
                    "people Saturday according to the local sheriff. " * 12
                    + "\n\nPolice issued an update on the Louisiana investigation. " * 12)
    assert "headline_geography_missing_from_article" in surface_errors(item,"world")


def test_krakow_poland_story_is_not_a_us_section_lead():
    item = article("Six killed in shooting in Krakow, Poland; British citizen among dead")
    errors = surface_errors(item, "us")
    assert "international_event_misrouted_to_us" in errors


def test_political_senate_race_cannot_lead_tech_or_business():
    item = article("Texas Attorney General Ken Paxton enters Senate race")
    assert "political_election_misrouted_to_tech" in surface_errors(item,"tech")
    assert "political_election_misrouted_to_business" in surface_errors(item,"business")


def test_misfiled_formula_one_coverage_is_not_general_us_news():
    item = article("Hamilton and Verstappen to start from back of grid at Sao Paulo Grand Prix")
    assert "sports_story_misrouted_to_us" in surface_errors(item,"us")


def test_us_federal_action_abroad_can_be_relevant_to_us_section():
    item = article("U.S. military strikes Iranian military site following attack")
    assert "international_event_misrouted_to_us" not in category_fit_reasons("us",item["headline"])


def test_repeated_persistent_story_id_across_sections_is_rejected():
    a=article("Federal Reserve announces nationwide rate decision",story_id="story_001")
    b=article("Federal agency details nationwide public health rule",story_id="story_001")
    failures=verify_category_heroes([section("business",a),section("us",b)],None)
    assert any("duplicate_category_hero_identity" in str(x["reasons"]) for x in failures)


def test_archive_story_id_collision_cannot_replace_a_different_live_article():
    live=article("AI tools upend the cybersecurity sector",story_id="story_000112")
    old={"story_id":"story_000112","headline":"Texas Attorney General Ken Paxton enters Senate race",
         "source_url":"https://example.org/elections/texas"}
    assert not canonical_reuse_is_corroborated(live,old)


def test_archive_reuse_may_keep_same_article_when_titles_match():
    live=article("Federal Reserve announces nationwide rate decision",story_id="story_002")
    old={"story_id":"story_002","headline":"Federal Reserve announces nationwide rate decision"}
    assert canonical_reuse_is_corroborated(live,old)


def test_archive_substitution_refusal_aborts_before_any_public_file_write(monkeypatch,tmp_path):
    import scripts.generate as g
    item=article("Texas Attorney General Ken Paxton enters Senate race",
                 story_id="story_000112")
    cat=section("us",item)
    old={"slug":"2026-06-06-paxton","headline":item["headline"],
         "story_id":item["story_id"],"date":"2026-06-06"}
    archive_file=tmp_path/"archive.json"
    archive_file.write_text(json.dumps([old]))
    monkeypatch.setattr(g,"OUTPUT_DIR",tmp_path)
    monkeypatch.setattr(g,"_archive_article_body",lambda row:REFUSAL)
    with pytest.raises(RuntimeError, match="Terminal publication integrity barrier"):
        g.write_archives([cat],cat,client=object(),cache=object(),
                         material_update_entries=[])
    assert json.loads(archive_file.read_text())==[old]
    assert not (tmp_path/"articles"/"2026-06-06-paxton.html").exists()


def test_false_registry_match_is_blocked_before_archive_changes(monkeypatch,tmp_path):
    import scripts.generate as g
    item=article("US Treasury sanctions foreign crypto exchange",
                 story_id="story_000112")
    cat=section("business",item)
    old={"slug":"2026-06-06-paxton",
         "headline":"Texas Attorney General Ken Paxton enters Senate race",
         "story_id":item["story_id"],"date":"2026-06-06"}
    archive_file=tmp_path/"archive.json"
    archive_file.write_text(json.dumps([old]))
    monkeypatch.setattr(g,"OUTPUT_DIR",tmp_path)
    with pytest.raises(RuntimeError, match="Terminal archive identity mismatch"):
        g.write_archives([cat],cat,client=object(),cache=object(),
                         material_update_entries=[])
    assert json.loads(archive_file.read_text())==[old]


def test_explicit_cross_tagging_is_not_treated_as_section_eligibility():
    import scripts.generate as g
    rows=[
        {"title":"Krakow, Poland shooting","link":"https://example.org/world"},
        {"title":"Federal agency announces US policy","link":"https://example.org/us"},
    ]
    classification={(row["link"],g.re.sub(r"[^a-z0-9]+"," ",row["title"].casefold()).strip()):cats
                    for row,cats in zip(rows,[["world"],["us"]])}
    selected=g._apply_category_classification(rows,"us",classification,limit=18)
    assert len(selected)==1
    assert selected[0]["title"]=="Federal agency announces US policy"
