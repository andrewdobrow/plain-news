"""
Plain News - hourly generation pipeline
Fetches RSS headlines, ranks by urgency via Claude, writes articles, rebuilds site.
"""

import os
import sys
import json
import re
import hashlib
import copy
try:
    import feedparser
except Exception:
    feedparser = None
try:
    import anthropic
except Exception:
    anthropic = None
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from plain_engine.editorial_rules import SYSTEM_PROMPT, category_rule
from plain_engine.archive_identity import (
    find_matching_entry,
    signature_tokens as _sig_tokens,
    is_duplicate_headline as _is_duplicate_headline,
)
from plain_engine import write_homepage_ranking_recommendations
from plain_engine.generation_cache import PersistentGenerationCache, CACHE_MISS, cache_hash
from plain_engine.source_recovery import prefetch_feed_documents, fetch_headlines as recover_headlines, build_content_bank as build_recovered_content_bank, fetch_guardian_match
from plain_engine.image_authority import extract_image as authoritative_extract_image, build_image_bank as build_authoritative_image_bank, ensure_item_image, FallbackRotator, write_image_quality_report
from plain_engine.assignment_pipeline import run_live_assignment_category
from plain_engine.category_classifier import classify_stories
from plain_engine.article_quality import enforce_category_quality, write_quality_report, publication_quality, protected_material_update_pending_recomposition
from plain_engine.model_usage import ModelUsageTracker, instrument_anthropic_client
from plain_engine.model_response import extract_model_text, parse_first_json_value
from plain_engine.semantic_publication_gate import retrieve_recent_candidates, adjudicate_candidates, ACTION_DUPLICATE, ACTION_UPDATE, ACTION_NEW, ACTION_HOLD
from plain_engine.semantic_material_update import compose_material_update
from scripts.editorial_runtime import apply_editorial_engine

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
SITE_URL               = "https://plainnews.app"
SITE_NAME              = "Plain"


_client = None
_usage_tracker = None
_generation_cache = None

def get_generation_cache():
    global _generation_cache
    if _generation_cache is None:
        _generation_cache = PersistentGenerationCache(OUTPUT_DIR / "data" / "generation-cache.json")
    return _generation_cache

def get_client():
    global _client, _usage_tracker
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required to generate Plain News content")
        if anthropic is None:
            raise RuntimeError("anthropic package is required to generate Plain News content")
        _usage_tracker = ModelUsageTracker(OUTPUT_DIR / "data" / "model-usage-report.json")
        _client = instrument_anthropic_client(anthropic.Anthropic(api_key=api_key), _usage_tracker)
    return _client

def write_model_usage_report():
    if _usage_tracker is not None:
        try:
            report = _usage_tracker.write_report()
            print(f"  Model usage: {report.get('totals',{}).get('requests',0)} requests, estimated ${report.get('totals',{}).get('estimated_list_cost_usd',0):.4f}")
        except Exception as exc:
            print(f"  Model usage report warning: {exc}")

# ---------------------------------------------------------------------------
# Local hosted fallback images — /images/fallback/ in the plain-news repo.
# Three images per category, selected deterministically so the same headline
# always gets the same image across pipeline runs.
# ---------------------------------------------------------------------------
FALLBACK_IMAGE_MAP = {
    "top_news":     ["top_news-1.jpg",     "top_news-2.jpg",     "top_news-3.jpg"],
    "world":        ["world-1.jpg",        "world-2.jpg",        "world-3.jpg"],
    "us":           ["us-1.jpg",           "us-2.jpg",           "us-3.jpg"],
    "politics":     ["politics-1.jpg",     "politics-2.jpg",     "politics-3.jpg"],
    "business":     ["business-1.jpg",     "business-2.jpg",     "business-3.jpg"],
    "tech":         ["tech-1.jpg",         "tech-2.jpg",         "tech-3.jpg"],
    "sports":       ["sports-1.jpg",       "sports-2.jpg",       "sports-3.jpg"],
    "entertainment":["entertainment-1.jpg","entertainment-2.jpg","entertainment-3.jpg"],
}

def get_fallback_image(category_key, headline=""):
    """Pick a deterministic local fallback image for the given category.
    Returns (url, credit) or ('', '') if no fallback images exist yet."""
    base_names = FALLBACK_IMAGE_MAP.get(category_key, FALLBACK_IMAGE_MAP["top_news"])
    available = []
    for base in base_names:
        stem = base.rsplit(".", 1)[0]
        for ext in ["jpg", "jpeg", "png", "webp"]:
            path = OUTPUT_DIR / "images" / "fallback" / f"{stem}.{ext}"
            if path.exists():
                available.append(f"{stem}.{ext}")
                break
    if not available:
        return "", ""
    seed = headline or category_key or "top_news"
    idx  = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % len(available)
    return f"{SITE_URL}/images/fallback/{available[idx]}", "Plain"


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
            for entry in feed.entries[:60]:
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

    # Fallback pass: if no 3-token match, try distinctive long tokens (>=6 chars).
    # Two shared distinctive terms (e.g. "longview"+"mill", "frankie"+"valli") are a
    # confident match even when the rewritten headline shares few common words.
    if not best_img:
        distinctive = {w for w in hw if len(w) >= 7}
        if distinctive:
            for entry in image_bank:
                source = entry.get("source", "").lower()
                if any(b in source for b in blocked_sources):
                    continue
                entry_tokens = {w for w in tokens(entry["title"]) if len(w) >= 7}
                overlap = len(distinctive & entry_tokens)
                if overlap > best_score and overlap >= 3:
                    entry_geo = tokens(entry["title"]) & geo_words
                    if hl_geo and entry_geo and not (hl_geo & entry_geo):
                        continue
                    best_score  = overlap
                    best_img    = upscale_image_url(entry["image_url"])
                    best_credit = get_image_credit(entry.get("source", ""))

    return best_img, best_credit


PLACEHOLDER_URL_PATTERNS = [
    "brand-icons", "brand_icons",
    "default-image", "default_image", "defaultimage",
    "top_image", "top-image",
    "htv_default", "htv-default",
    "news-slate", "news_slate",
    "og-image.png", "og_image.png",
    "eenewslogo", "site-logo", "site_logo",
    "station-logo", "stationlogo",
    "wpec-16x9", "wpbf", "wflx",
    "aolfp/images", "cbsnewsstatic.com/hub",
    "yimg.com/cv/apiv2",
    "foxtv.com/img",
    "gray.tv/gray/arc-fusion-assets",
    "townnews.com/content/tncms/custom",
    "bloximages",
]

def is_placeholder_image(img_url):
    url_lower = img_url.lower()
    return any(pat in url_lower for pat in PLACEHOLDER_URL_PATTERNS)


def fetch_og_image(url):
    """Fetch an article page and extract its og:image (or twitter:image) meta tag.
    This is the most reliable image source because it comes from the article itself,
    guaranteeing the image actually matches the story. Returns "" on any failure."""
    if not url:
        return ""
    try:
        import re as _re_og
        resp = requests.get(url, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; PlainBot/1.0)"})
        if resp.status_code != 200:
            return ""
        html = resp.text[:200000]  # only need the <head>
        # Try og:image then twitter:image, in either attribute order
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pat in patterns:
            m = _re_og.search(pat, html, _re_og.IGNORECASE)
            if m:
                img = m.group(1).strip()
                if img.startswith("http"):
                    return img
        return ""
    except Exception:
        return ""


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

# Model-facing editorial rules live in plain_engine/editorial_rules.py.

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

    # Category-specific national editorial contract lives with the engine policy.
    rule = category_rule(category_key)
    rule_line = f"\n\nCATEGORY RULE: {rule}" if rule else ""

    prompt = f"""Top headlines for {category_label}:{rule_line}

{headlines_text}

Tasks:
1. Pick the single most important/urgent story. Follow the CATEGORY RULE above strictly — if the most urgent story violates the category rule, pick the next best story that fits.
2. Write an accurate headline reflecting the current state (frame updates as updates, not new events).
3. Write a 420-480 word factual article in FOUR to FIVE full paragraphs. Use only confirmed facts from the source, written in your own words. This is the lead front-page story, so it must read as a complete article, not a brief summary. Cover the what, who, when, where, and the broader context or consequences. Do NOT write only two short paragraphs. If the source facts are limited, expand on confirmed context (background, why it matters, what happens next) rather than padding with filler or absence language.
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

    response = get_client().messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2400,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        }],
        messages=[{"role": "user", "content": prompt}],
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )

    raw = extract_model_text(response)
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
                item["link"]                 = source.get("link", "")
                item["image_url"]            = source.get("image_url", "")
                item["source_title"]         = source.get("title", "")
                item["source_summary"]       = source.get("summary", "")
                item["source_published_raw"] = source.get("published", "")
            except (IndexError, ValueError, TypeError):
                item["link"]                 = ""
                item["image_url"]            = ""
                item["source_title"]         = ""
                item["source_summary"]       = ""
                item["source_published_raw"] = ""
        else:
            item["link"]                 = ""
            item["image_url"]            = ""
            item["source_title"]         = ""
            item["source_summary"]       = ""
            item["source_published_raw"] = ""

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

    # Stale-hero swap: if the chosen hero describes an OLD event (even with a refreshed
    # timestamp), promote the freshest non-stale card to hero instead. Timestamp filtering
    # alone misses stories that publishers re-touch, so we also scan the body content.
    from email.utils import parsedate_to_datetime as _pdt
    from datetime import timezone as _tzc, timedelta as _tdc
    _now_c = datetime.now(_tzc.utc)
    _yest  = (_now_c - _tdc(days=1)).strftime("%A").lower()
    _2day  = (_now_c - _tdc(days=2)).strftime("%A").lower()
    _3day  = (_now_c - _tdc(days=3)).strftime("%A").lower()
    _4day  = (_now_c - _tdc(days=4)).strftime("%A").lower()
    _stale_days = {_yest, _2day, _3day, _4day}
    _fresh_override = ["today", "this morning", "this afternoon", "this evening",
                       "hours ago", "minutes ago", "just announced", "just released",
                       "breaking", "moments ago", "earlier today", "announced today",
                       "arrested today", "ruled today", "confirmed today"]
    _stale_phrases = ["yesterday", "two days ago", "three days ago", "earlier this week",
                      "last week", "days ago", "happened on", "occurred on", "took place on"]

    def _story_is_stale(item):
        content = (item.get("teaser", "") + " " + item.get("body", "")[:800]).lower()
        # Fresh-development language always wins (e.g. "suspect arrested today" in an old story)
        if any(p in content for p in _fresh_override):
            return False
        # Past day-name reference (e.g. "on Thursday" when today is Saturday)
        for day in _stale_days:
            if f" {day} " in content or f" {day}," in content or f" {day}." in content or content.startswith(f"{day} "):
                return True
        # Stale-event phrases
        if any(p in content for p in _stale_phrases):
            return True
        # Timestamp 24+ hours old via original RSS source
        idx = item.get("source_index")
        if idx is not None:
            try:
                pub_raw = headlines[int(idx) - 1].get("published", "")
                if pub_raw:
                    dt  = _pdt(pub_raw).astimezone(_tzc.utc)
                    if (_now_c - dt).total_seconds() / 3600 >= 24:
                        return True
            except Exception:
                pass
        return False

    if _story_is_stale(data["hero"]) and data.get("cards"):
        for ci, card in enumerate(data["cards"]):
            if not _story_is_stale(card):
                old_hero = data["hero"]
                print(f"  Stale hero swapped: '{old_hero.get('headline','')[:50]}' -> '{card.get('headline','')[:50]}'")
                # Give the demoted hero a teaser (heroes lack one; cards need one)
                if not old_hero.get("teaser"):
                    _body = old_hero.get("body", "").strip()
                    _first = _body.split(". ")[0].strip()
                    old_hero["teaser"] = (_first[:160] + ".") if _first else ""
                data["hero"] = card
                data["cards"][ci] = old_hero
                break

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
        resp = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        enhanced = extract_model_text(resp)
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
        resp = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        enhanced = extract_model_text(resp)
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
        try:
            dt_utc = parsedate_to_datetime(published_str).astimezone(timezone.utc)
        except Exception:
            dt_utc = datetime.fromisoformat(str(published_str).replace("Z", "+00:00")).astimezone(timezone.utc)
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

    # Compute age for each candidate so Claude can weight freshness
    from email.utils import parsedate_to_datetime
    from datetime import timezone as _tz, timedelta
    _now = datetime.now(_tz.utc)

    # HARD pre-filter: exclude candidates that are clearly stale events.
    # This runs before Claude sees anything, so Claude cannot pick a known-stale story.
    # A candidate is filtered out if:
    #   (1) its timestamp is more than 18 hours old AND content has no fresh-development language, OR
    #   (2) its content explicitly mentions a past day-name when today is a different day
    _today_name      = _now.strftime("%A").lower()
    _yesterday_name  = (_now - timedelta(days=1)).strftime("%A").lower()
    _two_days_name   = (_now - timedelta(days=2)).strftime("%A").lower()
    _three_days_name = (_now - timedelta(days=3)).strftime("%A").lower()
    _stale_day_names = {_yesterday_name, _two_days_name, _three_days_name}

    _stale_event_phrases = [
        "yesterday", "two days ago", "three days ago", "earlier this week",
        "last week", "days ago", "happened on", "occurred on",
    ]
    _fresh_dev_phrases = [
        "today", "this morning", "this afternoon", "this evening",
        "hours ago", "minutes ago", "just announced", "just released",
        "breaking", "moments ago", "earlier today",
    ]

    def _is_stale(cat):
        hero = cat["hero"]
        content = (hero.get("teaser", "") + " " + hero.get("body", "")[:800]).lower()
        # Fresh-development language wins regardless of timestamp
        if any(p in content for p in _fresh_dev_phrases):
            return False
        # Check for past day-name references (e.g. "Thursday" when today is Saturday)
        for day in _stale_day_names:
            # Check the day appears as a standalone word
            if f" {day} " in content or content.startswith(f"{day} ") or f" {day}." in content or f" {day}," in content:
                return True
        # Check for stale-event phrases
        if any(p in content for p in _stale_event_phrases):
            return True
        # Check timestamp — 18+ hours old with no fresh-development language is stale
        pub = hero.get("published", "")
        if pub:
            try:
                dt  = parsedate_to_datetime(pub).astimezone(_tz.utc)
                hrs = (_now - dt).total_seconds() / 3600
                if hrs >= 18:
                    return True
            except Exception:
                pass
        return False

    fresh_candidates = [c for c in candidates if not _is_stale(c)]
    if fresh_candidates:
        filtered_out = [c["hero"].get("headline", "")[:60] for c in candidates if c not in fresh_candidates]
        if filtered_out:
            print(f"  Hero pre-filter excluded {len(filtered_out)} stale candidate(s): {filtered_out}")
        candidates = fresh_candidates
    else:
        print(f"  Hero pre-filter: no fresh candidates, keeping all for Claude to decide")

    if len(candidates) == 1:
        print(f"  Front page hero: [{candidates[0]['category_label']}] {candidates[0]['hero'].get('headline','')[:60]} (only fresh candidate)")
        return candidates[0]

    def _age_label(cat):
        pub = cat["hero"].get("published", "")
        if not pub:
            return "unknown age"
        try:
            dt  = parsedate_to_datetime(pub).astimezone(_tz.utc)
            hrs = (_now - dt).total_seconds() / 3600
            if hrs < 1:
                mins = max(1, int((_now - dt).total_seconds() / 60))
                return f"{mins} minutes ago"
            if hrs < 24:
                return f"{int(hrs)} hours ago"
            days = int(hrs / 24)
            return f"{days} day{'s' if days != 1 else ''} ago"
        except Exception:
            return "unknown age"

    listing = "\n\n".join(
        f"{i+1}. [{c['category_label']}] (timestamp: {_age_label(c)}) {c['hero'].get('headline','')}\n"
        f"   Content: {(c['hero'].get('teaser','') + ' ' + c['hero'].get('body','')[:500]).strip()}"
        for i, c in enumerate(candidates)
    )
    _today_label = _now.strftime("%A, %B %d, %Y")
    _yesterday   = (_now - timedelta(days=1)).strftime("%A")
    _two_days    = (_now - timedelta(days=2)).strftime("%A")
    _three_days  = (_now - timedelta(days=3)).strftime("%A")
    prompt = (
        f"TODAY IS: {_today_label}\n"
        f"Yesterday was {_yesterday}. Two days ago was {_two_days}. Three days ago was {_three_days}.\n"
        "Use this date context to evaluate when events actually happened.\n\n"
        "You are selecting the SINGLE most front-page-worthy story for a US news app from these section heroes.\n\n"
        f"{listing}\n\n"
        "AUDIENCE: This app is for US readers. Skew toward stories that matter MOST to a US reader.\n"
        "But \"matters to US readers\" is about national/systemic relevance, NOT just whether it happened on US soil. "
        "A regional US tragedy is local to that region; a foreign event involving US allies or US interests can have "
        "broader national implications.\n"
        "\n"
        "FRESHNESS IS CRITICAL: This app updates throughout the day. The front page hero must be CURRENT news — what is "
        "happening NOW, not what already happened. Each candidate has a timestamp label, but BE SKEPTICAL OF TIMESTAMPS — "
        "publishers often re-publish or update old articles with minor changes, which refreshes the timestamp even though "
        "the event itself is old.\n"
        "\n"
        "CRITICAL DISTINCTION: differentiate between (A) an OLD EVENT being republished with a refreshed timestamp, vs "
        "(B) a NEW DEVELOPMENT in an ongoing story. The former is stale; the latter is fresh and can absolutely be hero-worthy.\n"
        "- (A) STALE: The article describes an event that already happened, with no new action today. Example: \"Blue Origin "
        "rocket exploded yesterday at Cape Canaveral\" — this is yesterday's explosion being recapped. Avoid as hero.\n"
        "- (B) FRESH: The article describes a NEW action, decision, arrest, ruling, statement, or development today, even if "
        "it's connected to an older story. Example: \"Suspect in Trump assassination attempt arrested\" — even though the "
        "attempt was days ago, the arrest is happening NOW and is breaking news. This IS hero-worthy.\n"
        "\n"
        "Look at the teaser to determine which case applies:\n"
        "- Phrases like \"announced today\", \"arrested today\", \"ruled today\", \"said this morning\", \"hours ago\", "
        "\"just\", \"breaking\" — these signal a NEW development happening now, even in an old story. TREAT AS FRESH.\n"
        "- Phrases that recap an old event with no new action today — \"yesterday's\", \"earlier this week\", \"last "
        "Monday's\", and the article is just summarizing what already happened — TREAT AS STALE.\n"
        "- When in doubt, ask: \"Is something new happening today in this story, or is this just a recap of an old event?\"\n"
        "\n"
        "Freshness tiers (combining timestamp AND content signals):\n"
        "- New event or new development from the past few hours: strong hero candidate\n"
        "- New development today in an older ongoing story: also strong (arrests, rulings, statements, decisions)\n"
        "- From 6-12 hours ago and still actively unfolding: still acceptable\n"
        "- Pure recap of an event from yesterday or earlier with no new development: AVOID as hero — it belongs as a card if at all\n"
        "\n"
        "When comparing, ask: \"Is something NEW happening in this story right now, or did it already happen and move on?\" "
        "If something new is happening, it's fresh regardless of when the original event occurred. If it has moved on with "
        "no new development, pick a fresher story.\n"
        "\n"
        "Pick the story with the GREATEST systemic impact for the US audience among CURRENT stories — not the biggest story regardless of when it happened.\n"
        "\n"
        "STRONG front-page heroes (in rough priority order, all assuming the story is fresh):\n"
        "1. Breaking court rulings or legal decisions involving the President, Congress, or major US institutions — these are the kind of stories that make every major outlet's front page\n"
        "2. Major US national policy/political developments affecting millions (Supreme Court rulings, major legislation passed/signed, executive actions with broad immediate impact)\n"
        "3. US national security crises, attacks on US soil, US military action\n"
        "4. Major economic events affecting US consumers/markets (Fed decisions, market crashes, major industry collapses, jobs reports)\n"
        "5. Major geopolitical events involving US allies or US interests (attacks on NATO countries, peace deals, sanctions, treaties) — these affect US foreign policy even when they happen abroad\n"
        "6. Major US infrastructure/space/scientific events with national consequence (NASA missions, major tech failures with national impact)\n"
        "\n"
        "MEDIUM heroes — okay if nothing stronger is fresh, but typically belong as cards if a strong-tier story exists:\n"
        "- Routine regulatory proposals (SEC rule changes, FCC proposals, agency-level rulemaking) — these matter to specific industries and policy wonks but rarely lead general-audience newspapers\n"
        "- Single-state political news without broader implications\n"
        "- Minor policy clarifications or technical legal rulings\n"
        "\n"
        "Quick gut check: if a story would NOT be on the front page of CNN, NYT, WaPo, or AP right now, it probably shouldn't be your hero either. Court rulings about Trump, breaking legal decisions, major political fights, and significant policy shifts make general-audience front pages. Niche regulatory proposals usually don't.\n"
        "\n"
        "WEAK front-page heroes (these belong as cards, NOT as the lead):\n"
        "- Smaller-scale localized US tragedies — single bus/car crashes, single-building fires, industrial accidents, factory explosions, mine collapses, single-aircraft small plane crashes, local crime. These affect their region/industry, not the nation broadly.\n"
        "- Foreign tragedies without US connection (foreign domestic crime, regional conflicts not involving US allies/interests)\n"
        "- Single-company news (funding rounds, IPOs, executive shakeups, single-quarter earnings)\n"
        "- Executive opinions or statements about competitors \u2014 'CEO says X is too expensive', 'executive calls Y overrated' are cards, not heroes\n"
        "- Industry commentary, analyst takes, or opinion pieces without a concrete policy or market-moving event\n"
        "- Sports/entertainment — these are ALWAYS cards, never heroes, regardless of historic significance. A Grand Slam final, championship game, or record-breaking performance belongs in the Sports section as a card.\n"
        "- Celebrity deaths unless of major historical/cultural figures\n"
        "- Routine policy proposals without immediate broad impact\n"
        "\n"
        "Casualty/disaster events that DO warrant hero status are large in scale or national in reach:\n"
        "- Major commercial airline crashes (large passenger jets, dozens+ killed)\n"
        "- Mass shootings or terrorism at historic scale\n"
        "- Multi-state natural disasters (major hurricanes, large wildfires across regions, earthquakes affecting populated areas)\n"
        "- Bridge collapses or major infrastructure failures with national implications\n"
        "- Attacks on US military or significant US security events\n"
        "- Industrial disasters with broad consequences (e.g. nuclear incidents, chemical releases affecting large populations)\n"
        "\n"
        "Rule of thumb: if a tragedy is contained to one workplace, one road, one building, or one small town, it is a card, not a hero — even with multiple casualties. If it affects multiple states, a major industry, or hundreds of lives, it can be hero-worthy.\n"
        "\n"
        "Examples:\n"
        "- 'Trump delays Iran ceasefire' BEATS 'Nine dead in Oregon paper mill disaster' — the ceasefire decision is active US foreign policy reshaping a major conflict; the mill disaster, however tragic, is a regional industrial accident with no national policy consequence.\n"
        "- 'Russian drone strikes NATO ally Romania' BEATS 'Bus crash kills 5 in Virginia' — the drone strike has NATO/Article 5 implications affecting US foreign policy; the bus crash, however tragic, is regional.\n"
        "- 'Supreme Court rules on major case' BEATS 'Tech company raises $10B' — court rulings reshape US law for millions.\n"
        "- 'US bus crash kills 5' BEATS 'European train delay' — between two local stories, US readers care more about US events.\n"
        "\n"
        "IMPORTANT ON CASUALTY COUNT: A high death toll does NOT by itself make a story front-page-worthy. The question is REACH — how many people BEYOND those directly involved are affected, and whether anything changes nationally. A chemical spill that kills 11 workers INSIDE one mill is a contained workplace accident — its reach is that facility and that town, NOT the nation. That is a card. The 'chemical release affecting large populations' exception means events like a city-wide toxic cloud forcing mass evacuation — NOT a fatal accident confined to one workplace. Do not let the word 'chemical' or a double-digit death count override the reach test.\n"
        "\n"
        "THINK FIRST, THEN ANSWER. For each candidate, briefly assess in one short line: (a) how recent the actual event is, and (b) its national reach — does it change US policy/markets/security, or affect Americans beyond those directly involved? Then state your pick.\n"
        "\n"
        "Format your response EXACTLY like this:\n"
        "Reasoning: <one line per candidate, very brief>\n"
        "PICK: <number>\n"
    )
    try:
        resp = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = extract_model_text(resp)
        import re as _re
        # Prefer the explicit PICK: line; fall back to last number in the text
        pick_match = _re.search(r"PICK:\s*(\d+)", raw, _re.IGNORECASE)
        if pick_match:
            idx = int(pick_match.group(1)) - 1
        else:
            nums = _re.findall(r"\d+", raw)
            idx = int(nums[-1]) - 1 if nums else -1
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
        resp = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = extract_model_text(resp)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        dupes = set(int(x) for x in parse_first_json_value(raw, expected_type=list))
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
        "5b. Foreign economic indicators (China factory/PMI data, foreign GDP, foreign central bank moves): LOWER for a US audience unless they trigger an immediate, named US market reaction. 'China factory activity contracts' is a routine indicator that interests economists but should NOT be a top card on a US front page — rank it well below US domestic news, US policy, and major US-relevant world events.\n"
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
        resp = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = extract_model_text(resp)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        indices = parse_first_json_value(raw, expected_type=list)
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
    SECTION_LABELS = {
        "world":         "World News",
        "us":            "U.S. News",
        "politics":      "Politics News",
        "business":      "Business News",
        "tech":          "Tech & Science News",
        "sports":        "Sports News",
        "entertainment": "Entertainment News",
    }

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
        # Look up actual archived slug so share URL matches the real article page
        _archive   = load_archive(OUTPUT_DIR / "archive.json")
        _matched = find_matching_entry(hero.get("headline",""), _archive, hero.get("link",""), hero.get("story_id",""))
        if hero.get("_archived_slug"):
            slug = hero["_archived_slug"]
        elif _matched:
            slug = _matched["slug"]
        else:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            slug = f"{today}-{slugify(hero.get('headline', ''))}"
        article_url = f"{SITE_URL}/articles/{slug}.html"
        # SEO section label for non-Top-News categories
        section_label = ""
        if cat_key in SECTION_LABELS:
            section_label = f'<div class="topic-section-label"><h2 class="county-label-text">{SECTION_LABELS[cat_key]}</h2></div>'
        return f"""
    <section class="hero{fade}" data-cat-hero="{cat_key}"{display}>
      {section_label}
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
          <div class="article-actions">
            <button class="share-btn" data-headline="{hero["headline"].replace('"', "&quot;")}" data-url="{article_url}" onclick="shareArticle(this)">Share &#8599;</button>
            <button class="collapse-btn" onclick="collapseThis(this)">Close &uarr;</button>
          </div>
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
      <a href="/news.html" class="wordmark">plain</a>
      <nav class="category-nav">
        <button class="cat-btn active" data-cat="all">Top News</button>
        <button class="cat-btn" data-cat="world">World</button>
        <button class="cat-btn" data-cat="us">U.S.</button>
        <button class="cat-btn" data-cat="politics">Politics</button>
        <button class="cat-btn" data-cat="business">Business</button>
        <button class="cat-btn" data-cat="tech">Tech & Science</button>
        <button class="cat-btn" data-cat="sports">Sports</button>
        <button class="cat-btn" data-cat="entertainment">Entertainment</button>
        <a href="/archive.html" class="cat-btn" style="text-decoration:none">Archive</a>
      </nav>
      <div class="header-actions">
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">&#9790;</button>
        <a href="https://apps.apple.com/us/app/plain-news-without-the-noise/id6774256916" target="_blank" class="appstore-btn" aria-label="Download on the App Store"><img src="https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg" alt="Download on the App Store" style="height:28px;display:block;"></a>
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

def slugify(text):
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def load_archive(archive_path):
    try:
        if archive_path.exists():
            return json.loads(archive_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def render_article_page(hero, category_label, category_key, pub_date, slug):
    """Render a permanent article page for a single Plain story."""
    description = (hero.get("teaser") or hero.get("body", "")[:155]).replace('"', '')
    image_url   = hero.get("image_url") or f"{SITE_URL}/social-card.png"
    structured_data = {
        "@context": "https://schema.org",
        "@type":    "NewsArticle",
        "headline": hero.get("headline", ""),
        "description": description,
        "image":    image_url,
        "datePublished": pub_date,
        "author":    {"@type": "Organization", "name": SITE_NAME},
        "publisher": {
            "@type": "Organization",
            "name":  SITE_NAME,
            "logo":  {"@type": "ImageObject", "url": f"{SITE_URL}/favicon.svg"},
        },
        "mainEntityOfPage": f"{SITE_URL}/articles/{slug}.html",
    }
    import json as _json
    schema_tag = f'  <script type="application/ld+json">{_json.dumps(structured_data)}</script>'
    body       = make_paragraphs(hero.get("body", ""))
    img_html   = ""
    if hero.get("image_url"):
        credit   = f'<figcaption class="img-credit">Photo: {hero["image_credit"]}</figcaption>' if hero.get("image_credit") else ""
        img_html = f'<figure class="article-hero-image"><img src="{hero["image_url"]}" alt="{hero["headline"]}" loading="eager">{credit}</figure>'

    # Nav for article pages — all absolute URLs since page lives in /articles/
    nav_links = " ".join([
        f'<button class="cat-btn" data-cat="{k}" onclick="window.location=\'{SITE_URL}/news.html?cat={k}\'">{l}</button>'
        for k, l in [("all","Top News"),("world","World"),("us","U.S."),
                     ("politics","Politics"),("business","Business"),
                     ("tech","Tech & Science"),("sports","Sports"),("entertainment","Entertainment")]
    ] + [f'<a href="{SITE_URL}/archive.html" class="cat-btn" style="text-decoration:none">Archive</a>'])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{hero["headline"]} — Plain</title>
  <meta name="description" content="{description}">
  <link rel="canonical" href="{SITE_URL}/articles/{slug}.html">
  <meta property="og:title" content="{hero["headline"]}">
  <meta property="og:description" content="{description}">
  <meta property="og:url" content="{SITE_URL}/articles/{slug}.html">
  <meta property="og:image" content="{image_url}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:image" content="{image_url}">
  <link rel="icon" href="/favicon.ico">
  <link rel="stylesheet" href="/style.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,500;1,9..144,300&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap" rel="stylesheet">
{schema_tag}
  <style>
    .article-wrap {{ max-width: 740px; margin: 0 auto; padding: 40px 24px 80px; }}
    .article-meta {{ display: flex; align-items: center; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
    .article-category {{ font-size: 10px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: var(--accent); }}
    .article-date {{ font-size: 11px; color: var(--text-muted); }}
    .article-headline {{ font-family: "Fraunces", serif; font-size: clamp(26px, 4vw, 42px); font-weight: 600; line-height: 1.15; letter-spacing: -.02em; color: var(--text); margin-bottom: 24px; }}
    .article-hero-image {{ margin: 0 0 28px; }}
    .article-hero-image img {{ width: 100%; max-height: 420px; object-fit: cover; border-radius: 8px; display: block; }}
    .article-body p {{ font-size: 17px; line-height: 1.8; color: var(--text-secondary); margin-bottom: 20px; }}
    .article-back {{ display: inline-block; font-size: 13px; color: var(--accent); text-decoration: none; margin-bottom: 32px; font-weight: 500; }}
    .article-back:hover {{ opacity: .7; }}
    .article-divider {{ border: none; border-top: 1px solid var(--border); margin: 40px 0; }}
    .article-more {{ font-family: "Fraunces", serif; font-size: 20px; font-weight: 500; color: var(--text); margin-bottom: 16px; }}
    .article-more-link {{ display: inline-block; color: var(--accent); font-size: 14px; font-weight: 500; text-decoration: none; }}
    .article-more-link:hover {{ opacity: .7; }}
  </style>
</head>
<body>
  <header class="site-header">
    <div class="header-inner">
      <a href="{SITE_URL}/news.html" class="wordmark">plain</a>
      <nav class="category-nav">{nav_links}</nav>
    </div>
  </header>
  <main>
    <div class="article-wrap">
      <a href="{SITE_URL}/news.html" class="article-back">&larr; Back to Plain</a>
      <div class="article-meta">
        <span class="article-category">{category_label}</span>
        <span class="article-date">{pub_date}</span>
      </div>
      <h1 class="article-headline">{hero["headline"]}</h1>
      {img_html}
      <div class="article-body">{body}</div>
      <hr class="article-divider">
      <p class="article-more">More news</p>
      <a href="{SITE_URL}/news.html?cat={category_key}" class="article-more-link">More {category_label} &rarr;</a>
    </div>
  </main>
  <footer>
    <div class="footer-inner">
      <span class="footer-wordmark">plain</span>
      <div class="footer-links">
        <a href="{SITE_URL}/archive.html">Archive</a>
        <a href="{SITE_URL}/privacy.html">Privacy</a>
        <a href="{SITE_URL}/terms.html">Terms</a>
      </div>
    </div>
  </footer>
  <script src="/main.js"></script>
</body>
</html>"""


def render_archive_page(archive_entries):
    by_month = defaultdict(list)
    for e in sorted(archive_entries, key=lambda x: x.get("date",""), reverse=True):
        try:
            month = e["date"][:7]
            label = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
        except Exception:
            label = "Recent"; month = "recent"
        by_month[(month, label)].append(e)

    months_html = ""
    for (month_key, month_label), entries in sorted(by_month.items(), reverse=True):
        items = ""
        for e in entries:
            items += f"""
        <li class="archive-item">
          <a href="/articles/{e['slug']}.html" class="archive-link">
            <span class="archive-cat">{e['category_label']}</span>
            <span class="archive-headline">{e['headline']}</span>
            <span class="archive-date">{e['date']}</span>
          </a>
        </li>"""
        months_html += f"""
      <div class="archive-month">
        <h2 class="archive-month-label">{month_label}</h2>
        <ul class="archive-list">{items}
        </ul>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Archive — Plain</title>
  <meta name="description" content="Every story published on Plain, organized by month.">
  <link rel="canonical" href="{SITE_URL}/archive.html">
  <link rel="icon" href="/favicon.ico">
  <link rel="stylesheet" href="/style.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,500;1,9..144,300&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap" rel="stylesheet">
  <style>
    .archive-wrap {{ max-width: 860px; margin: 0 auto; padding: 40px 24px 80px; }}
    .archive-eyebrow {{ font-size: 11px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); display: block; margin-bottom: 14px; }}
    .archive-headline {{ font-family: "Fraunces", serif; font-size: clamp(28px,4vw,40px); font-weight: 600; color: var(--text); margin-bottom: 8px; letter-spacing: -.02em; }}
    .archive-sub {{ font-size: 15px; color: var(--text-secondary); margin-bottom: 48px; }}
    .archive-month {{ margin-bottom: 40px; }}
    .archive-month-label {{ font-family: "Fraunces", serif; font-size: 20px; font-weight: 500; color: var(--text); margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }}
    .archive-list {{ list-style: none; }}
    .archive-item {{ border-bottom: 1px solid var(--border); }}
    .archive-link {{ display: flex; align-items: baseline; gap: 10px; padding: 12px 0; text-decoration: none; color: inherit; flex-wrap: wrap; }}
    .archive-link:hover .archive-headline {{ color: var(--accent); }}
    .archive-cat {{ font-size: 10px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: var(--accent); flex-shrink: 0; }}
    .archive-headline {{ font-size: 15px; font-weight: 500; color: var(--text); flex: 1; line-height: 1.4; }}
    .archive-date {{ font-size: 11px; color: var(--text-muted); flex-shrink: 0; margin-left: auto; }}
    @media(max-width:580px) {{ .archive-date {{ display: none; }} }}
  </style>
</head>
<body>
  <header class="site-header">
    <div class="header-inner">
      <a href="{SITE_URL}/news.html" class="wordmark">plain</a>
    </div>
  </header>
  <main>
    <div class="archive-wrap">
      <span class="archive-eyebrow">Archive</span>
      <h1 class="archive-headline">All Articles</h1>
      <p class="archive-sub">Every story published on Plain, organized by month.</p>
      {months_html}
    </div>
  </main>
  <footer>
    <div class="footer-inner">
      <span class="footer-wordmark">plain</span>
      <div class="footer-links">
        <a href="{SITE_URL}/privacy.html">Privacy</a>
        <a href="{SITE_URL}/terms.html">Terms</a>
      </div>
    </div>
  </footer>
  <script src="/main.js"></script>
</body>
</html>"""


def update_sitemap(archive_entries):
    now_str = datetime.utcnow().strftime("%Y-%m-%d")
    static = f"""  <url>
    <loc>{SITE_URL}/</loc>
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
    <lastmod>{now_str}</lastmod>
  </url>
  <url>
    <loc>{SITE_URL}/archive.html</loc>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
    <lastmod>{now_str}</lastmod>
  </url>
  <url>
    <loc>{SITE_URL}/privacy.html</loc>
    <priority>0.3</priority>
  </url>
  <url>
    <loc>{SITE_URL}/terms.html</loc>
    <priority>0.3</priority>
  </url>"""
    article_urls = "".join(f"""
  <url>
    <loc>{SITE_URL}/articles/{e['slug']}.html</loc>
    <priority>0.7</priority>
    <lastmod>{e.get('lastmod') or e['date']}</lastmod>
  </url>""" for e in archive_entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{static}
{article_urls}
</urlset>"""


def update_news_sitemap(archive_entries):
    """Google News sitemap — only articles from last 2 days."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")
    recent = [e for e in archive_entries if e.get("date","") >= cutoff]
    news_urls = ""
    for e in recent:
        pub_date = f"{e['date']}T00:00:00Z"
        headline = e['headline'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')
        news_urls += f"""
  <url>
    <loc>{SITE_URL}/articles/{e['slug']}.html</loc>
    <news:news>
      <news:publication>
        <news:name>Plain</news:name>
        <news:language>en</news:language>
      </news:publication>
      <news:publication_date>{pub_date}</news:publication_date>
      <news:title>{headline}</news:title>
    </news:news>
  </url>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
{news_urls}
</urlset>"""


def _archive_article_body(row):
    body = str(row.get("body") or "").strip()
    if body:
        return body
    slug = str(row.get("slug") or "").strip()
    path = OUTPUT_DIR / "articles" / f"{slug}.html"
    if not slug or not path.exists():
        return ""
    try:
        import html as _html
        text = path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'<div class="article-body">(.*?)</div>', text, flags=re.I | re.S)
        if not m:
            return ""
        paras = []
        for raw in re.findall(r"<p[^>]*>(.*?)</p>", m.group(1), flags=re.I | re.S):
            clean = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))).strip()
            if clean:
                paras.append(clean)
        return "\n\n".join(paras)
    except Exception:
        return ""


def _canonical_for_gate(row):
    out = dict(row)
    out["body"] = _archive_article_body(row)
    out.setdefault("source_headline", row.get("headline", ""))
    out.setdefault("first_published", row.get("date", ""))
    out.setdefault("published_at", row.get("lastmod", row.get("date", "")))
    return out


def _incoming_for_gate(hero):
    return {
        "headline": hero.get("headline", ""), "source_headline": hero.get("source_title", ""),
        "teaser": hero.get("teaser", ""),
        "body": hero.get("article_text") or hero.get("source_summary") or hero.get("body", ""),
        "source_url": hero.get("link", ""), "published_at": hero.get("source_published_raw", ""),
        "story_id": hero.get("story_id", ""), "event_key": hero.get("event_key", ""),
    }


def _source_incoming_for_gate(source):
    """Build the semantic-gate payload from recovered publisher evidence only."""
    return {
        "headline": source.get("title", ""),
        "source_headline": source.get("title", ""),
        "teaser": source.get("summary", ""),
        "body": source.get("article_text") or source.get("summary", ""),
        "source_url": source.get("link", ""),
        "published_at": source.get("published", ""),
        "story_id": source.get("story_id", ""),
        "event_key": source.get("event_key", ""),
    }


def _canonical_context_from_archive(row):
    if not isinstance(row, dict):
        return {}
    return {
        "slug": str(row.get("slug") or ""),
        "headline": str(row.get("headline") or ""),
        "body": _archive_article_body(row),
        "teaser": str(row.get("teaser") or ""),
        "date": str(row.get("date") or row.get("first_published") or ""),
        "source_url": str(row.get("source_url") or ""),
        "story_id": str(row.get("story_id") or ""),
        "event_key": str(row.get("event_key") or ""),
    }


def _pre_generation_semantic_decision(source, archive, canonicals, client, cache):
    """TCT-style late-source materiality check moved ahead of generation.

    Only a bounded, high-similarity recent candidate set reaches the model.  The
    result is cached and does not itself publish anything.
    """
    incoming = _source_incoming_for_gate(source)
    candidates = retrieve_recent_candidates(incoming, canonicals, window_days=7, max_candidates=4)

    # Exact pre-migration archive identity remains an admissible candidate even if
    # the newer semantic retriever is conservative about an angle-shifted headline.
    ctx = source.get("canonical_context") or {}
    ctx_slug = str(ctx.get("slug") or "").strip() if isinstance(ctx, dict) else ""
    if ctx_slug and all(str(c.get("slug") or "") != ctx_slug for c in candidates):
        match = next((row for row in canonicals if str(row.get("slug") or "") == ctx_slug), None)
        if match:
            candidates = [{"slug": ctx_slug, "headline": match.get("headline", ""), "article": match, "evidence": {"eligible": True, "retrieval_score": 1.0, "reasons": ["exact_archive_identity"]}}, *candidates]
            candidates = candidates[:4]

    if not candidates:
        return None

    key = cache_hash({
        "v": "plain-pre-generation-materiality-v2",
        "incoming": incoming,
        "candidates": [
            {
                "slug": c.get("slug"),
                "headline": c.get("headline"),
                "lastmod": (c.get("article") or {}).get("lastmod"),
                "source_url": (c.get("article") or {}).get("source_url"),
            }
            for c in candidates
        ],
    })
    cached = cache.get("pre_generation_materiality", key)
    if cached is not CACHE_MISS:
        decision = dict(cached or {})
        decision["cache_hit"] = True
        return decision

    decision = adjudicate_candidates(
        client,
        model="claude-sonnet-5",
        incoming=incoming,
        candidates=candidates,
        timeout_seconds=45,
    )
    if decision.get("action") == ACTION_HOLD:
        broad = retrieve_recent_candidates(incoming, canonicals, window_days=14, max_candidates=10)
        if broad:
            decision = adjudicate_candidates(
                client,
                model="claude-sonnet-5",
                incoming=incoming,
                candidates=broad,
                timeout_seconds=60,
            )
            decision["high_recall_resolution_pass"] = True
    cache.put("pre_generation_materiality", key, decision, ttl_seconds=86400)
    return decision


def apply_pre_generation_materiality(source_sets, archive, client, cache):
    """Suppress known no-change reprints and protect true updates before writing.

    A validated material update receives target-bound canonical context that is
    carried through the assignment editor, exact-source writer and final commit
    queue.  This is the Plain adaptation of TCT's late material-update placement
    and no-silent-loss authority.
    """
    canonicals = [_canonical_for_gate(row) for row in archive]
    by_slug = {str(row.get("slug") or ""): row for row in archive if row.get("slug")}
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "evaluated": 0,
        "material_updates": 0,
        "duplicates_suppressed": 0,
        "holds_suppressed": 0,
        "new_or_unmatched": 0,
        "items": [],
    }

    for cat_key, rows in list(source_sets.items()):
        kept = []
        for source in rows:
            decision = _pre_generation_semantic_decision(source, archive, canonicals, client, cache)
            if not decision:
                kept.append(source)
                continue
            report["evaluated"] += 1
            action = str(decision.get("action") or "")
            selected = str(decision.get("selected_candidate_slug") or "").strip()
            row = {
                "category": cat_key,
                "title": source.get("title", ""),
                "source_url": source.get("link", ""),
                "action": action,
                "selected_candidate_slug": selected,
                "confidence": decision.get("confidence"),
                "novel_facts": list(decision.get("novel_facts") or []),
                "reason": decision.get("reason", ""),
                "cache_hit": bool(decision.get("cache_hit")),
            }
            report["items"].append(row)

            if action == ACTION_UPDATE and selected:
                canonical = by_slug.get(selected)
                if not canonical:
                    # Never allow an update authority without a concrete canonical
                    # transaction target.  Keep it as an ordinary source so the
                    # terminal permalink gate can adjudicate later.
                    kept.append(source)
                    row["authority_error"] = "selected canonical slug missing from archive"
                    continue
                source["pre_generation_material_update"] = True
                source["pre_generation_material_update_canonical_slug"] = selected
                source["semantic_material_update_decision"] = copy.deepcopy(decision)
                source["canonical_context"] = _canonical_context_from_archive(canonical)
                source["material_update_novel_facts"] = list(decision.get("novel_facts") or [])
                source["material_update_confidence"] = float(decision.get("confidence") or 0.0)
                kept.append(source)
                report["material_updates"] += 1
                continue

            if action == ACTION_DUPLICATE:
                source["pre_generation_duplicate"] = True
                report["duplicates_suppressed"] += 1
                continue

            if action == ACTION_HOLD:
                source["pre_generation_hold"] = True
                report["holds_suppressed"] += 1
                continue

            kept.append(source)
            report["new_or_unmatched"] += 1
        source_sets[cat_key] = kept

    (OUTPUT_DIR / "pre-generation-material-update-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _semantic_publication_decision(hero, archive, client, cache):
    incoming = _incoming_for_gate(hero)
    canonicals = [_canonical_for_gate(row) for row in archive]
    candidates = retrieve_recent_candidates(incoming, canonicals, window_days=7, max_candidates=4)
    if not candidates:
        return {"action": ACTION_NEW, "selected_candidate_slug": "", "status": "no_candidates", "confidence": 1.0}
    key = cache_hash({"v":"plain-terminal-gate-v3","incoming":incoming,"candidates":[(x.get("slug"),x.get("headline"),x.get("lastmod")) for x in candidates]})
    cached = cache.get("semantic_gate", key)
    if cached is not CACHE_MISS:
        return dict(cached or {})
    decision = adjudicate_candidates(client, model="claude-sonnet-5", incoming=incoming, candidates=candidates, timeout_seconds=45)
    if decision.get("action") == ACTION_HOLD:
        broad = retrieve_recent_candidates(incoming, canonicals, window_days=14, max_candidates=12)
        decision = adjudicate_candidates(client, model="claude-sonnet-5", incoming=incoming, candidates=broad, timeout_seconds=60)
        decision["terminal_high_recall_pass"] = True
    cache.put("semantic_gate", key, decision, ttl_seconds=86400)
    return decision


def _validated_material_update_target(item):
    if not isinstance(item, dict) or not item.get("pre_generation_material_update"):
        return ""
    decision = item.get("semantic_material_update_decision") or {}
    slug = str(item.get("pre_generation_material_update_canonical_slug") or "").strip()
    return slug if (slug and decision.get("action") == ACTION_UPDATE and decision.get("same_real_world_event") is True and decision.get("material_new_update") is True) else ""


def selected_material_update_commit_entries(all_categories):
    """Return hidden canonical-write receipts for final surviving live placements.

    Plain cards are snippets rather than permalink pages, but a validated update
    selected into a card still creates a newsroom obligation: its established
    canonical article must be refreshed.  This queue preserves that obligation
    without changing Plain's visible card product.
    """
    best = {}
    for category in all_categories:
        cat_key = str(category.get("category_key") or "")
        cat_label = str(category.get("category_label") or cat_key.replace("_", " ").title())
        placements = [("hero", category.get("hero"))] + [("card", c) for c in category.get("cards", [])]
        placements += [("hidden_repair", c) for c in category.get("protected_material_update_commits", []) if isinstance(c, dict)]
        for surface, item in placements:
            if not isinstance(item, dict):
                continue
            slug = _validated_material_update_target(item)
            if not slug:
                continue
            if (item.get("publication_quality_ok") is False and not protected_material_update_pending_recomposition(item)) or item.get("editorial_eligible") is False:
                raise RuntimeError(
                    f"Validated material update survived selection but is not publishable or repairable: {slug} / {item.get('headline','')} / {item.get('publication_quality_reasons',[])}"
                )
            receipt = copy.deepcopy(item)
            receipt["_material_update_commit_only"] = surface != "hero"
            if surface == "hidden_repair":
                receipt["_material_update_commit_only"] = True
            receipt["_material_update_selection_surface"] = f"{cat_key}:{surface}"
            receipt["category_key"] = cat_key
            receipt["category_label"] = cat_label
            rank = (
                float(receipt.get("material_update_confidence") or 0.0),
                int(surface == "hero"),
                len(str(receipt.get("article_text") or receipt.get("body") or "").split()),
            )
            current = best.get(slug)
            if current is None or rank > current[0]:
                best[slug] = (rank, (cat_key, cat_label, receipt))
    return [best[slug][1] for slug in sorted(best)]


def _write_publication_integrity_report(archive, *, selected_update_entries=(), committed_update_slugs=(), output_path=None):
    """TCT-style forward publication health report without rewriting legacy pages."""
    output_path = Path(output_path or (OUTPUT_DIR / "publication-integrity-report.json"))
    story_groups = defaultdict(list)
    source_groups = defaultdict(list)
    missing_pages = []
    for row in archive:
        slug = str(row.get("slug") or "").strip()
        sid = str(row.get("story_id") or "").strip()
        src = re.sub(r"[?#].*$", "", str(row.get("source_url") or "").strip().rstrip("/").casefold())
        if sid:
            story_groups[sid].append(slug)
        if src:
            source_groups[src].append(slug)
        if slug and not (OUTPUT_DIR / "articles" / f"{slug}.html").exists():
            missing_pages.append(slug)
    duplicate_story_ids = {k: v for k, v in story_groups.items() if len(set(v)) > 1}
    duplicate_source_urls = {k: v for k, v in source_groups.items() if len(set(v)) > 1}
    selected = sorted({_validated_material_update_target(entry[2]) for entry in selected_update_entries if _validated_material_update_target(entry[2])})
    committed = sorted({str(x) for x in committed_update_slugs if str(x)})
    uncommitted = sorted(set(selected) - set(committed))
    report = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "archive_records": len(archive),
        "duplicate_story_id_groups": duplicate_story_ids,
        "duplicate_source_url_groups": duplicate_source_urls,
        "missing_article_pages": missing_pages,
        "selected_material_update_targets": selected,
        "committed_material_update_targets": committed,
        "uncommitted_material_update_targets": uncommitted,
        "legacy_duplicate_note": "Existing historical duplicate groups are reported, not destructively rewritten by this migration. Forward publication is guarded at the permalink barrier.",
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if uncommitted:
        raise RuntimeError(f"Validated material update(s) were selected but not committed: {uncommitted}")
    return report


def write_archives(all_categories, top_cat, *, client=None, cache=None, material_update_entries=None):
    """Canonical publication barrier: duplicate/update/new is decided before URLs write."""
    articles_dir = OUTPUT_DIR / "articles"; archive_path = OUTPUT_DIR / "archive.json"; articles_dir.mkdir(exist_ok=True)
    client = client or get_client(); cache = cache or get_generation_cache(); archive = load_archive(archive_path)
    working = [dict(x) for x in archive]; today = datetime.utcnow().strftime("%Y-%m-%d"); now_iso = datetime.utcnow().replace(microsecond=0).isoformat()+"Z"
    staged=[]; new_count=updated_count=unchanged_count=0
    committed_update_slugs=set()
    material_update_entries=list(material_update_entries or selected_material_update_commit_entries(all_categories))
    heroes=[(top_cat["category_key"],top_cat["category_label"],top_cat["hero"])] + [(c["category_key"],c["category_label"],c["hero"]) for c in all_categories if c["category_key"]!=top_cat["category_key"]]
    publication_entries = heroes + material_update_entries
    for cat_key,cat_label,hero in publication_entries:
        headline=str(hero.get("headline") or "").strip()
        if not headline: continue
        source_url=str(hero.get("link") or ""); target_slug=_validated_material_update_target(hero); sem=None
        existing=next((x for x in working if str(x.get("slug") or "")==target_slug),None) if target_slug else find_matching_entry(headline,working,source_url,hero.get("story_id",""))
        if target_slug and not existing:
            raise RuntimeError(f"Validated material update lost its canonical target before publication: {target_slug}")
        if not existing:
            sem=_semantic_publication_decision(hero,working,client,cache); action=sem.get("action"); selected=str(sem.get("selected_candidate_slug") or "")
            if action in {ACTION_DUPLICATE,ACTION_UPDATE} and selected:
                existing=next((x for x in working if x.get("slug")==selected),None)
            elif action==ACTION_HOLD:
                raise RuntimeError(f"Terminal publication gate held suspicious story; preserving previous site: {headline}")
            elif action!=ACTION_NEW:
                raise RuntimeError(f"Invalid terminal publication decision: {sem}")
        if existing:
            slug=existing["slug"];hero["_archived_slug"]=slug
            editorial_action=str(hero.get("editorial_action") or "").casefold(); should_update=bool(target_slug) or editorial_action in {"update_existing","replace_canonical"} or (sem or {}).get("action")==ACTION_UPDATE
            if not should_update:
                canonical_body=_archive_article_body(existing)
                if canonical_body:
                    hero["headline"]=existing.get("headline",headline);hero["body"]=canonical_body;hero["teaser"]=existing.get("teaser",hero.get("teaser",""));hero["image_url"]=existing.get("source_image_url") or existing.get("image_url") or hero.get("image_url","");hero["image_credit"]=existing.get("image_credit",hero.get("image_credit",""))
                existing["last_seen"]=now_iso;existing["last_seen_source_url"]=source_url
                if hero.get("story_id"):existing["story_id"]=hero["story_id"]
                if hero.get("event_key"):existing["event_key"]=hero["event_key"]
                existing["editorial_action"]="ignore";unchanged_count+=1;continue
            if target_slug and target_slug in committed_update_slugs:
                continue
            canonical=_canonical_for_gate(existing);incoming={"headline":hero.get("headline",""),"source_headline":hero.get("source_title",""),"teaser":hero.get("teaser",""),"body":hero.get("article_text") or hero.get("source_summary") or hero.get("body",""),"source_url":source_url,"published_at":hero.get("source_published_raw","")};decision=sem or hero.get("semantic_material_update_decision") or {"shared_anchors":[],"novel_facts":[],"reason":"Persistent editorial engine classified a material update."}
            composed=compose_material_update(client,model="claude-sonnet-4-5",canonical=canonical,incoming=incoming,decision=decision,timeout_seconds=60)
            if composed.get("status")!="validated":raise RuntimeError(f"Material update composition failed for {slug}: {composed.get('validation_errors')}")
            composed_item=dict(hero);composed_item["headline"]=composed["headline"];composed_item["teaser"]=composed["teaser"];composed_item["body"]=composed["body"]
            final_ok,final_reasons=publication_quality(composed_item,hero=True)
            if not final_ok:
                raise RuntimeError(f"Material update canonical recomposition failed final publication-quality contract for {slug}: {final_reasons}")
            hero["headline"]=composed["headline"];hero["teaser"]=composed["teaser"];hero["body"]=composed["body"]
            hero["publication_quality_ok"]=True;hero["publication_quality_reasons"]=[];hero.pop("_force_material_update_recomposition",None);hero["material_update_recomposition_completed"]=True
            staged.append((articles_dir/f"{slug}.html",render_article_page(hero,cat_label,cat_key,existing.get("date",today),slug)))
            existing.update({"headline":hero["headline"],"teaser":hero["teaser"],"body":hero["body"],"category_key":cat_key,"category_label":cat_label,"lastmod":today,"last_seen":now_iso,"source_url":source_url,"source_headline":hero.get("source_title",""),"source_published_raw":hero.get("source_published_raw",""),"image_url":hero.get("image_url",""),"image_credit":hero.get("image_credit",""),"image_origin":hero.get("image_origin",""),"story_id":hero.get("story_id",existing.get("story_id","")),"event_key":hero.get("event_key",existing.get("event_key","")),"editorial_action":"update_existing"})
            if not hero.get("is_fallback_image"):existing["source_image_url"]=hero.get("image_url","")
            if target_slug:
                committed_update_slugs.add(target_slug)
            updated_count+=1
        else:
            existing_slugs={x.get("slug") for x in working};base=f"{today}-{slugify(headline)}";slug=base;i=1
            while slug in existing_slugs:slug=f"{base}-{i}";i+=1
            hero["_archived_slug"]=slug;staged.append((articles_dir/f"{slug}.html",render_article_page(hero,cat_label,cat_key,today,slug)))
            row={"slug":slug,"headline":headline,"teaser":hero.get("teaser","") or hero.get("body","")[:180],"body":hero.get("body",""),"category_key":cat_key,"category_label":cat_label,"date":today,"first_published":now_iso,"lastmod":today,"last_seen":now_iso,"image_url":hero.get("image_url",""),"source_image_url":hero.get("image_url","") if not hero.get("is_fallback_image") else "","image_credit":hero.get("image_credit",""),"image_origin":hero.get("image_origin",""),"source_url":source_url,"source_headline":hero.get("source_title",""),"source_published_raw":hero.get("source_published_raw",""),"story_id":hero.get("story_id",""),"event_key":hero.get("event_key",""),"editorial_action":hero.get("editorial_action","publish_new")};working.append(row);new_count+=1
    # No public files are mutated until every hero and every protected update has
    # passed the terminal barrier and has a concrete commit receipt. This preserves
    # TCT's fail-closed transaction ordering rather than discovering a lost update
    # only after archive/article files have already been changed.
    required_update_slugs = {
        _validated_material_update_target(entry[2])
        for entry in material_update_entries
        if _validated_material_update_target(entry[2])
    }
    uncommitted_before_write = sorted(required_update_slugs - committed_update_slugs)
    if uncommitted_before_write:
        raise RuntimeError(
            f"Validated material update(s) reached publication but have no commit receipt: {uncommitted_before_write}"
        )
    for path,content in staged:path.write_text(content,encoding="utf-8")
    archive_path.write_text(json.dumps(working,ensure_ascii=False,indent=2),encoding="utf-8");(OUTPUT_DIR/"archive.html").write_text(render_archive_page(working),encoding="utf-8");(OUTPUT_DIR/"sitemap.xml").write_text(update_sitemap(working),encoding="utf-8");(OUTPUT_DIR/"news-sitemap.xml").write_text(update_news_sitemap(working),encoding="utf-8")
    _write_publication_integrity_report(working,selected_update_entries=material_update_entries,committed_update_slugs=committed_update_slugs)
    print(f"  Archived {new_count} new, updated {updated_count} existing, left {unchanged_count} unchanged ({len(working)} total); committed {len(committed_update_slugs)} protected material update(s)");return working

def _is_source_stale(row, hours=72):
    raw = str(row.get("published") or "")
    if not raw:
        return False
    try:
        from email.utils import parsedate_to_datetime
        from datetime import timezone as _tz
        dt = parsedate_to_datetime(raw).astimezone(_tz.utc)
    except Exception:
        try:
            from datetime import datetime as _dt, timezone as _tz
            dt = _dt.fromisoformat(raw.replace("Z", "+00:00")).astimezone(_tz.utc)
        except Exception:
            return False
    from datetime import datetime as _dt, timezone as _tz
    return (_dt.now(_tz.utc) - dt).total_seconds() > hours * 3600


def _bind_canonical_context(rows, archive):
    for row in rows:
        existing = find_matching_entry(str(row.get("title") or ""), archive, str(row.get("link") or ""), "")
        if existing:
            row["canonical_context"] = {"slug": existing.get("slug", ""), "headline": existing.get("headline", ""), "body": _archive_article_body(existing), "teaser": existing.get("teaser", ""), "date": existing.get("date", "")}
    return rows


def _source_quality_report(source_sets):
    from collections import Counter
    counts = Counter(); rows = []
    for cat, sources in source_sets.items():
        for s in sources:
            q = str(s.get("source_quality") or "unknown"); counts[q] += 1
            rows.append({"category":cat,"title":s.get("title",""),"source_url":s.get("link",""),"publisher":s.get("publisher_name",""),"quality":q,"word_count":len(str(s.get("article_text") or s.get("summary") or "").split()),"recovery":s.get("source_recovery_status","")})
    (OUTPUT_DIR/"source-quality-report.json").write_text(json.dumps({"generated_at":datetime.utcnow().isoformat()+"Z","counts":dict(counts),"items":rows},ensure_ascii=False,indent=2),encoding="utf-8")


def _category_cache_key(cat_key, sources):
    # Cache identity includes target-bound material-update authority. A source can
    # have identical publisher text while its relationship to Plain's canonical
    # changes as the archive/registry advances; reusing pre-decision generated copy
    # in that case would silently lose the update transaction.
    return cache_hash({
        "v": "plain-live-assignment-v5-section-depth",
        "category": cat_key,
        "editor": "claude-sonnet-5",
        "writer": "claude-sonnet-4-5",
        "sources": [
            {
                "title": s.get("title", ""),
                "url": s.get("link", ""),
                "published": s.get("published", ""),
                "quality": s.get("source_quality", ""),
                "content": hashlib.sha256(str(s.get("article_text") or s.get("summary") or "").encode()).hexdigest()[:16],
                "material_update": bool(s.get("pre_generation_material_update")),
                "material_target": str(s.get("pre_generation_material_update_canonical_slug") or ""),
                "material_novel_facts": list(s.get("material_update_novel_facts") or []),
            }
            for s in sources[:18]
        ],
    })


def _format_generated_times(category):
    for item in [category.get("hero", {}), *category.get("cards", [])]:
        if isinstance(item, dict): item["published"] = format_age(item.get("source_published_raw") or item.get("published", ""))
    return category


def _deterministic_story_dedupe(cards, against_story_id=""):
    seen = {against_story_id} if against_story_id else set(); out=[]
    for card in cards:
        sid=str(card.get("story_id") or "")
        if sid and sid in seen: continue
        if sid: seen.add(sid)
        out.append(card)
    return out


def _archive_sort_value(row):
    raw = str(row.get("lastmod") or row.get("date") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def recover_category_from_archive(cat_key, cat_label, archive, *, card_count=CARDS_PER_CATEGORY, max_age_days=14):
    """Fail closed to already-published canonicals when live generation is unavailable.

    This mirrors TCT's category-level failure containment: one feed/model outage must
    not destroy the entire edition, but recovery is allowed only from Plain's own
    existing permanent archive. No new facts, URLs, or model copy are invented.
    """
    now = datetime.utcnow()
    candidates=[]
    for row in archive:
        if str(row.get("category_key") or "") != cat_key:
            continue
        slug=str(row.get("slug") or "").strip(); headline=str(row.get("headline") or "").strip()
        if not slug or not headline:
            continue
        raw_date=str(row.get("lastmod") or row.get("date") or "").strip()
        try:
            dt=datetime.fromisoformat(raw_date[:10])
            if (now-dt).days > max_age_days:
                continue
        except Exception:
            pass
        body=_archive_article_body(row)
        if len(body.split()) < 80:
            continue
        candidates.append((row,body))
    candidates.sort(key=lambda pair:_archive_sort_value(pair[0]),reverse=True)
    if not candidates:
        return None

    def hydrate(row, body, *, hero=False):
        slug=str(row.get("slug") or "")
        teaser=str(row.get("teaser") or "").strip() or body[:220].rstrip()+"..."
        source_url=str(row.get("source_url") or "").strip()
        item={
            "headline":str(row.get("headline") or ""),
            "teaser":teaser,
            "body":body,
            "article_text":body,
            "source_summary":teaser,
            "source_title":str(row.get("source_headline") or row.get("headline") or ""),
            "source_published_raw":str(row.get("source_published_raw") or row.get("date") or ""),
            "published":format_age(str(row.get("source_published_raw") or row.get("date") or "")),
            "link":source_url or f"{SITE_URL}/articles/{slug}.html",
            "image_url":str(row.get("source_image_url") or row.get("image_url") or ""),
            "image_credit":str(row.get("image_credit") or ""),
            "image_origin":str(row.get("image_origin") or "archive_recovery"),
            "urgency_score":int(row.get("urgency_score") or (5 if hero else 3)),
            "story_id":str(row.get("story_id") or ""),
            "event_key":str(row.get("event_key") or ""),
            "editorial_action":"ignore",
            "editorial_eligible":True,
            "publication_quality_ok":True,
            "source_quality":"canonical_archive",
            "_archive_recovery":True,
            "_archived_slug":slug,
        }
        return item

    hero_row,hero_body=candidates[0]
    cards=[hydrate(row,body) for row,body in candidates[1:1+card_count]]
    return {
        "category_key":cat_key,
        "category_label":cat_label,
        "hero":hydrate(hero_row,hero_body,hero=True),
        "cards":cards,
        "archive_recovery":True,
        "assignment_editor":{"status":"archive_recovery","reason":"Live source/generation path unavailable; reused recent published canonicals."},
    }


def _apply_category_classification(rows, cat_key, classifications, *, limit=18):
    """Preserve section depth while treating the classifier as a safety signal."""
    positive, secondary = [], []
    for row in rows:
        key=(str(row.get("link") or ""),re.sub(r"[^a-z0-9]+"," ",str(row.get("title") or "").casefold()).strip())
        cats=list(classifications.get(key,[]) or [])
        row["classified_categories"]=cats
        if cats == ["none"] or ("none" in cats and len(cats)==1):
            row["category_fit_hint"]="rejected_none"
            continue
        if not cats or cat_key in cats:
            row["category_fit_hint"]="positive" if cat_key in cats else "unclassified"
            positive.append(row)
        else:
            row["category_fit_hint"]="cross_tagged"
            secondary.append(row)
    return (positive+secondary)[:max(1,int(limit or 18))]


def main():
    cache = get_generation_cache(); cache.reset_stats(); client = get_client(); archive = load_archive(OUTPUT_DIR / "archive.json"); all_categories=[]
    all_feed_urls=set(CONTENT_BANK_FEEDS)|set(IMAGE_BANK_FEEDS)
    for cfg in CATEGORIES.values(): all_feed_urls.update(cfg.get("feeds",[]))
    print(f"Prefetching {len(all_feed_urls)} shared RSS feeds...")
    feed_documents=prefetch_feed_documents(all_feed_urls)
    print("Building source/image banks...")
    image_bank=build_authoritative_image_bank(feed_documents,IMAGE_BANK_FEEDS)
    # Kept for source observability and future related-source recovery without a second feed fetch.
    _content_bank=build_recovered_content_bank(feed_documents,CONTENT_BANK_FEEDS)

    source_sets={}
    for cat_key,cfg in CATEGORIES.items():
        rows=recover_headlines(cfg["feeds"],cache=cache,limit=24,feed_documents=feed_documents,image_extractor=authoritative_extract_image)
        max_age=72 if cat_key in {"world","business","tech","sports","entertainment"} else 60
        rows=[r for r in rows if not _is_source_stale(r,max_age)] or rows[:12]
        _bind_canonical_context(rows,archive);source_sets[cat_key]=rows
        print(f"  {cfg['label']}: {len(rows)} exact-source candidates")

    # TCT-style classifier separates section eligibility from the writer.
    unique=[];key_to_row={}
    for rows in source_sets.values():
        for row in rows:
            k=(str(row.get("link") or ""),re.sub(r"[^a-z0-9]+"," ",str(row.get("title") or "").casefold()).strip())
            if k not in key_to_row:key_to_row[k]=row;unique.append(row)
    classifications={}
    for start in range(0,len(unique),40):
        batch=unique[start:start+40];res=classify_stories(client,batch,cache)
        for i,cats in res.items():
            row=batch[i-1];k=(str(row.get("link") or ""),re.sub(r"[^a-z0-9]+"," ",str(row.get("title") or "").casefold()).strip());classifications[k]=cats
    for cat_key,rows in source_sets.items():
        before=len(rows)
        source_sets[cat_key]=_apply_category_classification(rows,cat_key,classifications,limit=18)
        positive=sum(1 for r in source_sets[cat_key] if r.get("category_fit_hint") in {"positive","unclassified"})
        cross=sum(1 for r in source_sets[cat_key] if r.get("category_fit_hint")=="cross_tagged")
        print(f"  {CATEGORIES[cat_key]['label']}: {len(source_sets[cat_key])}/{before} candidates retained after classification ({positive} positive, {cross} cross-tagged; none rejected)")

    # Guardian recovery is source-safe: it creates its own alternate source row.
    if GUARDIAN_API_KEY:
        for cat_key,rows in source_sets.items():
            additions=[]
            for row in rows:
                if row.get("source_quality") in {"brief","thin","discovery_only"} and len(additions)<3:
                    m=fetch_guardian_match(str(row.get("title") or ""),api_key=GUARDIAN_API_KEY,cache=cache)
                    if m and m.get("link") and all(m.get("link")!=x.get("link") for x in rows+additions):additions.append(m)
            if additions:_bind_canonical_context(additions,archive);rows.extend(additions);print(f"  {cat_key}: {len(additions)} Guardian full-text alternate source(s) recovered")

    print("Running pre-generation duplicate/material-update authority...")
    materiality_report = apply_pre_generation_materiality(source_sets, archive, client, cache)
    print(
        "  Pre-generation semantic gate: "
        f"{materiality_report.get('material_updates', 0)} material update(s), "
        f"{materiality_report.get('duplicates_suppressed', 0)} no-change duplicate(s) suppressed, "
        f"{materiality_report.get('holds_suppressed', 0)} unresolved hold(s) withheld"
    )
    _source_quality_report(source_sets)

    for cat_key,cfg in CATEGORIES.items():
        sources=source_sets.get(cat_key,[])
        data=None; cacheable=False; live_error=None
        if sources:
            ck=_category_cache_key(cat_key,sources);cached=cache.get("categories",ck)
            if cached is not CACHE_MISS:
                data=dict((cached or {}).get("data") or cached);print(f"  {cfg['label']}: generation cache hit")
            else:
                print(f"Assigning/writing: {cfg['label']}...");budget=float(os.environ.get("PLAIN_CATEGORY_GENERATION_BUDGET_SECONDS","180") or 180);last=None
                for attempt in range(1,3):
                    try:data=run_live_assignment_category(client,cat_key,cfg["label"],sources,card_count=CARDS_PER_CATEGORY,timeout_seconds=budget);cacheable=True;break
                    except Exception as exc:last=exc;print(f"  attempt {attempt}/2 failed: {type(exc).__name__}: {exc}")
                if data is None:live_error=f"generation failed after bounded retries: {last}"
        else:
            live_error="no source candidates survived classification/recovery"

        if data is not None:
            _format_generated_times(data);enforce_category_quality(data)
            _diag=(data.get("assignment_editor") or {})
            print(
                f"  {cfg['label']}: {len(data.get('cards',[]))}/{CARDS_PER_CATEGORY} live cards accepted "
                f"(editor selected {_diag.get('editor_selected_card_count','?')}; "
                f"writer attempts {_diag.get('writer_card_attempts','?')}; "
                f"depth backfill {_diag.get('depth_backfill_writer_attempts',0)}; "
                f"writer failures {len(_diag.get('writer_failures') or [])})"
            )
            if len(data.get("cards",[]))<CARDS_PER_CATEGORY:
                print(f"  {cfg['label']}: section-depth shortfall {len(data.get('cards',[]))}/{CARDS_PER_CATEGORY}; no additional safe current source passed quality")
            repairs=((data.get("assignment_editor") or {}).get("protected_material_update_repairs") or [])
            if repairs:
                for repair in repairs:
                    print(
                        f"  {cfg['label']}: protected material update queued for canonical recomposition "
                        f"({repair.get('surface')}; {','.join(repair.get('reasons') or ['unknown'])})"
                    )
            hero_item=data.get("hero") or {}
            hero_quality_ok=bool(hero_item.get("publication_quality_ok",True) or protected_material_update_pending_recomposition(hero_item))
            if not hero_item or not hero_quality_ok:
                live_error="publication-quality contract rejected live hero";data=None;cacheable=False
        if data is None:
            protected_failure = bool(
                sources
                and any(s.get("pre_generation_material_update") for s in sources)
                and live_error
                and "validated material update" in str(live_error).casefold()
            )
            if protected_failure:
                raise RuntimeError(
                    f"{cfg['label']} selected a validated material update but could not safely write it; "
                    "failing closed instead of silently recovering an older category edition: "
                    f"{live_error}"
                )
            data=recover_category_from_archive(cat_key,cfg["label"],archive)
            if data is None:
                raise RuntimeError(f"Required {cfg['label']} category has neither a safe live edition nor a recent canonical recovery: {live_error}")
            print(f"  {cfg['label']}: archive recovery activated ({live_error})")
        elif cacheable:
            cache.put("categories",ck,{"data":data},ttl_seconds=6*3600)
        all_categories.append(data)
    if len(all_categories)!=len(CATEGORIES):raise RuntimeError(f"Required category coverage incomplete: {len(all_categories)}/{len(CATEGORIES)}")

    assignment_report={"generated_at":datetime.utcnow().isoformat()+"Z","assignment_editor_model":"claude-sonnet-5","writer_model":"claude-sonnet-4-5","categories":[{"category_key":c.get("category_key"),"hero_source_index":c.get("hero",{}).get("source_index"),"card_source_indexes":[x.get("source_index") for x in c.get("cards",[])],"diagnostics":c.get("assignment_editor",{})} for c in all_categories]}
    (OUTPUT_DIR/"assignment-editor-report.json").write_text(json.dumps(assignment_report,ensure_ascii=False,indent=2),encoding="utf-8")

    print("Applying persistent Plain editorial engine...")
    audit=apply_editorial_engine(all_categories,output_dir=OUTPUT_DIR,mode=os.environ.get("PLAIN_ENGINE_MODE","enforce"));counts=audit.get("counts",{});print(f"  Editorial engine: {counts.get('processed',0)} processed, {counts.get('rejected',0)} rejected, {counts.get('same_category_duplicates',0)} same-category duplicates")

    rotator=FallbackRotator(OUTPUT_DIR,SITE_URL);used=set()
    for cat in all_categories:
        for item in [cat.get("hero",{}),*cat.get("cards",[])]:
            if isinstance(item,dict):ensure_item_image(item,category_key=cat.get("category_key","top_news"),image_bank=image_bank,archive=archive,rotator=rotator,used_images=used)
    rotator.save();write_image_quality_report(all_categories,OUTPUT_DIR/"source-image-quality-report.json");write_quality_report(all_categories,OUTPUT_DIR/"article-quality-report.json")

    print("Fetching market data...");market_data,market_live=fetch_market_data();top_cat=select_front_page_hero(all_categories)
    if top_cat is None:
        def _score(cat):
            score=int(cat.get("hero",{}).get("urgency_score",0) or 0);cap=CATEGORIES.get(cat.get("category_key"),{}).get("front_page_cap",10)
            return min(score,4) if not CATEGORIES.get(cat.get("category_key"),{}).get("front_page_hero",True) else min(score,cap)
        top_cat=max(all_categories,key=_score)
    promote_duplicate_heroes(top_cat,all_categories)
    for cat in all_categories:ensure_item_image(cat["hero"],category_key=cat.get("category_key","top_news"),image_bank=image_bank,archive=archive,rotator=rotator,used_images=used)
    rotator.save()

    try:
        ranking_cards=[]
        for cat in all_categories:
            for item in [cat.get("hero",{}),*cat.get("cards",[])]:
                if not isinstance(item,dict) or not item.get("headline"):continue
                row=dict(item);row["cat_key"]=row["category_key"]=cat.get("category_key","");row["cat_label"]=cat.get("category_label","");row["published_raw"]=item.get("source_published_raw","");row["source_url"]=item.get("link","");ranking_cards.append(row)
        rh=dict(top_cat.get("hero",{}));rh["cat_key"]=rh["category_key"]=top_cat.get("category_key","");rh["source_url"]=rh.get("link","")
        write_homepage_ranking_recommendations(ranking_cards,rh,registry_path=OUTPUT_DIR/"story-registry.json",archive=archive,output_path=OUTPUT_DIR/"ranking-recommendations.json",review_path=OUTPUT_DIR/"ranking-review.md")
    except Exception as exc:print(f"  Ranking observability warning: {exc}")

    # Crucially publish/bind canonical URLs before rendering live share links.
    # Hidden commit receipts ensure a validated update selected as a snippet/card
    # still refreshes its established canonical without changing Plain's UI model.
    material_update_entries = selected_material_update_commit_entries(all_categories)
    if material_update_entries:
        print(f"  Protected material-update commit queue: {len(material_update_entries)} canonical target(s)")
    write_archives(all_categories,top_cat,client=client,cache=cache,material_update_entries=material_update_entries)
    # Canonical recomposition can repair a protected hero after the earlier writer-stage
    # report was emitted. Rewrite the quality report from the final publishable state.
    write_quality_report(all_categories,OUTPUT_DIR/"article-quality-report.json")
    (OUTPUT_DIR/"news.html").write_text(render_index(all_categories,market_data,market_live,top_cat=top_cat),encoding="utf-8")

    all_cards=[]
    for cat in all_categories:
        for is_hero,item in [(True,cat["hero"]),*[(False,c) for c in cat.get("cards",[])]]:
            all_cards.append({"headline":item.get("headline",""),"teaser":item.get("teaser",""),"body":item.get("body",""),"published":item.get("published",""),"cat_label":cat["category_label"],"urgency_score":item.get("urgency_score",0),"story_id":item.get("story_id",""),"event_key":item.get("event_key",""),"is_hero":is_hero})
    all_cards.sort(key=lambda c:int(c.get("urgency_score",0) or 0),reverse=True);all_cards=_deterministic_story_dedupe(all_cards,top_cat["hero"].get("story_id",""));all_cards=global_rank(all_cards,dedupe_against=top_cat["hero"].get("headline",""))
    def card_to_dict(c):return {k:c.get(k,"" if k!="urgency_score" else 0) for k in ("headline","teaser","body","published","cat_label","urgency_score","story_id","event_key")}
    app_data={"updated":now_et(),"market":{"live":market_live,"sp500":market_data.get("sp500") if market_data else None,"dow":market_data.get("dow") if market_data else None,"nasdaq":market_data.get("nasdaq") if market_data else None,"oil":market_data.get("oil") if market_data else None},"front_page":{"hero":{"headline":top_cat["hero"].get("headline",""),"teaser":top_cat["hero"].get("teaser",""),"body":top_cat["hero"].get("body",""),"image_url":top_cat["hero"].get("image_url",""),"image_credit":top_cat["hero"].get("image_credit",""),"published":top_cat["hero"].get("published",""),"cat_label":top_cat["category_label"],"urgency_score":top_cat["hero"].get("urgency_score",0),"story_id":top_cat["hero"].get("story_id",""),"event_key":top_cat["hero"].get("event_key","")},"cards":[card_to_dict(c) for c in all_cards[:6]]},"categories":[{"key":cat["category_key"],"label":cat["category_label"],"hero":{"headline":cat["hero"].get("headline",""),"teaser":cat["hero"].get("teaser",""),"body":cat["hero"].get("body",""),"image_url":cat["hero"].get("image_url",""),"image_credit":cat["hero"].get("image_credit",""),"published":cat["hero"].get("published",""),"urgency_score":cat["hero"].get("urgency_score",0),"story_id":cat["hero"].get("story_id",""),"event_key":cat["hero"].get("event_key","")},"cards":[{"headline":c.get("headline",""),"teaser":c.get("teaser",""),"body":c.get("body",""),"published":c.get("published",""),"urgency_score":c.get("urgency_score",0),"story_id":c.get("story_id",""),"event_key":c.get("event_key","")} for c in cat.get("cards",[])[:6]]} for cat in all_categories]}
    (OUTPUT_DIR/"data.json").write_text(json.dumps(app_data,ensure_ascii=False,indent=2),encoding="utf-8")
    cache.save(force=True);write_model_usage_report();print(f"  Generation cache: {cache.summary()}");print(f"\nDone. {len(all_categories)} categories written with TCT-derived backend protections.")


if __name__ == "__main__":
    main()
