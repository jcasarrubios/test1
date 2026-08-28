"""News classification via the Claude API.

Design choice worth stating: this is a scheduled batch classifier, not a
push-alert pipeline. A real-time news feed is precisely the machine that
manufactures reasons to enter a trade reactively; a digest computed once a day
and read once, with materiality pre-filtered to only the 3s, works with a
pre-trade-thesis discipline instead of against it.

Uses `output_config.format` (raw JSON schema) rather than `client.messages.parse`
so this module has no pydantic dependency — the schema is the contract.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

log = logging.getLogger(__name__)

CATEGORIES = ["regulatory", "exploit", "unlock_emission", "listing", "upgrade", "macro", "other"]

_SYSTEM = """You are a terse crypto news analyst. Classify each headline/summary \
pair independently. Category is exactly one of: regulatory, exploit, \
unlock_emission, listing, upgrade, macro, other.

Materiality is a 1-3 integer scale for someone who trades a fixed watchlist \
of large-cap DeFi and L1 tokens on a pre-trade-thesis discipline (no reactive \
entries on headlines):
1 = routine / low signal (minor partnership, generic opinion piece, rehashed old news)
2 = notable but not immediately actionable (a proposal, a rumor, a mid-size event)
3 = material (exploit/hack with confirmed loss, a regulatory action naming a \
specific protocol/exchange, a confirmed large unlock or emission change, a \
mainnet upgrade or outage, a listing/delisting on a major venue)

entities: watchlist symbols the item is clearly about, from the provided list \
only. Empty list if none apply. Be conservative with materiality 3 — most news \
is not material."""

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "category": {"type": "string", "enum": CATEGORIES},
        "materiality": {"type": "integer", "enum": [1, 2, 3]},
        "entities": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["index", "category", "materiality", "entities", "rationale"],
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": _ITEM_SCHEMA}},
    "required": ["items"],
    "additionalProperties": False,
}


def _batches(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _prompt(batch: list[dict[str, Any]], watchlist: list[str]) -> str:
    lines = [f"Watchlist symbols: {', '.join(watchlist)}", "", "Classify these items:"]
    for i, item in enumerate(batch):
        lines.append(
            f"[{i}] SOURCE: {item.get('source')}\nTITLE: {item['title']}\n"
            f"SUMMARY: {(item.get('summary') or '')[:400]}"
        )
    return "\n\n".join(lines)


def classify_batch(client, model: str, batch: list[dict[str, Any]],
                   watchlist: list[str]) -> dict[int, dict[str, Any]]:
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=_SYSTEM,
        messages=[{"role": "user", "content": _prompt(batch, watchlist)}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        return {}
    data = json.loads(text)
    return {row["index"]: row for row in data.get("items", [])}


def classify(items: list[dict[str, Any]], watchlist: list[str], model: str,
            batch_size: int = 20, client=None) -> list[dict[str, Any]]:
    """Classify `items` in place-safe copies. On any batch failure, those items
    fall back to the heuristic classifier rather than dropping out of the run.
    """
    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception as exc:  # noqa: BLE001 - e.g. no ANTHROPIC_API_KEY configured
            log.warning("no Claude client available (%s); using heuristic classifier for this run", exc)
            client = False

    out: list[dict[str, Any]] = []
    for batch in _batches(items, batch_size):
        if client is False:
            results = {}
        else:
            try:
                results = classify_batch(client, model, batch, watchlist)
            except Exception as exc:  # noqa: BLE001 - one bad batch must not fail the run
                log.warning("classification batch failed (%d items): %s", len(batch), exc)
                results = {}
        for i, item in enumerate(batch):
            item = dict(item)
            result = results.get(i)
            if result:
                item.update({
                    "category": result["category"],
                    "materiality": result["materiality"],
                    "entities": result["entities"],
                    "rationale": result["rationale"],
                    "classifier": model,
                })
            else:
                item.update(heuristic_classify(item, watchlist))
            out.append(item)
    return out


# --- fallback: no API key / batch failure -----------------------------------

_EXPLOIT_WORDS = ("hack", "exploit", "drain", "rug", "stolen", "breach")
_REG_WORDS = ("sec ", "sec,", "lawsuit", "regulat", "ban ", "charged", "indict")
_LISTING_WORDS = ("lists ", "delist", "listing")
_UPGRADE_WORDS = ("upgrade", "mainnet", "hard fork", "outage", "downtime")
_UNLOCK_WORDS = ("unlock", "emission", "token release", "vesting")


def heuristic_classify(item: dict[str, Any], watchlist: list[str]) -> dict[str, Any]:
    """Keyword fallback so the pipeline degrades rather than breaks without a key."""
    text = f"{item['title']} {item.get('summary', '')}".lower()
    if any(w in text for w in _EXPLOIT_WORDS):
        category, materiality = "exploit", 3
    elif any(w in text for w in _REG_WORDS):
        category, materiality = "regulatory", 2
    elif any(w in text for w in _LISTING_WORDS):
        category, materiality = "listing", 2
    elif any(w in text for w in _UPGRADE_WORDS):
        category, materiality = "upgrade", 2
    elif any(w in text for w in _UNLOCK_WORDS):
        category, materiality = "unlock_emission", 2
    else:
        category, materiality = "other", 1
    entities = [s for s in watchlist if s.lower() in text.split()] or \
               [s for s in watchlist if s.lower() in text]
    return {
        "category": category, "materiality": materiality, "entities": entities,
        "rationale": "heuristic keyword match (no classifier available)",
        "classifier": "heuristic",
    }
