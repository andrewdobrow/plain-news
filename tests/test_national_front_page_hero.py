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


def test_cross_tagged_motor_racing_in_us_section_cannot_become_top_news():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    misfiled = story("Hamilton and Verstappen to start from back of grid at Sao Paulo Grand Prix",
                     now, urgency=10, category="us")
    policy = story("Supreme Court rules on national federal policy", now, urgency=5)
    ranked, blocked = evaluate_front_page_candidates(
        [section("us",misfiled),section("politics",policy)], now=now)
    assert ranked[0]["headline"] == policy["headline"]
    assert "misclassified_sports_entertainment_content" in [r["reason"] for r in blocked]


def test_foreign_leader_impeachment_is_major_world_news_not_routine_crime():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    world = story("Paraguay president impeached and arrested after bank collapse", now,
                  urgency=8, category="world")
    tech = story("AI analyst predicts new software trends", now, urgency=9, category="tech")
    ranked,_ = evaluate_front_page_candidates(
        [section("world",world),section("tech",tech)], now=now)
    assert ranked[0]["headline"] == world["headline"]
    assert ranked[0]["tier"] >= 4


def test_single_company_federal_approval_does_not_automatically_outrank_nationwide_policy():
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    niche = story("US approves Nvidia chip sales to Chinese firm", now,
                  urgency=10, category="tech")
    national = story("Supreme Court rules on national federal policy", now,
                     urgency=6, category="politics")
    ranked,_ = evaluate_front_page_candidates([
        section("tech",niche),section("politics",national),
    ],now=now)
    assert ranked[0]["headline"] == national["headline"]


def test_final_canonical_guard_replaces_old_bound_story_with_fresh_lead(tmp_path):
    import scripts.generate as generate
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    stale = story("Supreme Court rules on national federal policy", now, hours=1)
    stale["story_id"] = "story_old"
    top = section("politics",stale)
    fresh = story("Ukraine launches drone strikes on Moscow in major offensive", now, hours=1, category="world")
    alternate = section("world",fresh)
    canonical = [{
        "story_id":"story_old","first_published":(now-timedelta(days=8)).isoformat(),
        "lastmod":now.date().isoformat(), "slug":"story-old",
    }]
    report = tmp_path/"final-hero-selection.json"
    chosen = generate.enforce_final_front_page_hero(top,[top,alternate],canonical,now=now,audit_path=report)
    assert chosen is alternate
    payload = json.loads(report.read_text())
    assert payload["passed"] is True
    assert payload["action"] == "recovered_fresh_canonical_category_hero"
    assert payload["selected_headline"] == fresh["headline"]


def test_standalone_front_page_card_does_not_drop_original_section_hero_from_archive(monkeypatch,tmp_path):
    import scripts.generate as generate
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    original = story("Federal authority releases a routine agency report",now,category="us")
    promoted = story("Supreme Court rules on national federal policy",now,category="us")
    original["story_id"] = "story_original"
    promoted["story_id"] = "story_promoted"
    category = section("us",original)
    top = {"category_key":"us","category_label":"U.S.","hero":promoted,
           "cards":[],"_standalone_front_page_hero":True}
    monkeypatch.setattr(generate,"OUTPUT_DIR",tmp_path)
    monkeypatch.setattr(generate,"_semantic_publication_decision",
                        lambda *a, **kw: {"action":generate.ACTION_NEW})
    monkeypatch.setattr(generate,"render_article_page",lambda *a,**kw:"<article>Safe article</article>")
    monkeypatch.setattr(generate,"render_archive_page",lambda *a,**kw:"<html>Archive</html>")
    monkeypatch.setattr(generate,"update_sitemap",lambda *a,**kw:"<xml>Sitemap</xml>")
    monkeypatch.setattr(generate,"update_news_sitemap",lambda *a,**kw:"<xml>News sitemap</xml>")
    monkeypatch.setattr(generate,"_write_publication_integrity_report",lambda *a,**kw:None)
    written=generate.write_archives([category],top,client=object(),cache=object(),
                                    material_update_entries=[])
    assert len(written)==2
    assert {row["story_id"] for row in written}=={"story_original","story_promoted"}
    assert all(row["category_key"]=="us" for row in written)
    assert len(list((tmp_path/"articles").glob("*.html")))==2


def test_promoted_card_is_excluded_from_top_news_before_model_ranking(monkeypatch):
    import scripts.generate as generate
    now = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
    main = story("Regional officials release routine state report",now,category="us")
    main["story_id"] = "story_original"
    card = story("Supreme Court rules on national federal policy",now,category="us")
    card["story_id"] = "story_featured"
    cat = section("us",main,cards=[card])
    selected = {"category_key":"us","category_label":"U.S.","hero":dict(card),
                "cards":[],"_standalone_front_page_hero":True}
    model_inputs = []
    monkeypatch.setattr(generate,"load_archive",lambda *a: [])
    def fake_rank(cards, dedupe_against=None):
        model_inputs.append([c["headline"] for c in cards])
        return cards
    monkeypatch.setattr(generate,"global_rank",fake_rank)
    html = generate.render_index([cat],top_cat=selected)
    assert model_inputs
    assert card["headline"] not in model_inputs[0]
    assert main["headline"] in model_inputs[0]
    assert card["headline"] in html  # still available as the top feature


def test_eastern_display_time_observes_november_standard_time(monkeypatch):
    import scripts.generate as generate
    fixed = datetime(2026, 11, 7, 18, tzinfo=timezone.utc)
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(generate,"datetime",FrozenDatetime)
    assert generate.now_et()=="1:00 PM ET"
    published=format_datetime(fixed-timedelta(hours=2),usegmt=True)
    assert generate.format_age(published)=="11:00 AM ET"


def test_fresh_world_development_beats_21_hour_old_targeted_treasury_action():
    """Regression: Nov 8 2026 news audit chose yesterday's MEXC headline."""
    now = datetime(2026, 11, 8, 23, 9, tzinfo=timezone.utc)
    mexc = story(
        "Treasury moves to cut off crypto exchange MEXC from U.S. financial "
        "system over alleged $16 billion in illicit transactions",
        now, hours=21, urgency=3, category="business",
    )
    strikes = story(
        "Houthi strikes hit Aramco Tower in Riyadh, target airport and oil facilities",
        now, hours=1.5, urgency=8, category="world",
    )
    ranked, rejected = evaluate_front_page_candidates([
        section("business", mexc),
        section("world", strikes),
    ], now=now)
    assert not rejected
    assert ranked[0]["headline"] == strikes["headline"]
    assert ranked[0]["tier"] == 4
    assert ranked[1]["tier"] == 3
    assert ranked[1]["tier_reason"] == "targeted_federal_entity_action"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_fresh_important_federal_action_can_still_beat_world_event():
    now = datetime(2026, 11, 8, 23, 9, tzinfo=timezone.utc)
    federal = story("Supreme Court rules on nationwide federal voting law", now, hours=1, urgency=9)
    world = story("Ukraine launches drone strikes on Moscow in major offensive",
                  now, hours=1, urgency=8, category="world")
    ranked, _ = evaluate_front_page_candidates(
        [section("politics", federal), section("world", world)], now=now
    )
    assert ranked[0]["headline"] == federal["headline"]
    assert ranked[0]["tier"] > ranked[1]["tier"]


def test_recency_can_overcome_single_significance_tier_gap():
    now = datetime(2026, 11, 8, 23, 9, tzinfo=timezone.utc)
    yesterday = story("Federal Reserve announces national interest rate decision",
                      now, hours=22, urgency=3, category="business")
    new_world = story("Ukraine launches drone strikes on Moscow in major offensive",
                      now, hours=1, urgency=8, category="world")
    ranked, _ = evaluate_front_page_candidates(
        [section("business", yesterday), section("world", new_world)], now=now
    )
    assert ranked[0]["headline"] == new_world["headline"]
    assert ranked[1]["tier"] == ranked[0]["tier"] + 1


def test_old_major_story_remains_eligible_if_only_newer_alternative_is_minor():
    now = datetime(2026, 11, 8, 23, 9, tzinfo=timezone.utc)
    federal = story("Supreme Court rules on nationwide federal policy",
                    now, hours=20, urgency=9)
    routine = story("Local officials describe regional infrastructure repairs",
                    now, hours=1, urgency=5, category="us")
    ranked, _ = evaluate_front_page_candidates(
        [section("politics", federal), section("us", routine)], now=now
    )
    assert ranked[0]["headline"] == federal["headline"]


def test_score_audit_includes_explainable_freshness_penalty():
    now = datetime(2026, 11, 8, 23, 9, tzinfo=timezone.utc)
    old = story("Federal Reserve announces nationwide interest rate decision",
                now, hours=22, urgency=6, category="business")
    new = story("Federal Reserve announces nationwide interest rate decision",
                now, hours=2, urgency=6, category="business")
    ranked, _ = evaluate_front_page_candidates(
        [section("business", old), section("business", new)], now=now
    )
    assert ranked[0]["age_hours"] == 2
    assert ranked[0]["score"] > ranked[1]["score"]
    assert ranked[0]["score_breakdown"]["freshness"] > ranked[1]["score_breakdown"]["freshness"]
    for row in ranked:
        assert row["score"] == round(sum(row["score_breakdown"].values()), 2)


def test_power_grid_frequency_research_cannot_beat_real_recent_infrastructure_strikes():
    """Regression: Nov 8 19:15 ET winner was misclassified as security emergency."""
    now = datetime(2026, 11, 9, 0, 14, tzinfo=timezone.utc)
    tech = story(
        "Researchers trace mysterious global power grid frequency drops "
        "to synchronized battery backup systems",
        now, hours=3.74, urgency=7, category="tech",
    )
    world = story(
        "Houthi strikes hit Aramco Tower in Riyadh, target airport and oil facilities",
        now, hours=1.09, urgency=3, category="world",
    )
    ranked, rejected = evaluate_front_page_candidates([
        section("tech", tech), section("world", world),
    ], now=now)
    assert not rejected
    assert ranked[0]["category_key"] == "world"
    assert ranked[1]["category_key"] == "tech"
    assert ranked[1]["tier_reason"] == "limited_national_reach"
    assert ranked[1]["tier"] == 1


def test_genuine_major_power_grid_outage_is_still_a_national_security_emergency():
    now = datetime(2026, 11, 9, 0, 14, tzinfo=timezone.utc)
    grid = story("Major power grid outage leaves multiple states without electricity",
                 now, hours=1, urgency=8, category="us")
    routine = story("Researchers trace power grid frequency drops to battery checks",
                    now, hours=1, urgency=9, category="tech")
    ranked, _ = evaluate_front_page_candidates(
        [section("us", grid), section("tech", routine)], now=now
    )
    assert ranked[0]["headline"] == grid["headline"]
    assert ranked[0]["tier_reason"] == "security_emergency"
    assert ranked[0]["tier"] == 4
