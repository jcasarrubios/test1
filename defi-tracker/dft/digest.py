"""v1: render the daily digest as Markdown.

Ranked multiples screen with percentile flags, filtered news (materiality 3
only), and a thesis-required note — nothing here pushes; it's read once at a
set hour.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .metrics.multiples import Multiple, rank_by_category


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "—"
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.0f}"


def _fmt_x(value: float | None) -> str:
    return "—" if value is None else f"{value:,.1f}x"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def render_multiples_table(rows: list[Multiple]) -> str:
    lines = [
        "| Symbol | Category | FDV | Ann. Fees | Ann. Revenue | P/F | P/S | Mcap/TVL | Flags |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(rows, key=lambda r: (r.price_to_sales is None, r.price_to_sales or 0)):
        flags = []
        if row.below_fee_floor:
            flags.append("below fee floor")
        if row.tvl_double_count_risk:
            flags.append("TVL double-count risk")
        lines.append(
            f"| {row.symbol} | {row.category or '—'} | {_fmt_usd(row.fdv_usd)} | "
            f"{_fmt_usd(row.annualised_fees)} | {_fmt_usd(row.annualised_revenue)} | "
            f"{_fmt_x(row.price_to_fees)} | {_fmt_x(row.price_to_sales)} | "
            f"{_fmt_x(row.mcap_to_tvl)} | {', '.join(flags) or '—'} |"
        )
    return "\n".join(lines)


def render_signal_section(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return "_No trailing-year percentile extremes fired today._"
    lines = []
    for sig in signals:
        pct = sig.get("percentile")
        pct_str = f"P{pct:.0f}" if pct is not None else "—"
        lines.append(
            f"- **{sig['subject']}** · `{sig['signal_type']}` ({sig['direction']}) — "
            f"value {sig.get('value'):+.2f} at {pct_str} of trailing-year distribution"
        )
    return "\n".join(lines)


def render_news_section(items: list[dict[str, Any]], surface_materiality: int) -> str:
    surfaced = [i for i in items if (i.get("materiality") or 0) >= surface_materiality]
    if not surfaced:
        return "_No material (materiality 3) items in the lookback window._"
    lines = []
    for item in surfaced:
        entities = ", ".join(item.get("entities") or []) or "—"
        also = f" (+{item['outlet_count'] - 1} other outlets)" if item.get("outlet_count", 1) > 1 else ""
        lines.append(
            f"- **[{item['category']}]** [{item['title']}]({item['url']}){also} — {item['source']}\n"
            f"  entities: {entities} · {item.get('rationale', '')}"
        )
    return "\n".join(lines)


def render(now: datetime, multiples_rows: list[Multiple], signals: list[dict[str, Any]],
          news_items: list[dict[str, Any]], surface_materiality: int,
          stablecoin_note: str | None = None) -> str:
    date_str = now.strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"# DeFi Valuation Digest — {date_str}",
        "",
        "> Read once. Nothing here is actionable until a thesis is written down "
        "first (`dft thesis add`). Cheap/expensive is a rank against peers or "
        "history, not a buy/sell call.",
        "",
        "## Valuation screen",
        "",
        render_multiples_table(multiples_rows),
        "",
        "## Percentile flags",
        "",
        render_signal_section(signals),
        "",
    ]
    if stablecoin_note:
        parts += ["## System liquidity", "", stablecoin_note, ""]
    parts += [
        f"## Material news (materiality ≥ {surface_materiality})",
        "",
        render_news_section(news_items, surface_materiality),
        "",
    ]
    return "\n".join(parts)
