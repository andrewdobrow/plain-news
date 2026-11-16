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
    with pytest.raises(RuntimeError, match="Unsafe canonical substitution|Terminal publication integrity barrier"):
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


def test_false_story_identity_recovers_from_recent_verified_politics_canonical(monkeypatch,tmp_path):
    """Reproduce failed run: a cached Dana White lead matched a different old article."""
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone
    now=datetime.now(timezone.utc)
    live=article(
        "Trump offers UFC chief Dana White White House press secretary job",
        story_id="story_000064",
    )
    current=section("politics",live)
    fresh_headline="Congress passes nationwide election integrity legislation"
    fresh_article=article(fresh_headline,story_id="story_000352")
    recent={
        "slug":"recent-verified-politics",
        "headline":fresh_headline,
        "story_id":"story_000352",
        "category_key":"politics", "category_label":"Politics",
        "first_published":(now-timedelta(hours=2)).isoformat(),
        "date":now.date().isoformat(), "lastmod":now.date().isoformat(),
        "source_url":"https://example.org/verified/legislation",
        "body":fresh_article["body"], "teaser":fresh_article["body"][:170],
    }
    unrelated={
        "slug":"old-unrelated-us-canonical",
        "headline":"Federal judge blocks Postal Service restrictions on mail-in ballot delivery",
        "story_id":"story_000064",
        "category_key":"us", "category_label":"U.S.",
        "date":"2026-07-02","lastmod":"2026-07-02",
    }
    saved=[unrelated,recent]
    (tmp_path/"archive.json").write_text(json.dumps(saved))
    monkeypatch.setattr(g,"OUTPUT_DIR",tmp_path)
    monkeypatch.setattr(g,"render_archive_page",lambda *a,**kw:"<html>safe archive</html>")
    monkeypatch.setattr(g,"update_sitemap",lambda *a,**kw:"<xml/>")
    monkeypatch.setattr(g,"update_news_sitemap",lambda *a,**kw:"<xml/>")
    monkeypatch.setattr(g,"_write_publication_integrity_report",lambda *a,**kw:None)
    output=g.write_archives([current],current,client=object(),cache=object(),
                            material_update_entries=[])
    assert current["hero"]["headline"]==fresh_headline
    assert current["hero"]["_archived_slug"]==recent["slug"]
    assert [row["slug"] for row in output]==[unrelated["slug"],recent["slug"]]
    assert not (tmp_path/"articles"/"old-unrelated-us-canonical.html").exists()
    report=json.loads((tmp_path/"archive-identity-recovery-report.json").read_text())
    assert len(report["recoveries"])==1
    assert report["recoveries"][0]["discarded_live_headline"].startswith("Trump offers UFC")
    assert report["recoveries"][0]["recovered_slug"]==recent["slug"]


def test_ordinary_section_can_recover_recent_verified_canonical(monkeypatch, tmp_path):
    """A poisoned Tech ID may recover a genuinely recent same-section article."""
    import scripts.generate as g

    suspect = article(
        "California sues OpenAI after AI agents allegedly escaped testing",
        story_id="story_000481",
    )
    suspect["link"] = "https://example.org/california-openai-suit"
    category = section("tech", suspect)
    unrelated = _fresh_canonical_article(
        "Independent AI safety researchers request external oversight",
        "tech", "old-unrelated-agent-case", "story_000481", hours=125,
    )
    safe = _fresh_canonical_article(
        "Technology researchers release new cybersecurity guidance",
        "tech", "verified-tech-archive", "story_000999", hours=30,
    )
    unrelated["source_url"] = "https://example.org/research/oversight"
    safe["source_url"] = "https://example.org/guidance"

    _setup_archive_render_stubs(monkeypatch, g, tmp_path)
    (tmp_path / "archive.json").write_text(json.dumps([unrelated, safe]))
    homepage = section("business", article(
        "Banks publish new national consumer guidance", story_id="story_business"
    ))
    written = g.write_archives([category, homepage], homepage, client=object(),
                               cache=object(), material_update_entries=[])
    assert category["hero"]["headline"] == safe["headline"]
    assert category["hero"]["_archived_slug"] == safe["slug"]
    assert category["hero"]["_archive_recovery"] is True
    assert any(row["slug"] == safe["slug"] for row in written)
    assert not (tmp_path / "articles" / "old-unrelated-agent-case.html").exists()


def test_national_lead_cannot_reuse_four_day_edition(monkeypatch, tmp_path):
    """The extended fallback window applies to sections, not Top News."""
    import scripts.generate as g
    suspect = article("New Tech announcement has a mistaken historic story ID",
                      story_id="story_000481")
    category = section("tech", suspect)
    unrelated = _fresh_canonical_article(
        "Older unrelated technology report", "tech",
        "old-unrelated-report", "story_000481", hours=120,
    )
    safe = _fresh_canonical_article(
        "Older technology researchers release new cybersecurity guidance",
        "tech", "old-safe-archive", "story_000999", hours=100,
    )
    _setup_archive_render_stubs(monkeypatch, g, tmp_path)
    (tmp_path / "archive.json").write_text(json.dumps([unrelated, safe]))
    with pytest.raises(RuntimeError, match="no safe recent same-section canonical recovery"):
        g.write_archives([category], category, client=object(), cache=object(),
                         material_update_entries=[])


def test_undated_and_week_old_rss_sources_cannot_reenter_fresh_feed(monkeypatch):
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    recent = format_datetime(datetime.now(timezone.utc) - timedelta(hours=2), usegmt=True)
    old = format_datetime(datetime.now(timezone.utc) - timedelta(days=9), usegmt=True)
    assert not g._is_source_stale({"published": recent}, 72)
    assert g._is_source_stale({"published": old}, 72)
    assert g._is_source_stale({"published": ""}, 72)
    assert g._is_source_stale({"published": "invalid source date"}, 72)


def test_stale_archive_with_fresh_lastmod_is_not_eligible_for_backfill(monkeypatch, tmp_path):
    """A touched page does not make a week-old event fresh again."""
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = _fresh_canonical_article(
        "Week-old policy briefing", "politics", "old-weekly", "story_old", hours=200,
    )
    old["lastmod"] = now.date().isoformat()
    old["source_published_raw"] = (now - timedelta(days=9)).isoformat()
    fresh = _fresh_canonical_article(
        "New congressional vote", "politics", "fresh-vote", "story_new", hours=6,
    )
    fresh["lastmod"] = now.date().isoformat()
    category = section("politics", article("Another current lead", story_id="hero"))
    category["cards"] = []
    monkeypatch.setattr(g, "OUTPUT_DIR", tmp_path)
    (tmp_path / "articles").mkdir()
    added = g._archive_depth_backfill(
        [category], [old, fresh], target_cards=5, max_age_days=7,
    )
    assert added == 1
    assert [x["headline"] for x in category["cards"]] == [fresh["headline"]]


def test_recency_guard_removes_old_source_cards_and_retains_current(monkeypatch):
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    now = datetime.now(timezone.utc)
    def card(headline, hours):
        return {
            "headline": headline, "source_published_raw":
            format_datetime(now - timedelta(hours=hours), usegmt=True),
        }
    current = card("Current story", 3)
    stale = card("Eight day old story", 8 * 24)
    missing = {"headline": "Undated story"}
    category = {"category_key": "us", "category_label": "U.S.",
                "cards": [current, stale, missing]}
    assert g._prune_stale_surface_cards([category], [], now=now) == 2
    assert category["cards"] == [current]


def test_stale_hero_is_not_accepted_even_when_archive_lastmod_is_today():
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    now = datetime.now(timezone.utc)
    old = _fresh_canonical_article(
        "Historic data breach", "tech", "old-breach", "story_123", hours=205,
    )
    old["lastmod"] = now.date().isoformat()
    hero = article("Historic data breach", story_id="story_123")
    hero["link"] = old["source_url"]
    hero["source_published_raw"] = format_datetime(now - timedelta(hours=205), usegmt=True)
    with pytest.raises(RuntimeError, match="Stale or undated section lead"):
        g._verify_surface_hero_freshness([section("tech", hero)], [old], now=now)


def test_misbound_live_tech_hero_prefers_recent_live_card(monkeypatch):
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    now = datetime.now(timezone.utc)
    collided = article("California sues AI vendor over agents", story_id="story_77")
    collided["link"] = "https://example.org/new-suit"
    old = _fresh_canonical_article(
        "Researchers investigate unrelated AI agents", "tech", "old", "story_77", hours=98,
    )
    old["source_url"] = "https://example.org/old-investigation"
    fresh = article("Software company launches new security patch", story_id="story_99")
    fresh["source_published_raw"] = format_datetime(now - timedelta(hours=2), usegmt=True)
    category = section("tech", collided)
    category["cards"] = [fresh]
    monkeypatch.setattr(g, "surface_errors", lambda item, *a, **kw: [])
    monkeypatch.setattr(g, "_expand_verified_homepage_card", lambda client, payload: dict(payload["item"]))
    repaired = g._repair_misbound_live_section_heroes([category], [old], client=object())
    assert repaired == 1
    assert category["hero"]["headline"] == fresh["headline"]
    assert category["cards"] == []


def test_unrelated_identity_match_cannot_use_old_or_refusal_archive_backup(monkeypatch,tmp_path):
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone
    now=datetime.now(timezone.utc)
    broken=article("Trump offers UFC chief Dana White White House press secretary job",
                   story_id="story_000064")
    current=section("politics",broken)
    recent={
        "slug":"recent-refusal-politics",
        "headline":"Congress announces national legislation",
        "story_id":"story_000352",
        "category_key":"politics","date":now.date().isoformat(),
        "lastmod":now.date().isoformat(),
        "first_published":(now-timedelta(hours=2)).isoformat(),
        "body":REFUSAL,
    }
    unrelated={
        "slug":"old-unrelated","headline":"Federal judge blocks Postal Service restrictions",
        "story_id":"story_000064","category_key":"us","date":"2026-07-02",
    }
    saved=[unrelated,recent]
    (tmp_path/"archive.json").write_text(json.dumps(saved))
    monkeypatch.setattr(g,"OUTPUT_DIR",tmp_path)
    with pytest.raises(RuntimeError,match="no safe recent same-section canonical recovery"):
        g.write_archives([current],current,client=object(),cache=object(),material_update_entries=[])
    assert json.loads((tmp_path/"archive.json").read_text())==saved


def test_recovery_never_uses_a_historically_old_article_with_new_lastmod(monkeypatch,tmp_path):
    import scripts.generate as g
    from datetime import datetime, timedelta, timezone
    now=datetime.now(timezone.utc)
    live=article("Trump offers UFC chief Dana White White House press secretary job",
                 story_id="story_000064")
    original=section("politics",live)
    old_body=article("Congress passes nationwide election integrity legislation")["body"]
    entries=[
        {"slug":"old-wrong","headline":"Federal judge blocks Postal Service restrictions",
         "story_id":"story_000064","category_key":"us","date":"2026-07-02"},
        {"slug":"old-rebranded","headline":"Congress passes nationwide election integrity legislation",
         "story_id":"story_000352","category_key":"politics","date":"2026-07-01",
         "first_published":(now-timedelta(days=14)).isoformat(),
         "lastmod":now.date().isoformat(),"body":old_body},
    ]
    (tmp_path/"archive.json").write_text(json.dumps(entries))
    monkeypatch.setattr(g,"OUTPUT_DIR",tmp_path)
    with pytest.raises(RuntimeError,match="no safe recent same-section canonical recovery"):
        g.write_archives([original],original,client=object(),cache=object(),material_update_entries=[])


def _setup_archive_render_stubs(monkeypatch, generate, tmp_path):
    monkeypatch.setattr(generate, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(generate, "render_archive_page", lambda *a, **k: "<html>archive</html>")
    monkeypatch.setattr(generate, "update_sitemap", lambda *a, **k: "<xml/>")
    monkeypatch.setattr(generate, "update_news_sitemap", lambda *a, **k: "<xml/>")
    monkeypatch.setattr(generate, "_write_publication_integrity_report", lambda *a, **k: None)


def _fresh_canonical_article(headline, category, slug, story_id, hours=2):
    from datetime import datetime, timedelta, timezone
    when = datetime.now(timezone.utc)
    source = article(headline, story_id=story_id)
    return {
        "slug": slug, "headline": headline, "category_key": category,
        "category_label": category.title(), "story_id": story_id,
        "first_published": (when - timedelta(hours=hours)).isoformat(),
        "date": when.date().isoformat(), "lastmod": when.date().isoformat(),
        "body": source["body"], "teaser": source["body"][:170],
        "source_url": "https://example.org/canonicals/" + slug,
    }


def test_stale_source_focus_after_canonical_replacement_recovers_other_verified_story(monkeypatch, tmp_path):
    """Exact November 8 entertainment failure: old Godzilla body / newer source focus."""
    import scripts.generate as g
    mismatch = article(
        "Godzilla Minus Zero Opens to Record $30 Million-Plus for Live-Action Foreign-Language Film",
        story_id="story_000275",
    )
    mismatch["source_title"] = "Celebrity profile discusses the filmmaker personal career and early films"
    mismatch["article_text"] = (
        "The filmmaker described early directing challenges, artistic technique, "
        "colleagues and career milestones unrelated to the current box office. " * 15
    )
    cat = section("entertainment", mismatch)
    bad_archive = _fresh_canonical_article(
        mismatch["headline"], "entertainment", "old-godzilla", "story_000275", hours=28
    )
    safe_archive = _fresh_canonical_article(
        "Film industry union announces new nationwide safety agreement",
        "entertainment", "safe-entertainment", "story_008888", hours=3
    )
    # Ensure the older canonical body matches its headline and the newer
    # source evidence does NOT. Previous code silently re-used this mix.
    assert "generated_copy_drifted_from_source_focus" in surface_errors(
        {**mismatch, "body": bad_archive["body"]}, "entertainment"
    )
    original_archive = [bad_archive, safe_archive]
    _setup_archive_render_stubs(monkeypatch,g,tmp_path)
    (tmp_path/"archive.json").write_text(json.dumps(original_archive))
    rows=g.write_archives([cat],cat,client=object(),cache=object(),
                          material_update_entries=[])
    assert len(rows)==2
    assert cat["hero"]["headline"]==safe_archive["headline"]
    assert cat["hero"]["_archived_slug"]==safe_archive["slug"]
    assert cat["hero"]["publication_quality_ok"] is True
    assert all(row["headline"] != "Celebrity profile discusses the filmmaker personal career and early films" for row in rows)
    report=json.loads((tmp_path/"archive-identity-recovery-report.json").read_text())
    assert report["recoveries"][0]["reason"]=="canonical_substitution_quality_failure"
    assert "generated_copy_drifted_from_source_focus" in report["recoveries"][0]["validation_errors"]


def test_unpublishable_canonical_substitution_without_safe_recovery_stays_blocked(monkeypatch, tmp_path):
    import scripts.generate as g
    bad = article("Godzilla Minus Zero Opens to Record $30 Million-Plus for Foreign-Language Film",
                  story_id="story_000275")
    bad["source_title"] = "Celebrity profile discusses the filmmaker personal career and early films"
    bad["article_text"] = "The filmmaker discussed early personal experiences and creative career milestones. " * 20
    cat = section("entertainment",bad)
    existing = _fresh_canonical_article(bad["headline"],"entertainment","godzilla-only","story_000275")
    _setup_archive_render_stubs(monkeypatch,g,tmp_path)
    (tmp_path/"archive.json").write_text(json.dumps([existing]))
    with pytest.raises(RuntimeError,match="Unsafe canonical substitution"):
        g.write_archives([cat],cat,client=object(),cache=object(),material_update_entries=[])
    assert json.loads((tmp_path/"archive.json").read_text())==[existing]


def test_terminal_semantic_hold_recovers_verified_same_section_archive(monkeypatch, tmp_path):
    import scripts.generate as g
    live = article("Broncos beat Panthers 26-21 for fifth straight win behind Nix touchdowns",
                   story_id="story_new")
    cat=section("sports", live)
    old = _fresh_canonical_article(
        "Lions extend winning streak with victory over Miami Dolphins",
        "sports", "recent-lions", "story_lions", hours=4
    )
    _setup_archive_render_stubs(monkeypatch,g,tmp_path)
    (tmp_path/"archive.json").write_text(json.dumps([old]))
    monkeypatch.setattr(
        g,"_semantic_publication_decision",
        lambda *a,**kw: {"action": g.ACTION_HOLD, "selected_candidate_slug":"",
                         "reason":"ambiguous duplicate; not independently verified"},
    )
    rows=g.write_archives([cat],cat,client=object(),cache=object(),material_update_entries=[])
    assert len(rows)==1
    assert cat["hero"]["headline"]==old["headline"]
    report=json.loads((tmp_path/"archive-identity-recovery-report.json").read_text())
    assert report["recoveries"][0]["reason"]=="semantic_hold_fallback_to_verified_canonical"


def test_terminal_semantic_hold_without_safe_archive_still_fails_closed(monkeypatch,tmp_path):
    import scripts.generate as g
    live=article("Broncos beat Panthers 26-21 for fifth straight win behind Nix touchdowns",
                 story_id="story_new")
    cat=section("sports",live)
    _setup_archive_render_stubs(monkeypatch,g,tmp_path)
    (tmp_path/"archive.json").write_text("[]")
    monkeypatch.setattr(g,"_semantic_publication_decision",
                        lambda *a,**k:{"action":g.ACTION_HOLD,"selected_candidate_slug":""})
    with pytest.raises(RuntimeError,match="no safe recent sports recovery"):
        g.write_archives([cat],cat,client=object(),cache=object(),material_update_entries=[])
    assert json.loads((tmp_path/"archive.json").read_text())==[]



def test_supreme_court_unrelated_cases_do_not_share_legacy_fuzzy_permalink():
    from plain_engine.archive_identity import find_matching_entry
    old = [{
        "slug": "old-gun-case",
        "headline": "Supreme Court declines to hear gun industry challenge to New York liability law",
    }]
    fresh = "Supreme Court declines to hear three conservative appeals on DEI, leaving lower-court rulings intact"
    assert find_matching_entry(fresh, old) is None
    assert find_matching_entry(old[0]["headline"], old) is old[0]


def test_exact_source_url_precedes_poisoned_registry_story_id():
    from plain_engine.archive_identity import find_matching_entry
    rows = [
        {"story_id": "story_000091", "slug": "old-gun-case",
         "headline": "Supreme Court declines to hear gun industry challenge to New York liability law",
         "source_url": "https://example.org/gun-industry-lawsuit"},
        {"story_id": "story_900000001", "slug": "new-dei-case",
         "headline": "Supreme Court declines to hear three conservative appeals on DEI",
         "source_url": "https://example.org/dei-appeals"},
    ]
    selected = find_matching_entry(
        "Supreme Court declines to hear three conservative appeals on DEI",
        rows, source_url="https://example.org/dei-appeals", story_id="story_000091",
    )
    assert selected is rows[1]


def test_unrelated_supreme_court_cases_can_publish_separately_only_after_independent_validation(monkeypatch,tmp_path):
    import scripts.generate as g
    from plain_engine.archive_identity import find_matching_entry
    old_title = "Supreme Court declines to hear gun industry challenge to New York liability law"
    new_title = "Supreme Court declines to hear three conservative appeals on DEI, leaving lower-court rulings intact"
    incoming = article(new_title, story_id="story_000091")
    incoming["link"] = "https://example.org/dei-appeals"
    cat = section("politics", incoming)
    prior = _fresh_canonical_article(
        old_title,"politics","old-gun-case","story_000091",hours=85
    )
    prior["source_url"] = "https://example.org/gun-industry-lawsuit"
    before = json.loads(json.dumps(prior))
    assert find_matching_entry(new_title,[prior],incoming["link"],incoming["story_id"]) is prior
    _setup_archive_render_stubs(monkeypatch,g,tmp_path)
    (tmp_path/"archive.json").write_text(json.dumps([prior]))
    monkeypatch.setattr(g,"adjudicate_candidates",lambda *a,**kw:{
        "status":"validated", "action":g.ACTION_NEW,
        "same_real_world_event":False, "confidence":0.98,
        "selected_candidate_slug":"",
    })
    written = g.write_archives([cat],cat,client=object(),cache=object(),
                               material_update_entries=[])
    assert len(written)==2
    assert written[0]==before
    assert written[1]["headline"]==new_title
    assert written[1]["story_id"]!=prior["story_id"]
    assert written[1]["source_url"]==incoming["link"]
    assert (tmp_path/"articles"/(written[1]["slug"]+".html")).exists()
    report=json.loads((tmp_path/"archive-identity-recovery-report.json").read_text())
    assert report["recoveries"][0]["reason"]=="separate_new_event_independently_semantic_verified"
    assert report["recoveries"][0]["semantic_confidence"]==0.98


def test_unrelated_supreme_court_cases_never_bypass_hold_or_insufficient_evidence(monkeypatch,tmp_path):
    import scripts.generate as g
    old_title="Supreme Court declines to hear gun industry challenge to New York liability law"
    new_title="Supreme Court declines to hear three conservative appeals on DEI, leaving lower-court rulings intact"
    incoming=article(new_title,story_id="story_000091")
    incoming["link"]="https://example.org/dei-appeals"
    cat=section("politics",incoming)
    prior=_fresh_canonical_article(old_title,"politics","old-gun-case","story_000091",hours=85)
    prior["source_url"]="https://example.org/gun-industry-lawsuit"
    _setup_archive_render_stubs(monkeypatch,g,tmp_path)
    (tmp_path/"archive.json").write_text(json.dumps([prior]))
    monkeypatch.setattr(g,"adjudicate_candidates",lambda *a,**kw:{
        "status":"validated", "action":g.ACTION_HOLD,
        "same_real_world_event":False, "confidence":0.4,
    })
    with pytest.raises(RuntimeError,match="no safe recent same-section canonical recovery"):
        g.write_archives([cat],cat,client=object(),cache=object(),
                         material_update_entries=[])
    assert json.loads((tmp_path/"archive.json").read_text())==[prior]
    assert not list((tmp_path/"articles").glob("*.html"))


def test_same_source_url_keeps_verified_archive_identity_not_poisoned_incoming_id(monkeypatch,tmp_path):
    import scripts.generate as g
    headline="Federal Reserve announces nationwide rate decision"
    canonical=_fresh_canonical_article(headline,"business","fed-rate","story_000011",hours=2)
    canonical["source_url"]="https://example.org/federal-reserve/decision"
    incoming=article(headline,story_id="story_000999")
    incoming["link"]=canonical["source_url"]
    cat=section("business",incoming)
    _setup_archive_render_stubs(monkeypatch,g,tmp_path)
    (tmp_path/"archive.json").write_text(json.dumps([canonical]))
    rows=g.write_archives([cat],cat,client=object(),cache=object(),
                          material_update_entries=[])
    assert len(rows)==1
    assert incoming["story_id"]=="story_000011"
    assert rows[0]["story_id"]=="story_000011"
