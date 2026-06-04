"""
cleanup_archive.py — run once via GitHub Actions to deduplicate archive.json.
Replaces duplicate article HTML files with redirect pages (preserves old URLs).
Safe to re-run — idempotent.

Add to update.yml temporarily:
    - run: python cleanup_archive.py
Then remove after one successful run.
"""

import json
import re
from pathlib import Path

REPO_ROOT    = Path(__file__).parent
ARCHIVE_PATH = REPO_ROOT / "archive.json"
ARTICLES_DIR = REPO_ROOT / "articles"

STOPS = {"the","a","an","in","of","for","to","and","or","on","at","is","was","are",
         "were","that","this","with","from","have","been","after","over","into","says",
         "said","will","than","more","also","when","s","county","florida","treasure",
         "coast","martin","lucie","indian","river","beach","port","city","news"}

def sig_tokens(text):
    return frozenset(w.lower().strip(".,;:()") for w in text.split()
                     if len(w) > 3 and w.lower() not in STOPS)

def is_duplicate(headline, existing_sets):
    tok = sig_tokens(headline)
    if len(tok) < 3: return False
    for ex in existing_sets:
        if len(tok & ex) >= 4: return True
    return False

def redirect_page(canonical_slug, site_url):
    url = f"{site_url}/articles/{canonical_slug}.html"
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="0;url={url}">
<link rel="canonical" href="{url}">
<title>Redirecting...</title>
</head>
<body>
<script>window.location.replace("{url}");</script>
</body>
</html>"""

def detect_site_url():
    # Try to read SITE_URL from generate.py
    for fname in ["scripts/generate.py", "generate.py"]:
        p = REPO_ROOT / fname
        if p.exists():
            for line in p.read_text().splitlines():
                if 'SITE_URL' in line and '=' in line and 'http' in line:
                    match = re.search(r'https?://[^\s"\']+', line)
                    if match:
                        return match.group(0).rstrip('/')
    return "https://treasurecoast.today"

def main():
    if not ARCHIVE_PATH.exists():
        print("archive.json not found — nothing to clean.")
        return

    site_url = detect_site_url()
    print(f"Site URL: {site_url}")

    archive = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(archive)} entries")

    # Sort ascending by date — keep earliest version of each story
    archive_sorted = sorted(archive, key=lambda e: e.get("date",""))

    seen_token_sets = []
    canonical_for   = {}  # duplicate_slug -> canonical_slug
    to_keep         = []

    for entry in archive_sorted:
        headline = entry.get("headline","").strip()
        slug     = entry.get("slug","")
        if not headline:
            to_keep.append(entry)
            continue
        if is_duplicate(headline, seen_token_sets):
            # Find the canonical — the most recent kept entry with matching tokens
            tok = sig_tokens(headline)
            for kept in reversed(to_keep):
                if len(tok & sig_tokens(kept["headline"])) >= 4:
                    canonical_for[slug] = kept["slug"]
                    break
        else:
            seen_token_sets.append(sig_tokens(headline))
            to_keep.append(entry)

    dupes = len(archive) - len(to_keep)
    print(f"Keeping {len(to_keep)} unique, replacing {dupes} duplicates with redirects")

    redirected = 0
    for dup_slug, canonical_slug in canonical_for.items():
        html_path = ARTICLES_DIR / f"{dup_slug}.html"
        if html_path.exists():
            html_path.write_text(redirect_page(canonical_slug, site_url), encoding="utf-8")
            print(f"  Redirected: {dup_slug} -> {canonical_slug}")
            redirected += 1

    ARCHIVE_PATH.write_text(json.dumps(to_keep, indent=2), encoding="utf-8")
    print(f"\nDone. {redirected} redirect pages written, archive.json cleaned.")
    print("Trigger a pipeline run to regenerate archive.html and sitemap.xml.")

if __name__ == "__main__":
    main()
