"""Regression tests for the LIVE, national homepage hero publishing authority."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import pytest

from plain_engine.article_quality import publication_quality
from plain_engine.front_page_hero import (
    canonical_timestamp, evaluate_front_page_candidates, parse_publication_timestamp,
)


def story(headline, reference, *, hours=2, urgency=6, category="politics"):
    # Valid source-backed long-form copy; headline claim appears in the first
    # paragraph and is repeated in the exact-source evidence.
    detail = (
        "Federal officials presented the decision and identified the agencies responsible. "
        "The decision follows months of published findings and established procedures. "
        "The announcement describes its immediate effect on national institutions. "
        "The official record lists the next implementation steps and review process. "
    )
    body = headline + ". " + detail * 2 + "\n\n" + (
        "Officials provided supporting records and a timeline for further decisions. "
        "The published material explains the jurisdiction and confirmed consequences. "
        "Relevant agencies will follow those procedures under existing law. "
        "The federal record identifies which agencies must carry out the decision. "
    ) * 2
    item = {
        "headline": headline,
        "body": body, "article_text": body,
        "source_summary": body,
        "source_title": headline, "source_quality": "full",
        "published": "11:00 AM ET",  # display-only: never trusted for age
        "source_published_raw": format_datetime(reference - timedelta(hours=hours), usegmt=True),
        "urgency_score": urgency, "publication_quality_ok": True,
        "link": "https://example.com/" + category + "/" + str(abs(hash(headline))),
    }
    assert publication_quality(item, hero=True)[0], publication_quality(item, hero=True)
    return item


def section(category, item, cards=()):
    return {"category_key": category, "category_label": category.title(), "hero": item, "cards": list(cards)}


def titles(candidates):
    return [c["headline"] for c in candidates]


def test_routine_sports_and_entertainment_can_never_be_automatic_front_page_heroes():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    sport = story("BYU defeats Utah in nationally televised football game", now, urgency=10, category="sports")
    movie = story("Movie breaks box office record on opening weekend", now, urgency=10, category="entertainment")
    policy = story("Supreme Court rules on national federal policy", now, urgency=7)
    ranked, blocked = evaluate_front_page_candidates([
        section("sports",sport),section("entertainment",movie),section("politics",policy),
    ], now=now)
    assert titles(ranked) == [policy["headline"]]
    assert len([b for b in blocked if b["reason"] == "sports_entertainment_not_eligible"]) == 2


def test_concrete_federal_action_beats_industry_commentary_even_at_lower_urgency():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    tech = story("AI tools are enabling automated hacking at scale", now, urgency=10, category="tech")
    politics = story("Supreme Court rules on major national policy challenge", now, urgency=6)
    ranked, _ = evaluate_front_page_candidates(
        [section("tech",tech),section("politics",politics)], now=now)
    assert ranked[0]["headline"] == politics["headline"]
    assert ranked[0]["tier"] > ranked[1]["tier"]


def test_major_world_development_can_lead_without_us_military_keyword():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    world = story("Ukraine launches drone strikes on Moscow in major offensive", now, urgency=8, category="world")
    company = story("Tech company launches new software", now, urgency=9, category="tech")
    ranked, _ = evaluate_front_page_candidates(
        [section("world",world),section("tech",company)], now=now)
    assert ranked[0]["category_key"] == "world"
    assert ranked[0]["tier"] >= 4


def test_supporting_news_can_compete_without_being_selected_as_a_summary_hero():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    ordinary = story("Local officials describe a regional permitting proposal", now, category="us")
    strong = story("Supreme Court rules on national federal policy", now, urgency=9)
    card = dict(strong)
    card["body"] = strong["body"].split("\n\n")[0] + "\n\nA concise verified summary of this federal decision."
    card["source_index"] = 2
    ranked, _ = evaluate_front_page_candidates(
        [section("us",ordinary,cards=[card])], now=now)
    assert ranked[0]["role"] == "card"
    assert ranked[0]["headline"] == strong["headline"]


def test_source_date_not_formatted_display_time_is_authority():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    stale = story("Supreme Court rules on major national policy", now, hours=75, urgency=10)
    fresh = story("Federal Reserve announces national interest rate decision", now, hours=2)
    ranked, excluded = evaluate_front_page_candidates(
        [section("politics",stale),section("business",fresh)], now=now)
    assert ranked[0]["headline"] == fresh["headline"]
    assert "missing_or_stale_canonical_timestamp" in [x["reason"] for x in excluded]
    assert parse_publication_timestamp("11:00 AM ET") is None


def test_old_first_publication_cannot_be_revived_by_lastmod_or_new_rss_date():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    old = story("Supreme Court rules on federal policy", now, hours=1)
    old["story_id"] = "story_003"
    archive = [{
        "story_id": "story_003", "slug": "existing-canonical",
        "first_published": (now - timedelta(days=8)).isoformat(),
        "lastmod": now.date().isoformat(),
        "headline": old["headline"],
    }]
    ranked, rejected = evaluate_front_page_candidates([section("politics",old)], archive=archive, now=now)
    assert not ranked
    assert rejected[0]["timestamp_basis"] == "archive:first_published"
    archive[0]["meaningful_update_validated"] = True
    archive[0]["canonical_last_material_update_at"] = (now - timedelta(hours=2)).isoformat()
    ranked,_ = evaluate_front_page_candidates([section("politics",old)], archive=archive, now=now)
    assert ranked and ranked[0]["timestamp_basis"] == "archive:canonical_last_material_update_at"


def test_unknown_date_cannot_win_from_high_urgency_or_today_in_copy():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    item = story("President signs national federal legislation", now, urgency=10)
    item["source_published_raw"] = ""
    item["body"] += "\n\nThe law took effect today."
    candidates, excluded = evaluate_front_page_candidates([section("politics",item)], now=now)
    assert not candidates
    assert excluded[0]["reason"] == "missing_or_stale_canonical_timestamp"


def test_promoted_card_must_be_rewritten_and_validated_as_full_article(monkeypatch, tmp_path):
    import scripts.generate as generate
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    ordinary = story("Regional authority releases a routine report", now, category="us")
    best = story("President signs national federal legislation", now, urgency=9)
    card = dict(best)
    card.update({"source_index": 2, "body": "Brief summary only.\n\nSecond short paragraph."})
    area = section("us", ordinary, cards=[card])
    calls = []
    def fake_writer(client, *, category_key, category_label, source, assignment, hero, timeout_seconds):
        calls.append((hero,source["title"]))
        return {"headline": source["title"], "body": source["article_text"],
                "source_index": assignment["source_index"], "published": source["published"]}
    monkeypatch.setattr(generate,"run_assignment_writer",fake_writer)
    chosen = generate.select_front_page_hero([area], client=object(), now=now,
                                             audit_path=tmp_path/"hero-selection.json")
    assert chosen["_standalone_front_page_hero"]
    assert chosen["hero"]["headline"] == best["headline"]
    assert chosen["hero"]["publication_quality_ok"]
    assert calls == [(True, best["headline"])]
    assert area["hero"] is ordinary  # category hero never silently demoted
    assert area["cards"] == [card]
    audit = json.loads((tmp_path/"hero-selection.json").read_text())
    assert audit["selected"]["standalone_card_promoted"] is True


def test_failed_card_full_article_upgrade_falls_back_to_valid_section_hero(monkeypatch):
    import scripts.generate as generate
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    ordinary = story("National agency releases an infrastructure report", now, category="us")
    best = story("Supreme Court rules on national federal policy", now, urgency=10)
    card = dict(best);card.update({"source_index": 2, "body": "Brief only."})
    area = section("us", ordinary, cards=[card])
    monkeypatch.setattr(generate,"run_assignment_writer",lambda *a,**k: {"headline":best["headline"],"body":"Too short."})
    chosen = generate.select_front_page_hero([area],client=object(),now=now)
    assert chosen is area


def test_no_fresh_verified_article_fails_closed_instead_of_downgrading_to_sports():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    sports = story("BYU wins a major football game", now, hours=1, category="sports")
    politics = story("Supreme Court rules on major national policy", now, hours=100)
    candidates,_ = evaluate_front_page_candidates(
        [section("sports",sports),section("politics",politics)],now=now)
    assert not candidates
