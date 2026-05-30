"""
Plain News - hourly generation pipeline
Fetches RSS headlines, ranks by urgency via Claude, writes articles, rebuilds site.
"""

import os
import json
import re
import feedparser
import anthropic
from datetime import datetime
from pathlib import Path

# -- CONFIG --

CATEGORIES = {
    "world": {
        "label": "World",
        "front_page_hero": False,
        "feeds": [
            "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        ],
    },
    "us": {
        "label": "U.S.",
        "feeds": [
            "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en",
            "https://feeds.npr.org/1001/rss.xml",
            "https://feeds.bbci.co.uk/news/rss.xml",
        ],
    },
    "business": {
        "label": "Business",
        "front_page_cap": 7,
        "feeds": [
            "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        ],
    },
    "tech": {
        "label": "Tech & Science",
        "feeds": [
            "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
            "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
            "https://www.theverge.com/rss/index.xml",
            "https://feeds.arstechnica.com/arstechnica/index",
            "https://techcrunch.com/feed/",
            "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        ],
    },
    "sports": {
        "label": "Sports",
        "front_page_cap": 7,
        "feeds": [
            "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en",
            "https://www.espn.com/espn/rss/news",
            "https://feeds.bbci.co.uk/sport/rss.xml",
        ],
    },
    "entertainment": {
        "label": "Entertainment",
        "front_page_cap": 7,
        "feeds": [
            "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en",
            "https://variety.com/feed/",
            "https://www.rollingstone.com/feed/",
            "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
        ],
    },
    "politics": {
        "label": "Politics",
        "feeds": [
            "https://thehill.com/feed/",
            "https://thehill.com/homenews/administration/feed/",
            "https://thehill.com/homenews/senate/feed/",
            "https://thehill.com/homenews/house/feed/",
            "https://feeds.npr.org/1014/rss.xml",
            "https://rss.politico.com/white-house.xml",
            "https://rss.politico.com/congress.xml",
            "https://feeds.washingtonpost.com/rss/politics",
            "https://www.axios.com/feeds/feed.rss",
        ],
    },
}

HEADLINES_PER_CATEGORY = 12

GUARDIAN_API_KEY = os.environ.get("GUARDIAN_API_KEY", "")

# Direct publisher RSS feeds used as content bank — richer summaries than Google News
CONTENT_BANK_FEEDS = [
    # BBC
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/us-and-canada/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "https://feeds.bbci.co.uk/sport/rss.xml",
    "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
    # NPR
    "https://feeds.npr.org/1001/rss.xml",
    "https://feeds.npr.org/1004/rss.xml",
    # The Guardian
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/us-news/rss",
    "https://www.theguardian.com/business/rss",
    "https://www.theguardian.com/technology/rss",
    "https://www.theguardian.com/science/rss",
    "https://www.theguardian.com/sport/rss",
    "https://www.theguardian.com/culture/rss",
    # ESPN
    "https://www.espn.com/espn/rss/news",
    # TechCrunch / Ars / Verge
    "https://techcrunch.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.theverge.com/rss/index.xml",
]

# Feeds that reliably include images in RSS — used for image matching
IMAGE_BANK_FEEDS = [
    # BBC (all sections)
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/us-and-canada/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "https://feeds.bbci.co.uk/news/health/rss.xml",
    "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "https://feeds.bbci.co.uk/sport/rss.xml",
    "https://feeds.bbci.co.uk/sport/american-football/rss.xml",
    "https://feeds.bbci.co.uk/sport/baseball/rss.xml",
    "https://feeds.bbci.co.uk/sport/basketball/rss.xml",
    "https://feeds.bbci.co.uk/sport/formula1/rss.xml",
    # The Guardian
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/us-news/rss",
    "https://www.theguardian.com/business/rss",
    "https://www.theguardian.com/technology/rss",
    "https://www.theguardian.com/science/rss",
    "https://www.theguardian.com/sport/rss",
    "https://www.theguardian.com/politics/rss",
    "https://www.theguardian.com/culture/rss",
    # NPR
    "https://feeds.npr.org/1001/rss.xml",
    "https://feeds.npr.org/1004/rss.xml",
    "https://feeds.npr.org/1006/rss.xml",
    "https://feeds.npr.org/1014/rss.xml",
    # Sports
    "https://www.espn.com/espn/rss/news",
    "https://www.cbssports.com/rss/headlines",
    "https://www.cbssports.com/nba/rss/headlines",
    "https://www.cbssports.com/nfl/rss/headlines",
    "https://www.cbssports.com/mlb/rss/headlines",
    # Tech
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    # Entertainment
    "https://variety.com/feed/",
    "https://www.rollingstone.com/feed/",
    "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
    # Yahoo News (broad aggregator with images)
    "https://news.yahoo.com/rss",
    "https://news.yahoo.com/rss/politics",
    "https://news.yahoo.com/rss/world",
    "https://news.yahoo.com/rss/science",
    "https://news.yahoo.com/rss/health",
]
CARDS_PER_CATEGORY     = 8
OUTPUT_DIR             = Path(__file__).parent.parent

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# -- RSS FETCHING --

def extract_image(entry):
    """Try every known location for an image in an RSS entry."""
    def valid(u):
        if not u or len(u) < 15: return False
        return not any(x in u.lower() for x in ["1x1", "pixel", "spacer", "tracking", "data:"])

    # 1. media:thumbnail (BBC, many feeds)
    for t in (getattr(entry, "media_thumbnail", None) or []):
        if isinstance(t, dict) and valid(t.get("url", "")):
            return t["url"]

    # 2. media:content
    for m in (getattr(entry, "media_content", None) or []):
        if not isinstance(m, dict): continue
        u = m.get("url", "")
        if valid(u) and ("image" in m.get("type", "") or any(u.lower().endswith(e) for e in (".jpg",".jpeg",".png",".webp"))):
            return u

    # 3. enclosures
    for enc in (getattr(entry, "enclosures", None) or []):
        if isinstance(enc, dict) and "image" in enc.get("type", ""):
            u = enc.get("href", enc.get("url", ""))
            if valid(u): return u

    # 4. Parse <img> from description HTML (catches Google News thumbnails)
    html = ""
    for field in ["description", "summary"]:
        val = entry.get(field, "") or getattr(entry, field, "")
        if isinstance(val, list) and val:
            html = val[0].get("value", "") if isinstance(val[0], dict) else str(val[0])
        elif isinstance(val, str):
            html = val
        if html: break

    for match in re.finditer(r'<img[^>]+src=["\']([^"\']{20,})["\']', html):
        u = match.group(1)
        if valid(u): return u

    return ""


def upscale_image_url(url):
    """Upscale BBC CDN images by replacing size param with 1024."""
    if not url or "ichef.bbci.co.uk" not in url:
        return url
    return re.sub(r"/\d{2,3}/", "/1024/", url, count=1)


FEED_PUBLISHER_MAP = {
    "bbci.co.uk":        "BBC News",
    "theguardian.com":   "The Guardian",
    "espn.com":          "ESPN",
    "cbssports.com":     "CBS Sports",
    "npr.org":           "NPR",
    "techcrunch.com":    "TechCrunch",
    "arstechnica.com":   "Ars Technica",
    "theverge.com":      "The Verge",
    "variety.com":       "Variety",
    "rollingstone.com":  "Rolling Stone",
    "yahoo.com":         "Yahoo News",
    "reuters.com":       "Reuters",
    "apnews.com":        "AP News",
    "nytimes.com":       "The New York Times",
    "washingtonpost.com":"The Washington Post",
    "thehill.com":       "The Hill",
    "thehill.com":       "The Hill",
    "statnews.com":      "STAT News",
    "forbes.com":        "Forbes",
    "bloomberg.com":     "Bloomberg",
    "wsj.com":           "The Wall Street Journal",
    "cnn.com":           "CNN",
    "foxnews.com":       "Fox News",
    "nbcnews.com":       "NBC News",
    "abcnews.go.com":    "ABC News",
    "cbsnews.com":       "CBS News",
    "usatoday.com":      "USA Today",
    "time.com":          "Time",
    "newsweek.com":      "Newsweek",
    "theatlantic.com":   "The Atlantic",
    "axios.com":         "Axios",
    "buzzfeednews.com":  "BuzzFeed News",
    "huffpost.com":      "HuffPost",
    "vox.com":           "Vox",
    "slate.com":         "Slate",
    "wired.com":         "Wired",
    "zdnet.com":         "ZDNet",
    "engadget.com":      "Engadget",
    "9to5mac.com":       "9to5Mac",
    "macrumors.com":     "MacRumors",
    "nasa.gov":          "NASA",
    "scientificamerican.com": "Scientific American",
    "nature.com":        "Nature",
    "bbc.com":           "BBC News",
    "independent.co.uk": "The Independent",
    "telegraph.co.uk":   "The Telegraph",
    "ft.com":            "Financial Times",
    "economist.com":     "The Economist",
}


def get_image_credit(source_url):
    """Return a clean publisher name from a feed URL. Returns empty string if unknown."""
    if not source_url:
        return ""
    source_lower = source_url.lower()
    for domain, name in FEED_PUBLISHER_MAP.items():
        if domain in source_lower:
            return name
    return ""


def build_image_bank():
    """Fetch images from RSS feeds that reliably include them (BBC, ESPN, TechCrunch)."""
    bank = []
    for url in IMAGE_BANK_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:30]:
                title = entry.get("title", "").strip()
                img   = extract_image(entry)
                if title and img:
                    bank.append({"title": title, "image_url": img, "source": url})
        except Exception as e:
            print(f"  Image bank feed error ({url[:50]}): {e}")
    print(f"  Image bank built: {len(bank)} entries with images")
    return bank


def match_image(headline, image_bank, cat_key=""):
    """Fuzzy-match a headline against the image bank with geographic and category conflict detection."""
    stops = {"that","this","with","from","have","been","after","over","into","says","said","will","than","more","also","when","were","they","their","about"}
    geo_words = {"ukraine","ukrainian","russia","russian","china","chinese","israel","israeli","gaza","iran","iranian",
                 "france","french","germany","german","australia","australian","india","indian","pakistan","pakistani",
                 "korea","korean","japan","japanese","mexico","mexican","brazil","brazilian","cuba","cuban"}

    # Category-to-source mapping — prevent cross-category image mismatches
    cat_source_hints = {
        "tech":          ["techcrunch", "arstechnica", "theverge", "technology"],
        "sports":        ["espn", "cbssports", "sport"],
        "entertainment": ["variety", "entertainment"],
        "business":      ["business"],
        "science":       ["science", "nasa"],
    }
    # Sources that should NOT be used for certain categories
    cat_source_blocks = {
        "tech":          ["espn", "cbssports", "sport"],
        "business":      ["espn", "cbssports", "sport"],
        "entertainment": ["espn", "cbssports"],
        "science":       ["espn", "cbssports", "sport"],
        "world":         ["espn", "cbssports"],
        "politics":      ["espn", "cbssports"],
        "us":            ["espn", "cbssports"],
    }

    def tokens(text):
        return set(w.lower().strip(".,;:()") for w in text.split() if len(w) > 3 and w.lower() not in stops)

    hw = tokens(headline)
    hl_geo = hw & geo_words
    blocked_sources = cat_source_blocks.get(cat_key, [])
    best_score, best_img, best_credit = 0, "", ""

    for entry in image_bank:
        source = entry.get("source", "").lower()
        # Block sports images on non-sports categories
        if any(b in source for b in blocked_sources):
            continue
        entry_tokens = tokens(entry["title"])
        overlap = len(hw & entry_tokens)
        if overlap > best_score and overlap >= 3:
            entry_geo = entry_tokens & geo_words
            if hl_geo and entry_geo and not (hl_geo & entry_geo):
                continue
            best_score   = overlap
            best_img     = upscale_image_url(entry["image_url"])
            best_credit  = get_image_credit(entry.get("source", ""))
    return best_img, best_credit


def find_image(headline, entries):
    """Match headline back to RSS entry for image, link, and publish time."""
    h = headline.lower()[:50]
    for entry in entries:
        t = entry.get("title", "").lower()[:50]
        if h in t or t in h:
            return {
                "image_url": entry.get("image_url", ""),
                "link":      entry.get("link", ""),
                "published": entry.get("published", ""),
            }
    return {"image_url": "", "link": "", "published": ""}


def extract_publisher_url(entry):
    """Extract actual publisher URL from a Google News RSS entry.
    Google News embeds the publisher URL as an href in the description HTML.
    Falls back to entry link for non-Google feeds.
    """
    link = entry.get("link", "")
    if "news.google.com" not in link:
        return link  # Already a direct publisher URL
    desc = entry.get("summary", entry.get("description", ""))
    if isinstance(desc, list):
        desc = desc[0].get("value", "") if desc else ""
    matches = re.findall(r'href="(https?://(?!news\.google)[^"]+)"', desc)
    if matches:
        return matches[0]
    return link


def sanitize_text(text):
    """Remove characters that break JSON parsing."""
    if not text:
        return ""
    return text.replace("\\", " ").replace('"', "'").replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()


def clean_summary(text):
    """Strip navigation text, bylines, HTML tags, and noise from RSS summaries."""
    if not text:
        return ""
    import re as _re
    # Remove HTML tags
    text = _re.sub(r"<[^>]+>", " ", text)
    # Remove URLs
    text = _re.sub(r"https?://\S+", "", text)
    # Remove common RSS noise patterns
    noise_patterns = [
        r"(?i)read more.*$",
        r"(?i)click here.*$",
        r"(?i)continue reading.*$",
        r"(?i)\[\+\d+ chars\].*$",
        r"(?i)^by [A-Z][a-z]+ [A-Z][a-z]+",
        r"(?i)related articles?:.*$",
        r"(?i)also read:.*$",
        r"(?i)share this:.*$",
        r"(?i)follow us.*$",
        r"&amp;|&lt;|&gt;|&quot;|&#\d+;",
    ]
    for pattern in noise_patterns:
        text = _re.sub(pattern, "", text, flags=_re.MULTILINE)
    # Remove characters that break JSON parsing
    text = text.replace("\\", " ").replace('"', "'").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    # Collapse whitespace
    text = _re.sub(r"\s+", " ", text).strip()
    return text


def fetch_headlines(feeds, limit=HEADLINES_PER_CATEGORY):
    """Pull headlines from all feeds, deduplicate, then limit."""
    seen, entries = set(), []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries[:15]:
                title = sanitize_text(entry.get("title", "").strip())
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                entries.append({
                    "title":     title,
                    "summary":   clean_summary(entry.get("summary", entry.get("description", "")))[:800],
                    "link":      extract_publisher_url(entry),
                    "image_url": extract_image(entry),
                    "published": entry.get("published", ""),
                })
                count += 1
        except Exception as e:
            print(f"  Feed error ({url[:60]}): {e}")
    # Sort by published date (freshest first) then limit
    def pub_sort(h):
        try:
            from email.utils import parsedate_to_datetime
            from datetime import timezone
            return parsedate_to_datetime(h["published"]).astimezone(timezone.utc).timestamp()
        except Exception:
            return 0
    entries.sort(key=pub_sort, reverse=True)
    return entries[:limit]


# -- CLAUDE EDITORIAL ENGINE --

SYSTEM_PROMPT = """You are the editorial engine for Plain, a clean US-focused news site. Write factual, neutral, plain English articles. No jargon. No em dashes.

EDITORIAL PRIORITIES (weigh together):
1. CONSEQUENCE — how significantly does this affect people? Deaths, resignations, crises, economic decisions all score equally based on impact.
2. RECENCY — fresh breaking news ranks above follow-ups. Edited timestamps do not make old stories new.
3. SCOPE — how many people are meaningfully affected.

SCORING GUIDE:
- Government/national security changes, major deaths, active crises: 8-10
- Economic policy, natural disasters with casualties: 7-9
- Follow-ups on previous day's events: 4-6 (always below genuinely new stories)
- Sports/entertainment: 4-8 based on cultural significance
- Politics category: the story must be primarily ABOUT a US political actor, institution, or policy (Congress, White House, Supreme Court, US elections, US politicians). US-Iran negotiations belong here because the US government is the main actor. Turkish police raiding opposition offices do NOT — that is a World story. If the US government is not the primary subject, score it 1-2.
- U.S. category: political news scores above 7 only if it directly affects economy, public safety, constitutional rights, or national security.

ACCURACY — never violate:
- Write only details explicitly in the provided source material. Never speculate or infer.
- If a detail is unknown, omit it entirely. Never write about missing information in any form.
- Never fabricate quotes, statistics, names, or events.
- Use past tense for past events. Frame updates as updates, not new events.
- Never reference a specific day of the week (Monday, Tuesday, etc.) unless it appears explicitly in the source material. Do not infer the day from context or current date knowledge.

STYLE — never violate:
- Never editorialize. No loaded words: controversial, rocky, embattled, slammed, blasted, chaotic, failed.
- Never copy text verbatim from sources. Write in your own words; paraphrase everything except direct quotes from named individuals.
- No newsletter openers like "Good morning."
- Report what happened. Let readers draw their own conclusions."""


def strip_absence_language(text):
    """Remove sentences containing absence/uncertainty language from article text."""
    if not text:
        return text
    absence_patterns = [
        "no information was", "no details were", "no details have",
        "details were not", "details have not", "details are not",
        "has not been confirmed", "have not been confirmed",
        "was not disclosed", "were not disclosed",
        "it remains unclear", "it is unclear", "remains unknown",
        "officials have not", "has not responded", "did not respond",
        "not immediately available", "not yet available",
        "could not be reached", "could not be confirmed",
        "no official statement", "no statement has",
        "reporting is ongoing", "investigation is ongoing",
    ]
    sentences = text.replace("\n\n", "<<PARA>>").split(".")
    cleaned = []
    for s in sentences:
        s_lower = s.lower()
        if not any(p in s_lower for p in absence_patterns):
            cleaned.append(s)
    result = ".".join(cleaned)
    return result.replace("<<PARA>>", "\n\n").strip()


def strip_markdown(text, headline=""):
    """Remove markdown formatting and headline restatements from article text."""
    if not text:
        return text
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # Remove common Guardian/newsletter openers
    greetings = ["good morning.", "good afternoon.", "good evening.", "good morning,", "good afternoon,", "good evening,"]
    lower = text.lower()
    for g in greetings:
        if lower.startswith(g):
            text = text[len(g):].lstrip()
            break

    # Remove first paragraph if it looks like a headline restatement
    if headline:
        paragraphs = text.split("\n\n")
        if paragraphs:
            first = paragraphs[0].strip()
            if len(first.split()) < 20:
                hl_words = set(re.sub(r"[^a-z0-9 ]", " ", headline.lower()).split())
                p_words  = set(re.sub(r"[^a-z0-9 ]", " ", first.lower()).split())
                if len(hl_words & p_words) >= min(4, len(hl_words) // 2):
                    text = "\n\n".join(paragraphs[1:]).strip()
    return text


def generate_category_content(category_key, category_label, headlines):
    # Build headlines with raw published strings for Claude to copy back
    def sanitize(text):
        if not text:
            return ""
        import re as _re
        # Remove characters that break JSON
        text = text.replace("\\", " ").replace('"', "'").replace("\n", " ").replace("\r", " ").replace("\t", " ")
        # Remove non-printable characters
        text = "".join(c for c in text if c.isprintable())
        # Remove any remaining control sequences
        text = _re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
        # Collapse whitespace
        text = _re.sub(r"\s+", " ", text).strip()
        return text

    def hl_line(i, h):
        pub     = sanitize(h.get("published", ""))
        pub_str = f" [pub:{pub}]" if pub else ""
        title   = sanitize(h.get("title", ""))
        summary = sanitize(h.get("summary", ""))
        return f"{i+1}. {title}{pub_str}\n   {summary[:550]}"
    # Pre-filter headlines older than 48 hours before Claude sees them
    from datetime import timezone as _tz
    _now_utc = datetime.now(_tz.utc)
    def _is_stale(h):
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(h.get("published","")).astimezone(_tz.utc)
            age_hrs = (_now_utc - dt).total_seconds() / 3600
            return age_hrs > 48
        except Exception:
            return True
    if category_label == "Politics":
        print(f"  Politics pre-filter: {len(headlines)} headlines incoming")
        for h in headlines[:8]:
            stale = _is_stale(h)
            print(f"    [stale={stale}] [{h.get('published','NO DATE')}] {h.get('title','')[:55]}")
    fresh = [h for h in headlines if not _is_stale(h)]
    headlines = fresh if len(fresh) >= 1 else headlines

    headlines_text = "\n".join(hl_line(i, h) for i, h in enumerate(headlines))
    # Final safety pass — remove any remaining characters that break JSON
    headlines_text = headlines_text.replace("\\", " ")
    # Final nuclear sanitization — encode to ASCII and back to strip any remaining bad chars
    headlines_text = headlines_text.encode("ascii", "ignore").decode("ascii")

    prompt = f"""Top headlines for {category_label}:

{headlines_text}

Tasks:
1. Pick the single most important/urgent story.
2. Write an accurate headline reflecting the current state (frame updates as updates, not new events).
3. Write a 420-480 word factual article. Use only confirmed facts from the source. Write in your own words.
4. For the next {CARDS_PER_CATEGORY} most important stories write a teaser (one sentence), body (two short paragraphs ~120 words), and urgency_score (1-10). The cards MUST be different stories from the hero — never repeat or reframe the hero story as a card. Card bodies must only contain confirmed facts from the headline and summary. Never use phrases like "no information was disclosed", "details were not available", "it remains unclear", "has not been confirmed", "officials have not commented", or any similar absence language. If details are limited write fewer words and stop — do not pad.

Return ONLY valid JSON:
{{
  "hero": {{
    "headline": "accurate temporally-framed headline",
    "body": "full article text with paragraph breaks",
    "urgency_score": <1-10>,
    "published": "copy the [pub:...] string from the chosen headline exactly, including the date",
    "source_index": <the number of the chosen headline, e.g. 3>
  }},
  "cards": [
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}},
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}},
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}},
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}},
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}},
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}},
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}},
    {{"headline": "...", "teaser": "...", "body": "two paragraphs...", "urgency_score": <1-10>, "published": "copy timestamp", "source_index": <number>}}
  ]
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1800,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        }],
        messages=[{"role": "user", "content": prompt}],
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        from json_repair import repair_json
        data = json.loads(repair_json(raw))
    except Exception:
        try:
            data = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            import re as _re
            cleaned = raw.encode("ascii", "ignore").decode("ascii")
            try:
                data = json.loads(cleaned, strict=False)
            except json.JSONDecodeError:
                start = cleaned.index("{")
                end   = cleaned.rindex("}") + 1
                data  = json.loads(cleaned[start:end], strict=False)
    data["category_key"]   = category_key
    data["category_label"] = category_label

    # Use source_index to attach original RSS link and image directly — no fuzzy matching needed
    def attach_source(item, headlines):
        idx = item.get("source_index")
        if idx is not None:
            try:
                source = headlines[int(idx) - 1]
                item["link"]      = source.get("link", "")
                item["image_url"] = source.get("image_url", "")
            except (IndexError, ValueError, TypeError):
                item["link"]      = ""
                item["image_url"] = ""
        else:
            item["link"]      = ""
            item["image_url"] = ""

        # Format published
        raw_pub = item.get("published", "").replace("pub:", "").strip().strip("[]")
        item["published"] = format_age(raw_pub)
        return item

    data["hero"] = attach_source(data["hero"], headlines)
    data["hero"]["body"] = strip_markdown(data["hero"].get("body", ""), data["hero"].get("headline", ""))
    for card in data.get("cards", []):
        attach_source(card, headlines)
        card["body"] = strip_absence_language(strip_markdown(card.get("body", ""), card.get("headline", "")))

    # Age-based score decay for stale non-breaking stories
    def decay_score(item):
        score = item.get("urgency_score", 5)
        idx = item.get("source_index")
        if idx is None: return item
        try:
            pub_raw = headlines[int(idx) - 1].get("published", "")
            if not pub_raw: return item
            from email.utils import parsedate_to_datetime
            from datetime import timezone
            dt  = parsedate_to_datetime(pub_raw).astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            hrs = (now - dt).total_seconds() / 3600
            headline = item.get("headline", "").lower()
            fresh_words = ["confirms","confirmed","announces","announced","charges","charged",
                          "arrested","arrest","resigns","resigned","fired","breaks","exclusive",
                          "new details","emerges","emerged","discovered","uncovers","uncovered",
                          "identified","named","ruled","plot","conspiracy","indicted","sentenced",
                          "found guilty","linked","motive","cause of death"]
            is_fresh = any(w in headline for w in fresh_words)
            if not is_fresh:
                if hrs > 48: score = min(score, 4)
                elif hrs > 36: score = min(score, 5)
                elif hrs > 24: score = min(score, 7)
                elif hrs > 12: score = min(score, 8)
        except Exception:
            pass
        item["urgency_score"] = score
        return item

    decay_score(data["hero"])
    for card in data.get("cards", []): decay_score(card)

    # Age cap function — parses RSS timestamps properly
    def apply_age_cap(item):
        from email.utils import parsedate_to_datetime
        from datetime import timezone, timedelta
        import re as _re
        score = item.get("urgency_score", 5)
        # Try to get age from source headline timestamp via source_index
        idx = item.get("source_index")
        pub_raw = ""
        if idx is not None:
            try:
                pub_raw = headlines[int(idx) - 1].get("published", "")
            except Exception:
                pass
        if not pub_raw:
            pub_raw = item.get("published", "")
        if not pub_raw:
            return
        try:
            dt  = parsedate_to_datetime(pub_raw).astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            hrs = (now - dt).total_seconds() / 3600
        except Exception:
            return

        headline_lower = item.get("headline", "").lower()
        body_lower     = item.get("body", "").lower()[:400]
        one_time_events = ["resigns", "resigned", "steps down", "fired", "dies", "dead at",
                           "killed in", "found dead", "passed away", "obituary"]

        if hrs > 48:
            item["urgency_score"] = min(score, 4)
        elif hrs > 24:
            if any(w in headline_lower for w in one_time_events):
                item["urgency_score"] = min(score, 4)
            else:
                item["urgency_score"] = min(score, 6)
        elif hrs > 12:
            if any(w in headline_lower for w in one_time_events):
                item["urgency_score"] = min(score, 6)
            else:
                # Check for stale body signals
                _now   = datetime.now(timezone.utc)
                _dates = [(_now - timedelta(days=d)).strftime("%B %d").lower().replace(" 0", " ") for d in range(2, 14)]
                _months_gone = [(_now - timedelta(days=d*30)).strftime("%B").lower() for d in range(1, 6)]
                stale_body_signals = ["last week", "last month", "a week ago", "days ago",
                                      "on monday", "on tuesday", "on wednesday", "on thursday",
                                      "on friday", "on saturday", "on sunday"] + _dates + _months_gone
                if any(s in body_lower for s in stale_body_signals):
                    item["urgency_score"] = min(score, 6)

    apply_age_cap(data["hero"])
    if data["hero"].get("published", "") and not any(w in data["hero"]["published"].lower() for w in ["minute", "hour", "a few", ":"]):
        print(f"  Hard age cap applied: hero is from {data['hero']['published']}")
    for card in data.get("cards", []):
        apply_age_cap(card)

    return data


# -- HTML GENERATION --

def now_et():
    et_hour = (datetime.utcnow().hour - 4) % 24
    suffix  = "AM" if et_hour < 12 else "PM"
    display = et_hour % 12 or 12
    return f"{display}:00 {suffix} ET"


def make_paragraphs(text):
    if not text:
        return ""
    # Split on double newlines first, fall back to single newlines
    paragraphs = text.split("\n\n")
    if len(paragraphs) == 1:
        paragraphs = text.split("\n")
    return "".join(
        f"<p>{p.strip()}</p>"
        for p in paragraphs
        if p.strip() and len(p.strip()) > 30
    )


def build_content_bank():
    """Build a bank of rich publisher content from direct RSS feeds.
    These have far richer summaries than Google News and no redirect issues.
    """
    bank = []
    seen = set()
    for url in CONTENT_BANK_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:25]:
                title = sanitize_text(entry.get("title", ""))
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                summary = entry.get("summary", entry.get("description", ""))[:1200]
                if summary and len(summary) > 100:
                    bank.append({
                        "title":   title,
                        "summary": summary,
                        "source":  feed.feed.get("title", url),
                    })
        except Exception as e:
            print(f"  Content bank feed error ({url[:50]}): {e}")
    print(f"  Content bank built: {len(bank)} entries")
    return bank


def find_content(headline, content_bank, max_entries=5):
    """Fuzzy-match a headline against the content bank and return combined rich summaries."""
    stops = {"that","this","with","from","have","been","said","will","more",
             "also","when","were","they","their","about","says","just","after"}
    def tokens(text):
        return set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()) - stops
    hero_tokens = tokens(headline)
    matches = []
    for entry in content_bank:
        overlap = len(hero_tokens & tokens(entry["title"]))
        if overlap >= 2:
            matches.append((overlap, entry))
    matches.sort(key=lambda x: x[0], reverse=True)
    if not matches:
        return ""
    parts = []
    for _, entry in matches[:max_entries]:
        src     = entry["source"]
        title   = entry["title"]
        summary = entry["summary"]
        parts.append(f"[{src}] {title}\n{summary}")
    return "\n\n".join(parts)


def fetch_guardian_article(headline):
    """Search Guardian API for matching article and return full body text.
    Free API key returns complete article content.
    """
    if not GUARDIAN_API_KEY:
        return ""
    try:
        import requests as _req
        # Build search query from key headline words
        stops = {"that","this","with","from","have","been","said","will","more",
                 "also","when","were","they","their","about","says","just","after","as","a","the","in","of","for","to","and","or","on","at","an"}
        words = [w for w in re.sub(r"[^a-z0-9 ]", " ", headline.lower()).split()
                 if len(w) > 3 and w not in stops][:6]
        query = " ".join(words)
        params = {
            "q":            query,
            "api-key":      GUARDIAN_API_KEY,
            "show-fields":  "bodyText",
            "page-size":    3,
            "order-by":     "relevance",
        }
        resp = _req.get("https://content.guardianapis.com/search", params=params, timeout=8)
        results = resp.json().get("response", {}).get("results", [])
        for result in results:
            body = result.get("fields", {}).get("bodyText", "")
            if body and len(body.split()) > 150:
                words_list = body.split()
                truncated  = " ".join(words_list[:900])
                print(f"  Guardian: {len(words_list)} words fetched")
                return truncated
    except Exception as e:
        print(f"  Guardian fetch failed: {e}")
    return ""


def fetch_article_text(url, max_words=900):
    """Article fetch disabled — base articles from RSS summaries only."""
    return ""



def enhance_card(card, content_bank, headlines):
    """Enrich a card body using content bank and related RSS summaries. Uses Haiku."""
    headline = card.get("headline", "")
    if not headline:
        return card

    # Gather content bank matches
    bank_content = find_content(headline, content_bank, max_entries=2)

    # Gather related RSS summaries
    stops = {"that","this","with","from","have","been","said","will","more",
             "also","when","were","they","their","about","says","just","after"}
    hl_tokens = set(re.sub(r"[^a-z0-9 ]", " ", headline.lower()).split()) - stops
    related_parts = []
    for h in headlines:
        h_tokens = set(re.sub(r"[^a-z0-9 ]", " ", h.get("title","").lower()).split()) - stops
        if len(hl_tokens & h_tokens) >= 2:
            related_parts.append(h.get("title","") + ". " + h.get("summary","")[:200])
    related_text = " | ".join(related_parts[:3])

    source_parts = [p for p in [bank_content, related_text] if p]
    source_text  = "\n\n".join(source_parts)

    if not source_text or len(source_text.split()) < 50:
        return card

    # Relevance check
    stops2 = {"the","a","an","in","of","for","to","and","or","on","at","is","was","are","were","that","this","with"}
    hl_tok  = set(re.sub(r"[^a-z0-9 ]", " ", headline.lower()).split()) - stops2
    src_tok = set(re.sub(r"[^a-z0-9 ]", " ", source_text[:400].lower()).split()) - stops2
    if len(hl_tok & src_tok) < 2:
        return card

    try:
        body   = card.get("body", "")
        prompt = (
            f"You wrote this news card about: {headline}\n\n"
            f"Your original card text:\n\n{body}\n\n"
            f"Here is additional source material:\n\n{source_text}\n\n"
            "If the source is about a different story, return the original card text unchanged. "
            "Otherwise rewrite the card body in two short paragraphs (~120 words total) "
            "using only confirmed facts from the source. Write in your own words. "
            "Never use phrases like 'no information was disclosed', 'details were not available', "
            "'it remains unclear', 'has not been confirmed', or any similar absence language. "
            "If details are limited write fewer words and stop — do not pad."
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        enhanced = resp.content[0].text.strip()
        explanation_signals = ["i cannot rewrite", "source material", "does not match", "cannot proceed"]
        if enhanced and not any(s in enhanced.lower()[:150] for s in explanation_signals):
            card["body"] = strip_markdown(enhanced, headline)
    except Exception:
        pass
    return card


def enhance_hero_article(hero, full_text):
    """Rewrite the hero article using the full source text for accuracy and detail."""
    if not full_text or len(full_text.split()) < 150:
        return hero  # Not enough text to improve on
    body = hero.get("body", "")
    prompt = (
        f"You wrote this article about: {hero.get('headline', '')}\n\n"
        f"Your original article:\n\n{body}\n\n"
        f"Here is source material:\n\n{full_text}\n\n"
        "If the source material is clearly about a different story, location, or incident than the headline, "
        "return your original article exactly as written above with no changes. "
        "Otherwise, rewrite your article using confirmed facts from the source. "
        "Write in your own words — paraphrase everything except direct quotes from named individuals. "
        "Do not invent details not in the source. Do not comment on absent information. "
        "Do not copy newsletter openers like 'Good morning'. "
        "Keep it 420-480 words. Plain direct English. No em dashes."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        enhanced = resp.content[0].text.strip()
        # Detect if Claude returned an explanation instead of an article
        explanation_signals = ["i cannot rewrite", "source material", "does not match", "i must return", "cannot proceed"]
        if enhanced and not any(s in enhanced.lower()[:200] for s in explanation_signals):
            hero["body"] = strip_markdown(enhanced, hero.get("headline", ""))
            print(f"  Hero article enhanced with full source text")
        else:
            print(f"  Enhancement skipped: Claude returned explanation, keeping original")
    except Exception as e:
        print(f"  Enhancement failed ({e}), keeping original")
    return hero


def format_age(published_str):
    """Format publish time using stdlib only — no pytz needed."""
    if not published_str:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        from datetime import timezone, timedelta
        et      = timezone(timedelta(hours=-4))  # EDT approximation
        dt_utc  = parsedate_to_datetime(published_str).astimezone(timezone.utc)
        now_utc = datetime.now(timezone.utc)
        mins    = int((now_utc - dt_utc).total_seconds() / 60)
        dt_et   = dt_utc.astimezone(et)
        now_et  = now_utc.astimezone(et)
        hour    = dt_et.hour % 12 or 12
        ampm    = "AM" if dt_et.hour < 12 else "PM"
        time_str = f"{hour}:{dt_et.strftime('%M')} {ampm} ET"
        if mins < 60:
            return "A few minutes ago"
        if dt_et.date() == now_et.date():
            return time_str
        if mins < 2880:
            return f"Yesterday, {time_str}"
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        return f"{months[dt_et.month-1]} {dt_et.day}, {time_str}"
    except Exception:
        return ""


def select_front_page_hero(all_categories):
    """Use Claude to pick the most front-page-worthy hero across all categories.
    Considers systemic impact and US relevance — not just raw urgency or casualty count.
    Localized tragedies (regional accidents) lose to geopolitical events with broad reach,
    even when casualty counts are similar."""
    if not all_categories:
        return None

    def _is_eligible(cat):
        if not CATEGORIES.get(cat["category_key"], {}).get("front_page_hero", True):
            us_words = ["us strikes", "us military", "american forces", "u.s. strikes",
                        "u.s. military", "united states strikes", "trump orders", "pentagon"]
            return any(w in cat["hero"].get("headline", "").lower() for w in us_words)
        return True

    def _fp_score(cat):
        score = int(cat["hero"].get("urgency_score", 0) or 0)
        cap   = CATEGORIES.get(cat["category_key"], {}).get("front_page_cap", 10)
        return min(score, cap)

    eligible   = [c for c in all_categories if _is_eligible(c)]
    candidates = eligible if eligible else all_categories
    if len(candidates) == 1:
        return candidates[0]

    listing = "\n".join(
        f"{i+1}. [{c['category_label']}] {c['hero'].get('headline','')}"
        for i, c in enumerate(candidates)
    )
    prompt = (
        "You are selecting the SINGLE most front-page-worthy story for a US news app from these section heroes.\n\n"
        f"{listing}\n\n"
        "AUDIENCE: This app is for US readers. Skew toward stories that matter MOST to a US reader.\n"
        "But \"matters to US readers\" is about national/systemic relevance, NOT just whether it happened on US soil. "
        "A regional US tragedy is local to that region; a foreign event involving US allies or US interests can have "
        "broader national implications.\n"
        "\n"
        "Pick the story with the GREATEST systemic impact for the US audience — not just the highest casualty count.\n"
        "\n"
        "STRONG front-page heroes (in rough priority order):\n"
        "1. Major US national policy/political developments affecting millions (Supreme Court rulings, major legislation, executive actions with broad impact)\n"
        "2. US national security crises, attacks on US soil, US military action\n"
        "3. Major economic events affecting US consumers/markets (Fed decisions, market crashes, major industry collapses, jobs reports)\n"
        "4. Major geopolitical events involving US allies or US interests (attacks on NATO countries, peace deals, sanctions, treaties) — these affect US foreign policy even when they happen abroad\n"
        "5. Major US infrastructure/space/scientific events with national consequence (NASA missions, major tech failures with national impact)\n"
        "\n"
        "WEAK front-page heroes (these belong as cards, NOT as the lead):\n"
        "- Localized US tragedies (regional bus/car crashes, single-building fires, local crime) even with casualties — affect their region, not the nation\n"
        "- Foreign tragedies without US connection (foreign domestic crime, regional conflicts not involving US allies/interests)\n"
        "- Single-company news (funding rounds, IPOs, executive shakeups, single-quarter earnings)\n"
        "- Sports/entertainment unless truly historic\n"
        "- Celebrity deaths unless of major historical/cultural figures\n"
        "- Routine policy proposals without immediate broad impact\n"
        "\n"
        "Examples:\n"
        "- 'Russian drone strikes NATO ally Romania' BEATS 'Bus crash kills 5 in Virginia' — the drone strike has NATO/Article 5 implications affecting US foreign policy; the bus crash, however tragic, is regional.\n"
        "- 'Supreme Court rules on major case' BEATS 'Tech company raises $10B' — court rulings reshape US law for millions.\n"
        "- 'US bus crash kills 5' BEATS 'European train delay' — between two local stories, US readers care more about US events.\n"
        "\n"
        "Return ONLY the number of the chosen story. Just the number, nothing else."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        import re as _re
        match = _re.search(r"\d+", raw)
        if match:
            idx = int(match.group()) - 1
            if 0 <= idx < len(candidates):
                chosen = candidates[idx]
                print(f"  Front page hero: [{chosen['category_label']}] {chosen['hero'].get('headline','')[:60]}")
                return chosen
    except Exception as e:
        print(f"  Front page hero selection failed ({e}), falling back to score-based")

    # Fallback: score-based selection
    top_cat = max(candidates, key=_fp_score)
    if _fp_score(top_cat) < 5:
        top_cat = max(all_categories, key=_fp_score)
    return top_cat


def promote_duplicate_heroes(top_cat, all_categories):
    """If any other category's hero covers the same underlying story as the front page
    hero, promote that category's next non-duplicate card to be its hero instead.
    Uses Claude for reliable semantic matching (string matching is too brittle for
    rewritten headlines). Mutates all_categories in place."""
    fp_headline = top_cat["hero"].get("headline", "")
    fp_key      = top_cat["category_key"]
    others      = [c for c in all_categories if c["category_key"] != fp_key]
    if not others or not fp_headline:
        return

    listing = "\n".join(f"{i+1}. {c['hero'].get('headline','')}" for i, c in enumerate(others))
    prompt = (
        f"The lead front-page story is:\n\"{fp_headline}\"\n\n"
        f"Here are other section lead headlines:\n{listing}\n\n"
        "Which of these numbered headlines cover the SAME underlying event as the lead story? "
        "Same event means the same action by the same actors at the same time, even if worded "
        "completely differently (e.g. 'US strikes Iran' and 'US military hits Iranian launch site' "
        "are the same event).\n"
        "Return ONLY a JSON array of the numbers that are duplicates of the lead story. "
        "If none are duplicates, return []."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        dupes = set(int(x) for x in json.loads(raw))
    except Exception as e:
        print(f"  Hero dedup failed ({e}), leaving heroes as-is")
        return

    for i, cat in enumerate(others):
        if (i + 1) in dupes:
            cards = cat.get("cards", [])
            if cards:
                promoted = cards[0]
                cat["hero"] = {
                    "headline":      promoted.get("headline", ""),
                    "body":          promoted.get("body", ""),
                    "teaser":        promoted.get("teaser", ""),
                    "urgency_score": promoted.get("urgency_score", 0),
                    "published":     promoted.get("published", ""),
                    "image_url":     promoted.get("image_url", ""),
                    "image_credit":  promoted.get("image_credit", ""),
                    "link":          promoted.get("link", ""),
                }
                cat["cards"] = cards[1:]
                print(f"  Promoted next card to hero for {cat['category_label']} (was duplicate of front page hero)")


def global_rank(all_cards, dedupe_against=None):
    """Final global ranking — sends all headlines to Claude for true cross-category
    ordering AND semantic deduplication. Claude identifies stories that cover the same
    underlying event and keeps only the most important version.

    dedupe_against: optional headline string (e.g. the front page hero) that stories
    should also be deduplicated against — any story covering that same event is dropped.
    """
    if not all_cards:
        return all_cards

    ranked_input = all_cards
    stories = []
    for i, c in enumerate(ranked_input):
        cat   = c.get("cat_label", "")
        head  = c.get("headline", "")
        stories.append(f"{i+1}. [{cat}] {head}")
    stories_text = "\n".join(stories)
    n = len(ranked_input)

    dedupe_clause = ""
    if dedupe_against:
        dedupe_clause = (
            f"\nThe lead story already shown is: \"{dedupe_against}\"\n"
            "EXCLUDE any story from your list that covers this same underlying event, "
            "even if worded very differently or framed from a different angle.\n"
        )

    prompt = (
        f"Rank these {n} news stories by true global importance and urgency.\n"
        "This site serves a primarily US audience. The front page hero must be relevant to US readers.\n"
        "\n"
        "CRITICAL DEDUPLICATION RULE: Many of these stories cover the SAME underlying event "
        "from different angles or with different wording (e.g. 'US strikes Iran' and "
        "'US military shoots down Iranian drones, hits launch site' are the SAME event). "
        "Identify every cluster of stories about the same event and keep ONLY the single "
        "best version of each. Drop all the others entirely. Two stories are the same event "
        "if they describe the same action, by the same actors, at the same time — regardless "
        "of how differently they are phrased.\n"
        + dedupe_clause +
        "\n"
        "PRIMARY signal: consequence and US relevance combined.\n"
        "SECONDARY signal: recency — edited timestamps do not make old stories new.\n"
        "Apply this weighting:\n"
        "1. Major foreign policy events (military action, peace deals, sanctions, treaty changes), especially involving oil/trade routes or major allies: TOP TIER — these affect millions and reshape global affairs\n"
        "2. Direct US impact on millions of Americans (economy-wide policy, national security incidents, major Supreme Court rulings, mass casualties): very high\n"
        "3. Significant domestic political developments (major legislation, executive actions with broad impact, presidential decisions): high\n"
        "4. Major business stories that affect consumers/economy broadly (Fed decisions, major industry collapses, jobs reports): high\n"
        "5. Company-specific news (funding rounds, single-company earnings, executive changes, IPO plans): MEDIUM — even huge funding rounds for private companies rank BELOW major foreign policy or national news. A $10B Anthropic round is less important than a US-Iran de-escalation deal.\n"
        "6. International tragedies with no direct US connection: belong in World but should NOT lead. Rank below any US-relevant story.\n"
        "7. Follow-up stories: rank below genuinely new stories\n"
        "8. Sports, entertainment: rank below policy and crisis stories unless exceptionally significant\n"
        "When two stories seem equally important, use recency as a tiebreaker.\n\n"
        f"{stories_text}\n\n"
        "Return ONLY a JSON array of the original numbers, in ranked order, most important first, "
        "with all duplicates removed (only the best version of each distinct event included).\n"
        "Example: [4, 1, 12, 7]"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        indices = json.loads(raw)
        seen, ranked = set(), []
        for idx in indices:
            i = int(idx) - 1
            if 0 <= i < n and i not in seen:
                seen.add(i)
                ranked.append(ranked_input[i])
        # NOTE: stories Claude omitted are treated as duplicates and intentionally dropped.
        # Only append back un-ranked stories if Claude returned suspiciously few (failure guard).
        if len(ranked) < max(3, n // 3):
            for i, card in enumerate(ranked_input):
                if i not in seen:
                    ranked.append(card)
        print(f"  Global ranking: {len(ranked)} stories after dedup (from {n})")
        return ranked
    except Exception as e:
        print(f"  Global ranking failed ({e}), using urgency_score fallback")
        return all_cards


def fetch_market_data():
    """Fetch market data server-side during pipeline run. No CORS issues."""
    import requests as _req
    symbols = [
        ("sp500",  "^GSPC",  "S&P 500"),
        ("dow",    "^DJI",   "DOW"),
        ("nasdaq", "^IXIC",  "NASDAQ"),
        ("oil",    "CL=F",   "Oil"),
    ]
    results = {}
    for key, sym, label in symbols:
        try:
            url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
            hdrs = {"User-Agent": "Mozilla/5.0"}
            resp = _req.get(url, headers=hdrs, timeout=6)
            meta = resp.json()["chart"]["result"][0]["meta"]
            price  = meta["regularMarketPrice"]
            prev   = meta.get("previousClose") or meta.get("chartPreviousClose", price)
            change = (price - prev) / prev * 100
            state  = meta.get("marketState", "CLOSED")
            results[key] = {
                "label":  label,
                "price":  f"{price:,.2f}",
                "change": f"{change:+.2f}",
                "up":     change >= 0,
                "live":   state == "REGULAR",
            }
        except Exception as e:
            print(f"  Market fetch failed ({sym}): {e}")
            results[key] = None
    live = any(v and v["live"] for v in results.values())
    # Fallback: check actual ET time if Yahoo marketState is unreliable
    if not live:
        from datetime import timezone, timedelta
        et_now = datetime.now(timezone(timedelta(hours=-4)))
        is_weekday = et_now.weekday() < 5
        et_hour = et_now.hour + et_now.minute / 60
        live = is_weekday and 9.5 <= et_hour <= 16.0
    print(f"  Market data: {sum(1 for v in results.values() if v)} symbols fetched, market {'live' if live else 'closed'}")
    return results, live


def render_index(all_categories, market_data=None, market_live=False, top_cat=None):
    timestamp = now_et()
    # World excluded from front page hero unless it involves direct US action
    us_action_words = ["us strikes", "us military", "american forces", "u.s. strikes",
                       "u.s. military", "united states strikes", "trump orders", "pentagon"]
    def is_front_page_eligible(cat):
        if not CATEGORIES.get(cat["category_key"], {}).get("front_page_hero", True):
            headline = cat["hero"].get("headline", "").lower()
            return any(w in headline for w in us_action_words)
        return True
    def front_page_score(cat):
        score = cat["hero"].get("urgency_score", 0)
        cap   = CATEGORIES.get(cat["category_key"], {}).get("front_page_cap", 10)
        return min(score, cap)
    eligible = [c for c in all_categories if is_front_page_eligible(c)]
    if top_cat is None:
        top_cat  = max(eligible if eligible else all_categories, key=front_page_score)
        # If best eligible story scores below 5, include World as fallback
        if front_page_score(top_cat) < 5:
            top_cat = max(all_categories, key=front_page_score)
    hero_desc = top_cat["hero"].get("headline", "News without the noise")[:120]

    # Build market ticker HTML from server-side data
    def fmt_ticker(key, label):
        d = (market_data or {}).get(key)
        if not d:
            return f'<span class="ticker-item">{label} <span class="ticker-val">--</span></span>'
        cls  = "ticker-up" if d["up"] else "ticker-down"
        return f'<span class="ticker-item">{label} <span class="ticker-val">{d["price"]} <span class="{cls}">{d["change"]}%</span></span></span>'
    ticker_html = " ".join([
        fmt_ticker("sp500",  "S&amp;P 500"),
        fmt_ticker("dow",    "DOW"),
        fmt_ticker("nasdaq", "NASDAQ"),
        fmt_ticker("oil",    "Oil"),
    ])
    closed_html = '' if market_live else '<span class="ticker-closed">Market closed</span>'

    # -- Hero sections (one per category + "all") --
    def hero_section(cat_key, cat_label, hero, visible):
        display    = "" if visible else ' style="display:none"'
        fade       = " fade-in" if visible else ""
        preview    = hero["body"][:380].rstrip()
        paragraphs = make_paragraphs(hero["body"])
        img_url    = hero.get("image_url", "")
        img_credit = hero.get("image_credit", "")
        credit_html = f'<figcaption class="img-credit">Photo: {img_credit}</figcaption>' if img_url and img_credit else ""
        img_html   = f'<figure class="hero-image-wrap"><img class="hero-image" src="{img_url}" alt="{hero["headline"]}" loading="lazy">{credit_html}</figure>' if img_url else ""
        pub_time   = hero.get("published") or f"Today, {timestamp}"
        return f"""
    <section class="hero{fade}" data-cat-hero="{cat_key}"{display}>
      <div class="hero-inner">
        {img_html}
        <span class="tag">{cat_label}</span>
        <h1>{hero["headline"]}</h1>
        <p class="hero-summary">{preview}...</p>
        <div class="hero-foot">
          <span class="meta">{pub_time}</span>
          <button class="expand-btn" onclick="toggleExpand(this)">Continue reading &darr;</button>
        </div>
        <div class="article-expand hero-expand">
          <div class="hero-expand-body">{paragraphs}</div>
          <button class="collapse-btn" onclick="collapseThis(this)">Close &uarr;</button>
        </div>
      </div>
    </section>"""

    heroes_html  = hero_section("all", top_cat["category_label"], top_cat["hero"], visible=True)
    for cat in all_categories:
        heroes_html += hero_section(cat["category_key"], cat["category_label"], cat["hero"], visible=False)

    # -- Card grid -- category heroes + regular cards, sorted by urgency --
    all_cards = []
    top_cat_key = top_cat["category_key"]

    # Add category heroes to pool — skip top_cat (already shown as the All hero above)
    for cat in all_categories:
        if cat["category_key"] == top_cat_key:
            continue
        hero = cat["hero"]
        all_cards.append({
            "headline":      hero["headline"],
            "teaser":        hero["body"][:220].rstrip() + "...",
            "body":          hero["body"],
            "urgency_score": hero.get("urgency_score", 0),
            "image_url":     hero.get("image_url", ""),
            "cat_key":       cat["category_key"],
            "cat_label":     cat["category_label"],
            "is_hero":       True,
        })

    # Add regular cards
    for cat in all_categories:
        for card in cat["cards"]:
            all_cards.append({
                **card,
                "cat_key":   cat["category_key"],
                "cat_label": cat["category_label"],
                "is_hero":   False,
            })

    all_cards.sort(key=lambda c: int(c.get("urgency_score", 0) or 0), reverse=True)  # Pre-sort

    # Deduped set for the Top News front page (semantic dedup via Claude)
    topnews_cards = global_rank(all_cards, dedupe_against=top_cat["hero"]["headline"])

    # Mark which cards are in the Top News set (by identity) so the JS filter can
    # show the deduped set for "Top News" but the full set for category views
    topnews_ids = {id(c) for c in topnews_cards}

    # Order: Top News cards first (in ranked order), then remaining cards grouped after.
    # This way the grid contains every card, but the front page only reveals the deduped set.
    remaining_cards = [c for c in all_cards if id(c) not in topnews_ids]
    all_cards = topnews_cards + remaining_cards

    # Static support card injected at position 3
    support_card = """
      <div class="article-card support-card fade-in" data-cat="all" data-support-card="true">
        <span class="card-tag support-card-tag">Plain</span>
        <h2 class="card-headline support-card-headline">Plain is free. Help keep it that way.</h2>
        <p class="card-summary">No ads. No paywalls. No agenda. Plain runs entirely on reader support. If it's worth something to you, consider buying us a coffee.</p>
        <div class="card-foot">
          <a href="https://buymeacoffee.com/andrewdobrow" target="_blank" class="support-card-btn">Support Plain &#9829;</a>
        </div>
      </div>"""

    cards_html = ""
    for i, card in enumerate(all_cards):
        if i == 2:
            cards_html += support_card
        teaser = card.get("teaser", card.get("summary", ""))
        body   = card.get("body", card.get("summary", ""))
        card_paragraphs = make_paragraphs(body)
        ck        = card["cat_key"]
        cl        = card["cat_label"]
        card_time = card.get("published") or timestamp
        is_hero_attr = ' data-is-hero="true"' if card.get("is_hero") else ""
        topnews_attr = ' data-topnews="true"' if id(card) in topnews_ids else ""
        cards_html += f"""
      <div class="article-card fade-in" data-cat="{ck}"{is_hero_attr}{topnews_attr}>
        <span class="card-tag">{cl}</span>
        <h2 class="card-headline">{card["headline"]}</h2>
        <p class="card-summary">{teaser}</p>
        <div class="card-foot">
          <span class="card-time">{card_time}</span>
          <button class="expand-btn" onclick="toggleExpand(this)">Continue reading &darr;</button>
        </div>
        <div class="article-expand">
          <div class="card-expand-body">{card_paragraphs}</div>
          <button class="collapse-btn" onclick="collapseThis(this)">Close &uarr;</button>
        </div>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Plain - News without the noise</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,500;0,9..144,600;1,9..144,300&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap" rel="stylesheet">
  <meta name="description" content="Plain: {hero_desc} — Updated every hour. No ads. No agenda. Always free.">
  <!-- Open Graph -->
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://plainnews.app">
  <meta property="og:title" content="Plain — News without the noise">
  <meta property="og:description" content="Updated every hour. No ads. No paywalls. No agenda. Just the news that matters.">
  <meta property="og:image" content="https://plainnews.app/social-card.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Plain — News without the noise">
  <meta name="twitter:description" content="Updated every hour. No ads. No paywalls. No agenda. Just the news that matters.">
  <meta name="twitter:image" content="https://plainnews.app/social-card.png">
  <link rel="stylesheet" href="style.css?v={datetime.utcnow().strftime('%Y%m%d%H')}">
  <!-- Google tag (gtag.js) -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-GZ5F591SL0"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', 'G-GZ5F591SL0');
  </script>
</head>
<body>

  <header>
    <div class="header-inner">
      <a href="/" class="wordmark">plain</a>
      <nav class="category-nav">
        <button class="cat-btn active" data-cat="all">Top News</button>
        <button class="cat-btn" data-cat="world">World</button>
        <button class="cat-btn" data-cat="us">U.S.</button>
        <button class="cat-btn" data-cat="politics">Politics</button>
        <button class="cat-btn" data-cat="business">Business</button>
        <button class="cat-btn" data-cat="tech">Tech & Science</button>
        <button class="cat-btn" data-cat="sports">Sports</button>
        <button class="cat-btn" data-cat="entertainment">Entertainment</button>
      </nav>
      <div class="header-actions">
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">&#9790;</button>
        <button class="support-btn" onclick="window.open('https://buymeacoffee.com/andrewdobrow','_blank')">Support Plain</button>
      </div>
    </div>
  </header>

  <div class="update-bar">
    Updated at <strong>{timestamp}</strong> &mdash; Next update in <strong id="countdown">57 min</strong>
  </div>

  <div class="market-ticker">
    <div class="ticker-inner">
      <span class="ticker-label">Markets</span>
      {ticker_html}
      {closed_html}
    </div>
  </div>

  <main>
    {heroes_html}

    <p class="section-label">Latest</p>

    <div class="articles-grid" id="articlesGrid">
      {cards_html}
    </div>

    <div class="support-box" id="support">
      <div class="support-box-text">
        <p>Plain runs on reader support.</p>
        <span>No ads. No investors. No agenda. Just a belief that clean news should be free and a real cost to keep it that way. If Plain is part of your day, consider buying us a coffee.</span>
      </div>
      <button class="support-box-btn" onclick="window.open('https://buymeacoffee.com/andrewdobrow','_blank')">Support Plain &#9829;</button>
    </div>
  </main>

  <footer>
    <div class="footer-inner">
      <span class="footer-wordmark">plain</span>
      <span class="footer-tagline">Updated every hour. No ads. No noise. Always free.</span>
      <div class="footer-links">
        <a href="about.html">About</a>
        <a href="https://buymeacoffee.com/andrewdobrow" target="_blank">Support</a>
        <a href="mailto:anjrued123@gmail.com">Contact</a>
        <a href="privacy.html">Privacy</a>
        <a href="https://lowsignal.dev" target="_blank">Built by Low Signal Labs</a>
      </div>
    </div>
  </footer>

  <script src="main.js?v={datetime.utcnow().strftime('%Y%m%d%H')}"></script>
</body>
</html>"""



# -- MAIN --

def main():
    all_categories = []

    # Build image bank and content bank once per run
    print("Building image bank...")
    image_bank = build_image_bank()
    print("Building content bank...")
    content_bank = build_content_bank()

    for cat_key, cat_config in CATEGORIES.items():
        print(f"Processing: {cat_config['label']}...")
        headlines = fetch_headlines(cat_config["feeds"])

        # Filter headlines older than 48 hours — unparseable dates are treated as stale
        from datetime import timezone as _tz2
        _now2 = datetime.now(_tz2.utc)
        def _headline_stale(h):
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(h.get("published","")).astimezone(_tz2.utc)
                return (_now2 - dt).total_seconds() > 48 * 3600
            except Exception:
                return True  # Can't parse date = treat as stale, exclude it
        fresh_h = [h for h in headlines if not _headline_stale(h)]
        if cat_key == "politics":
            print(f"  Politics debug: {len(headlines)} total, {len(fresh_h)} fresh")
            for h in headlines[:5]:
                print(f"    [{h.get('published','NO DATE')}] {h.get('title','')[:60]}")
        if len(fresh_h) >= 6:
            headlines = fresh_h
        if not headlines:
            print(f"  No headlines found for {cat_config['label']}, skipping.")
            continue
        try:
            data = generate_category_content(cat_key, cat_config["label"], headlines)

            # Images — source_index already attached image_url, fall back to image bank
            source_img = data["hero"].get("image_url", "")
            bank_img, bank_credit = match_image(data["hero"]["headline"], image_bank, cat_key)
            img    = source_img or bank_img
            credit = bank_credit  # bank_credit is empty string if no match, that's fine
            data["hero"]["image_credit"] = credit
            # Second fallback: check content bank entries for matching images
            if not img:
                for entry in content_bank:
                    entry_lower = entry.get("title", "").lower()
                    hero_words  = [w for w in data["hero"]["headline"].lower().split() if len(w) > 4]
                    if sum(1 for w in hero_words if w in entry_lower) >= 2:
                        # Try to get image from matching content bank entry source feed
                        _fb = match_image(entry["title"], image_bank, cat_key)
                        if _fb[0]:
                            img = _fb[0]
                            data["hero"]["image_credit"] = _fb[1]
                            break
            data["hero"]["image_url"] = img

            # Hero enrichment — combine all available sources
            hero_headline = data["hero"]["headline"]

            # 1. Guardian API — full article text (best source when available)
            guardian_text = fetch_guardian_article(hero_headline)

            # 2. Content bank — rich publisher summaries from BBC, NPR, Guardian RSS etc
            bank_content  = find_content(hero_headline, content_bank)

            # 3. Related RSS summaries from the category feed
            hero_idx     = data["hero"].get("source_index", 1) - 1
            related_parts = []
            hero_tokens   = set(re.sub(r"[^a-z0-9 ]", " ", hero_headline.lower()).split())
            stops         = {"that","this","with","from","have","been","said","will","more",
                             "also","when","were","they","their","about","says","just"}
            hero_tokens  -= stops
            for h in headlines:
                h_tokens = set(re.sub(r"[^a-z0-9 ]", " ", h.get("title","").lower()).split()) - stops
                if len(hero_tokens & h_tokens) >= 2:
                    related_parts.append(h.get("title","") + ". " + h.get("summary",""))
            related_text = " | ".join(related_parts[:6])

            # Combine: Guardian full text first, then bank content, then related summaries
            source_parts = [p for p in [guardian_text, bank_content, related_text] if p]
            source_text  = "\n\n".join(source_parts)

            if source_text and len(source_text.split()) >= 100:
                # Final relevance check — ensure source actually relates to hero headline
                stops2 = {"the","a","an","in","of","for","to","and","or","on","at","is","was","are","were","that","this","with"}
                hl_tok = set(re.sub(r"[^a-z0-9 ]", " ", hero_headline.lower()).split()) - stops2
                src_tok = set(re.sub(r"[^a-z0-9 ]", " ", source_text[:500].lower()).split()) - stops2
                # Geographic mismatch check — key country/place names must not conflict
                geo_words = {"china","chinese","russia","russian","ukraine","ukrainian","iran","israeli","israel",
                             "australia","australian","india","indian","france","french","germany","german",
                             "britain","british","uk","japan","japanese","brazil","mexican","mexico",
                             "congo","ebola","africa","african","europe","european","california","texas",
                             "florida","washington","london","paris","beijing","moscow","gaza",
                             "maralago","capitol","pentagon","whitehouse","nasa","nascar","senate","congress"}
                hl_geo  = hl_tok & geo_words
                src_geo = src_tok & geo_words
                geo_conflict = bool(hl_geo) and bool(src_geo) and not (hl_geo & src_geo)
                if geo_conflict:
                    print(f"  Enhancement skipped: geographic mismatch ({hl_geo} vs {src_geo})")
                elif len(hl_tok & src_tok) >= 3:
                    data["hero"] = enhance_hero_article(data["hero"], source_text)
                    print(f"  Enhanced with: {'Guardian+' if guardian_text else ''}{'bank+' if bank_content else ''}{'related' if related_text else ''}")
                else:
                    print(f"  Enhancement skipped: insufficient keyword overlap")

            all_categories.append(data)
            print(f"  Hero: {data['hero']['headline'][:60]}... (urgency: {data['hero'].get('urgency_score')}, image: {'yes' if img else 'no'})")

            # Enrich cards with content bank + related summaries
            for card in data.get("cards", []):
                enhance_card(card, content_bank, headlines)

        except Exception as e:
            print(f"  Claude error for {cat_config['label']}: {e}")
            continue

    if not all_categories:
        print("No categories generated. Aborting.")
        return

    print("Fetching market data...")
    market_data, market_live = fetch_market_data()

    def _is_fp_eligible(cat):
        if not CATEGORIES.get(cat["category_key"], {}).get("front_page_hero", True):
            us_words = ["us strikes", "us military", "american forces", "u.s. strikes",
                        "u.s. military", "united states strikes", "trump orders", "pentagon"]
            return any(w in cat["hero"].get("headline", "").lower() for w in us_words)
        return True
    def _fp_score(cat):
        score = cat["hero"].get("urgency_score", 0)
        cap   = CATEGORIES.get(cat["category_key"], {}).get("front_page_cap", 10)
        return min(score, cap)
    # Semantic front page hero selection — Claude picks the most front-page-worthy story
    # across all candidate category heroes (not just highest urgency_score)
    top_cat = select_front_page_hero(all_categories)
    if top_cat is None:
        # Fallback to score-based if Claude call totally failed
        def _is_fp_eligible(cat):
            if not CATEGORIES.get(cat["category_key"], {}).get("front_page_hero", True):
                us_words = ["us strikes", "us military", "american forces", "u.s. strikes",
                            "u.s. military", "united states strikes", "trump orders", "pentagon"]
                return any(w in cat["hero"].get("headline", "").lower() for w in us_words)
            return True
        def _fp_score(cat):
            score = int(cat["hero"].get("urgency_score", 0) or 0)
            cap   = CATEGORIES.get(cat["category_key"], {}).get("front_page_cap", 10)
            return min(score, cap)
        _eligible = [c for c in all_categories if _is_fp_eligible(c)]
        top_cat   = max(_eligible if _eligible else all_categories, key=_fp_score)
        if _fp_score(top_cat) < 5:
            top_cat = max(all_categories, key=_fp_score)

    # Ensure no other category leads with the same story as the front page hero.
    # Any category whose hero duplicates the front page hero gets its next card promoted.
    promote_duplicate_heroes(top_cat, all_categories)

    # Global deduplication — one story, one appearance across all categories
    import re as _re2

    def _headline_key(h):
        return _re2.sub(r'[^a-z0-9 ]', '', h.lower().strip())[:80]

    def _similar(a, b):
        ka, kb = _headline_key(a), _headline_key(b)
        if ka[:60] == kb[:60] or ka in kb or kb in ka:
            return True
        stops = {"a","an","the","in","on","at","to","for","of","and","or","is","are",
                 "was","were","with","its","by","as","from","that","this","after",
                 "over","into","about","amid","during","says","say","new","s"}
        wa = set(ka.split()) - stops
        wb = set(kb.split()) - stops
        if not wa or not wb:
            return False
        overlap = len(wa & wb) / min(len(wa), len(wb))
        return overlap >= 0.65

    # Define _hero_similar for use in front page dedup below
    def _hero_similar(a, b):
        ka, kb = _headline_key(a), _headline_key(b)
        if ka[:50] == kb[:50] or ka in kb or kb in ka:
            return True
        stops = {"a","an","the","in","on","at","to","for","of","and","or","is","are",
                 "was","were","with","its","by","as","from","that","this","after",
                 "over","into","about","amid","during","says","say","new","s"}
        wa = set(ka.split()) - stops
        wb = set(kb.split()) - stops
        if not wa or not wb:
            return False
        overlap = len(wa & wb) / min(len(wa), len(wb))
        return overlap >= 0.35

    # Categories are NOT deduplicated — World, U.S., Politics can all cover Iran
    # Deduplication only happens on the front page (_all_cards) below

    index_html = render_index(all_categories, market_data, market_live, top_cat=top_cat)
    (OUTPUT_DIR / "index.html").write_text(index_html, encoding="utf-8")

    # Write data.json for the iOS app
    import json as _json
    _timestamp = now_et()

    # Build front page cards for the app using the SAME semantic dedup as the website
    _all_cards = []
    for cat in all_categories:
        hero = cat["hero"]
        _all_cards.append({
            "headline":      hero.get("headline", ""),
            "teaser":        hero.get("teaser", ""),
            "body":          hero.get("body", ""),
            "published":     hero.get("published", ""),
            "cat_label":     cat["category_label"],
            "urgency_score": hero.get("urgency_score", 0),
            "is_hero":       True,
        })
        for card in cat.get("cards", []):
            _all_cards.append({
                "headline":      card.get("headline", ""),
                "teaser":        card.get("teaser", ""),
                "body":          card.get("body", ""),
                "published":     card.get("published", ""),
                "cat_label":     cat["category_label"],
                "urgency_score": card.get("urgency_score", 0),
                "is_hero":       False,
            })
    _all_cards.sort(key=lambda c: int(c.get("urgency_score", 0) or 0), reverse=True)
    # Semantic dedup via Claude — same approach as the website front page
    _all_cards = global_rank(_all_cards, dedupe_against=top_cat["hero"].get("headline", ""))

    def card_to_dict(c):
        return {
            "headline":      c.get("headline", ""),
            "teaser":        c.get("teaser", ""),
            "body":          c.get("body", ""),
            "published":     c.get("published", ""),
            "cat_label":     c.get("cat_label", ""),
            "urgency_score": c.get("urgency_score", 0),
        }

    app_data = {
        "updated": _timestamp,
        "market": {
            "live": market_live,
            "sp500": market_data.get("sp500") if market_data else None,
            "dow":   market_data.get("dow")   if market_data else None,
            "nasdaq":market_data.get("nasdaq") if market_data else None,
            "oil":   market_data.get("oil")   if market_data else None,
        },
        "front_page": {
            "hero": {
                "headline":      top_cat["hero"].get("headline", ""),
                "teaser":        top_cat["hero"].get("teaser", ""),
                "body":          top_cat["hero"].get("body", ""),
                "image_url":     top_cat["hero"].get("image_url", ""),
                "image_credit":  top_cat["hero"].get("image_credit", ""),
                "published":     top_cat["hero"].get("published", ""),
                "cat_label":     top_cat["category_label"],
                "urgency_score": top_cat["hero"].get("urgency_score", 0),
            },
            "cards": [card_to_dict(c) for c in _all_cards[:6]]
        },
        "categories": [
            {
                "key":   cat["category_key"],
                "label": cat["category_label"],
                "hero": {
                    "headline":      cat["hero"].get("headline", ""),
                    "teaser":        cat["hero"].get("teaser", ""),
                    "body":          cat["hero"].get("body", ""),
                    "image_url":     cat["hero"].get("image_url", ""),
                    "image_credit":  cat["hero"].get("image_credit", ""),
                    "published":     cat["hero"].get("published", ""),
                    "urgency_score": cat["hero"].get("urgency_score", 0),
                },
                "cards": [
                    {
                        "headline":      c.get("headline", ""),
                        "teaser":        c.get("teaser", ""),
                        "body":          c.get("body", ""),
                        "published":     c.get("published", ""),
                        "urgency_score": c.get("urgency_score", 0),
                    }
                    for c in cat.get("cards", [])[:6]
                ]
            }
            for cat in all_categories
        ]
    }
    (OUTPUT_DIR / "data.json").write_text(_json.dumps(app_data, indent=2), encoding="utf-8")
    print(f"\nDone. {len(all_categories)} categories written to index.html + data.json.")


if __name__ == "__main__":
    main()
