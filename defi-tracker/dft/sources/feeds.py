"""RSS/Atom fetching and parsing, stdlib only.

Free, no key, no quota. Handles both RSS 2.0 (`<item>`) and Atom (`<entry>`),
which is all the crypto outlets publish between them.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from xml.etree import ElementTree

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
ATOM = "{http://www.w3.org/2005/Atom}"


def strip_html(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    clean = html.unescape(_TAG_RE.sub(" ", text))
    clean = " ".join(clean.split())
    return clean[:limit]


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    for parser in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            dt = parser(value.replace("Z", "+00:00") if parser is datetime.fromisoformat else value)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return None


def _text(node, *names: str) -> str | None:
    for name in names:
        found = node.find(name)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return None


def parse(xml_text: str, source: str) -> list[dict[str, Any]]:
    """Parse one feed document into normalised item dicts."""
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError as exc:
        log.warning("feed %s: unparseable XML (%s)", source, exc)
        return []

    items: list[dict[str, Any]] = []
    for node in root.iter():
        tag = node.tag
        if tag == "item":
            link = _text(node, "link", "guid")
            items.append({
                "source": source,
                "title": strip_html(_text(node, "title"), 300),
                "url": link,
                "summary": strip_html(_text(node, "description", "summary")),
                "published_at": _parse_date(
                    _text(node, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
            })
        elif tag == f"{ATOM}entry":
            link_node = node.find(f"{ATOM}link")
            link = (link_node.get("href") if link_node is not None else None) or _text(node, f"{ATOM}id")
            items.append({
                "source": source,
                "title": strip_html(_text(node, f"{ATOM}title"), 300),
                "url": link,
                "summary": strip_html(_text(node, f"{ATOM}summary", f"{ATOM}content")),
                "published_at": _parse_date(_text(node, f"{ATOM}published", f"{ATOM}updated")),
            })
    return [i for i in items if i["title"] and i["url"]]


def fetch_all(http, feeds: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """Fetch every configured feed. One dead feed never fails the batch."""
    items: list[dict[str, Any]] = []
    for feed in feeds:
        try:
            items.extend(parse(http.get_text(feed["url"]), feed.get("name", feed["url"])))
        except Exception as exc:  # noqa: BLE001
            log.warning("feed %s failed: %s", feed.get("name", feed["url"]), exc)
    return items
