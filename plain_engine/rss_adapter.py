"""RSS and feedparser article adapter."""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .fact_extraction import RawArticle


class RSSArticleError(ValueError):
    """Raised when an RSS entry cannot become a valid RawArticle."""


class RSSArticleAdapter:
    def __init__(
        self,
        *,
        custom_sources: set[str] | None = None,
        default_published_at: datetime | None = None,
    ) -> None:
        self._custom_sources = {
            source.casefold()
            for source in (custom_sources or set())
        }

        self._default_published_at = (
            default_published_at
            or datetime.now(timezone.utc)
        )

    def convert(
        self,
        entry: Mapping[str, Any],
        *,
        source: str,
        county: str | None = None,
        is_custom: bool | None = None,
    ) -> RawArticle:
        title = self._required_text(
            entry.get("title"),
            field_name="title",
        )

        url = self._required_text(
            entry.get("link"),
            field_name="URL",
        )

        body = self._extract_body(entry)

        if not body:
            raise RSSArticleError(
                "RSS entry is missing article body text."
            )

        article_id = self._extract_article_id(
            entry=entry,
            source=source,
            title=title,
            url=url,
        )

        published_at = self._extract_published_at(entry)

        custom = (
            is_custom
            if is_custom is not None
            else source.casefold() in self._custom_sources
        )

        return RawArticle(
            article_id=article_id,
            title=title,
            body=body,
            source=source,
            url=url,
            published_at=published_at,
            county=county,
            is_custom=custom,
        )

    def _extract_body(
        self,
        entry: Mapping[str, Any],
    ) -> str:
        # The generator enriches feed entries with the publisher's exact article
        # text before the editorial audit runs.  Identity decisions must consume
        # that evidence instead of falling back to a thin RSS/Google News summary
        # (which is often little more than the headline).
        for field in ("body", "article_text", "source_text"):
            cleaned = self._clean_html(entry.get(field))
            if cleaned:
                return cleaned

        content = entry.get("content")

        if isinstance(content, list):
            for item in content:
                if isinstance(item, Mapping):
                    value = item.get("value")
                    cleaned = self._clean_html(value)
                    if cleaned:
                        return cleaned

        summary = self._clean_html(entry.get("summary"))

        if summary:
            return summary

        summary_detail = entry.get("summary_detail")

        if isinstance(summary_detail, Mapping):
            return self._clean_html(
                summary_detail.get("value")
            )

        return ""

    def _extract_article_id(
        self,
        *,
        entry: Mapping[str, Any],
        source: str,
        title: str,
        url: str,
    ) -> str:
        supplied_id = entry.get("id") or entry.get("guid")

        if supplied_id:
            return str(supplied_id).strip()

        identity = "\n".join(
            (
                source.casefold().strip(),
                title.casefold().strip(),
                url.casefold().strip(),
            )
        )

        digest = hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:20]

        return f"rss-{digest}"

    def _extract_published_at(
        self,
        entry: Mapping[str, Any],
    ) -> datetime:
        parsed = (
            entry.get("published_parsed")
            or entry.get("updated_parsed")
        )

        if parsed:
            timestamp = calendar.timegm(parsed)
            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

        return self._default_published_at

    @staticmethod
    def _required_text(
        value: Any,
        *,
        field_name: str,
    ) -> str:
        if value is None:
            raise RSSArticleError(
                f"RSS entry is missing required {field_name}."
            )

        text = str(value).strip()

        if not text:
            raise RSSArticleError(
                f"RSS entry is missing required {field_name}."
            )

        return text

    @staticmethod
    def _clean_html(value: Any) -> str:
        if value is None:
            return ""

        text = str(value)

        text = re.sub(
            r"<(?:br|hr)\s*/?>",
            " ",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"</(?:p|div|li|h[1-6])>",
            " ",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text)

        return text.strip()