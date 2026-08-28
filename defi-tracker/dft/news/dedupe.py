"""News dedupe: exact URL match plus fuzzy title clustering.

The same wire story hits eight outlets with eight different URLs and near-
identical headlines. URL normalisation catches syndication/tracking-param
duplicates; title similarity clusters the rest so the digest counts one story
once instead of eight times.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "ref", "fbclid", "gclid", "mc_cid", "mc_eid")
_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "to", "for", "and", "or", "with",
    "is", "are", "as", "at", "by", "its", "after", "amid", "over",
}


def normalise_url(url: str) -> str:
    parts = urlsplit(url)
    query = "&".join(
        kv for kv in parts.query.split("&")
        if kv and not kv.split("=")[0].lower().startswith(_TRACKING_PREFIXES)
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def normalise_title(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return " ".join(w for w in words if w not in _STOPWORDS)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def dedupe(items: list[dict[str, Any]], threshold: float = 0.82) -> list[dict[str, Any]]:
    """Attach `url_key`/`title_key` to every item and drop duplicates.

    Exact `url_key` collisions are dropped outright. Remaining items are
    clustered by title similarity above `threshold`; only the first item of
    each cluster (earliest `published_at`, ties broken by input order) survives,
    tagged with how many outlets ran the same story.
    """
    seen_urls: set[str] = set()
    prepared = []
    for item in items:
        item = dict(item)
        item["url_key"] = normalise_url(item["url"])
        if item["url_key"] in seen_urls:
            continue
        seen_urls.add(item["url_key"])
        item["title_key"] = normalise_title(item["title"])
        prepared.append(item)

    prepared.sort(key=lambda i: i.get("published_at") or "")

    clusters: list[dict[str, Any]] = []
    for item in prepared:
        match = None
        for cluster in clusters:
            if similarity(cluster["title_key"], item["title_key"]) >= threshold:
                match = cluster
                break
        if match is None:
            item["outlet_count"] = 1
            item["also_reported_by"] = []
            clusters.append(item)
        else:
            match["outlet_count"] += 1
            match["also_reported_by"] = match["also_reported_by"] + [item["source"]]

    return clusters
