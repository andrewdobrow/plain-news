"""Terminal Plain publication safety: protect the actual reader-visible copy.

Do not depend on an earlier writer-quality result: canonical reuse, repair and
category promotion can mutate a headline/body after quality checks have run.
"""
from __future__ import annotations

import re
from .article_quality import publication_quality

_MODEL_REFUSAL = re.compile(
    r"(?:\bi (?:appreciate you sharing|don't have access to (?:the |actual )?"
    r"(?:content|source|article)|cannot (?:responsibly )?rewrite|can't (?:responsibly )?rewrite)"
    r"|\bcould you (?:please )?(?:share|provide) (?:the )?(?:source|full text|article|material)"
    r"|\b(?:as an ai|as a language model)\b"
    r"|\b(?:you've only provided the headlines|you have only provided headlines)"
    r"|\bi would need you to provide (?:the )?(?:full text|source|article))",
    re.I,
)
# These are explicit geographic mentions; aliases permit a city-specific headline
# to be grounded by the country if the reporting uses that designation instead.
_PLACES = {
    "krakow": {"krakow", "poland"}, "poland": {"poland", "krakow"},
    "israel": {"israel"}, "gaza": {"gaza"},
    "louisiana": {"louisiana"}, "wisconsin": {"wisconsin"},
    "moscow": {"moscow", "russia"}, "russia": {"russia", "moscow"},
    "ukraine": {"ukraine"}, "paraguay": {"paraguay"},
    "iran": {"iran"}, "china": {"china"},
    "texas": {"texas"}, "florida": {"florida"},
    "japan": {"japan"}, "india": {"india"},
    "taiwan": {"taiwan"}, "korea": {"korea"},
    "germany": {"germany"}, "france": {"france"},
    "brazil": {"brazil"}, "canada": {"canada"},
    "mexico": {"mexico"}, "london": {"london", "england", "britain", "uk"},
    "manchester": {"manchester", "england", "britain", "uk"},
    "britain": {"britain", "uk", "british"},
    "afghanistan": {"afghanistan"}, "syria": {"syria"},
}
_INTERNATIONAL = {
    "krakow", "poland", "israel", "gaza", "moscow", "russia",
    "ukraine", "paraguay", "iran", "china", "japan", "india",
    "taiwan", "korea", "germany", "france", "brazil", "canada",
    "mexico", "london", "manchester", "britain", "afghanistan", "syria",
}
_US_ACTION = re.compile(
    r"\b(?:u\.s\.|united states|american|pentagon|white house|"
    r"state department|us military|us president|washington dc|"
    r"u\.s\. citizens?|u\.s\. troops?)\b", re.I
)
_POLITICS_EVENT = re.compile(
    r"\b(?:senate race|presidential (?:race|campaign)|"
    r"runs? for (?:senate|governor|congress|president)|"
    r"enters? (?:the )?(?:senate|presidential) race|"
    r"attorney general .*?election|campaigns? for (?:senate|office)|"
    r"wins? (?:the )?(?:senate|gubernatorial) primary)\b", re.I
)
_SPORTS_EVENT = re.compile(
    r"\b(?:grand prix|formula 1|formula one|ncaa|"
    r"nba finals|nfl|mlb|nhl|premier league|"
    r"college football|football game|soccer match|tennis match|"
    r"boxing match|world cup qualifier)\b", re.I
)
_TECH_EVENTS = re.compile(
    r"\b(?:technology|tech|software|computer|chip|semiconductor|"
    r"cyber|ai |artificial intelligence|data center|phone|"
    r"science|nasa|space|satellite|laboratory|research|"
    r"gaming|app|electronics|robotics)\b", re.I
)


def assistant_response_reasons(body):
    """Catch refusals and task-dialogue contamination even if word count is long."""
    value = str(body or "")
    return ["ai_refusal_or_task_dialogue_in_article"] if _MODEL_REFUSAL.search(value) else []


def category_fit_reasons(category_key, headline):
    """High-precision disqualifiers, not a brittle exhaustive keyword classifier."""
    key = str(category_key or "").casefold()
    title = str(headline or "")
    lower = title.casefold()
    errors = []
    foreign = [place for place in _INTERNATIONAL if re.search(r"\b" + re.escape(place) + r"\b", lower)]
    if key == "us" and foreign and not _US_ACTION.search(title):
        errors.append("international_event_misrouted_to_us")
    if key in {"tech", "business"} and _POLITICS_EVENT.search(title):
        errors.append("political_election_misrouted_to_" + key)
    if key != "sports" and _SPORTS_EVENT.search(title):
        errors.append("sports_story_misrouted_to_" + key)
    return errors


def headline_body_reasons(headline, body):
    """Geo claims must be grounded in the article body, not contradicted by it."""
    title = str(headline or "").casefold()
    text = str(body or "").casefold()[:1800]
    named = [
        (place, aliases) for place, aliases in _PLACES.items()
        if re.search(r"\b" + re.escape(place) + r"\b", title)
    ]
    if named and not any(re.search(r"\b" + re.escape(alias) + r"\b", text)
                         for _, aliases in named for alias in aliases):
        return ["headline_geography_missing_from_article"]
    return []


def surface_errors(item, category_key, *, hero=True):
    """Run the full terminal contract on the exact content about to be published."""
    errors = []
    if not isinstance(item, dict):
        return ["non_dict_article"]
    ok, reasons = publication_quality(item, hero=hero)
    if not ok:
        errors.extend(reasons)
    errors += assistant_response_reasons(item.get("body"))
    errors += category_fit_reasons(category_key, item.get("headline"))
    errors += headline_body_reasons(item.get("headline"), item.get("body"))
    return sorted(set(errors))


def canonical_reuse_is_corroborated(item, existing):
    """Prevent an unrelated story_id collision from replacing verified live copy.

    A permanent ID alone cannot transform an unrelated current story into a
    months-old canonical; require independent URL or headline continuity.
    """
    if not isinstance(item, dict) or not isinstance(existing, dict):
        return False
    incoming_url = str(item.get("link") or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")
    old_url = str(existing.get("source_url") or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if incoming_url and old_url and incoming_url == old_url:
        return True
    old_title = str(existing.get("headline") or "")
    new_title = str(item.get("headline") or "")
    if not old_title or not new_title:
        return False
    def tokens(value):
        return {
            x for x in re.findall(r"[a-z0-9]+", value.casefold())
            if len(x) >= 4 and x not in {
                "this", "that", "from", "with", "over", "after", "before",
                "says", "said", "news", "report", "update", "today",
                "enters", "could", "would",
            }
        }
    a, b = tokens(old_title), tokens(new_title)
    shared = len(a & b)
    return shared >= 3 and shared / max(1, min(len(a), len(b))) >= .5


def verify_category_heroes(categories, top_cat):
    """No duplicate category leads or invalid body may cross the publication barrier."""
    failures = []
    seen = {}
    entries = list(categories or ())
    if top_cat is not None and top_cat.get("_standalone_front_page_hero"):
        entries = [top_cat] + entries
    for category in entries:
        key = str(category.get("category_key") or "")
        hero = category.get("hero") or {}
        errors = surface_errors(hero, key, hero=True)
        title = str(hero.get("headline") or "")
        if errors:
            failures.append({"category": key, "headline": title, "reasons": errors})
        # Unique identity enforced across sections; standalone Top News is
        # deliberately a different entry from its underlying section hero.
        ident = str(hero.get("story_id") or "") or "title:" + re.sub(r"\W+", " ", title.casefold()).strip()
        if ident in seen:
            failures.append({
                "category": key, "headline": title,
                "reasons": ["duplicate_category_hero_identity:" + seen[ident]],
            })
        else:
            seen[ident] = key
    return failures
