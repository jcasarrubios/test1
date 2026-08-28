"""Configuration loading (TOML, stdlib `tomllib`) with sane defaults."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(os.environ.get("DFT_CONFIG", "config/config.toml"))


@dataclass
class Config:
    path: Path
    raw: dict[str, Any] = field(default_factory=dict)

    # --- generic accessors -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # --- frequently used sections -----------------------------------------
    @property
    def db_path(self) -> Path:
        return Path(self.get("core.db_path", "data/dft.db"))

    @property
    def out_dir(self) -> Path:
        return Path(self.get("core.out_dir", "out"))

    @property
    def assets(self) -> list[dict[str, Any]]:
        return list(self.get("assets", []))

    @property
    def watchlist_symbols(self) -> list[str]:
        return [a["symbol"] for a in self.assets if a.get("watchlist", True)]

    @property
    def benchmark_symbol(self) -> str:
        return self.get("core.benchmark", "BTC")

    @property
    def protocol_slugs(self) -> list[str]:
        """Protocols to screen: explicit list, plus any slug attached to an asset."""
        slugs = list(self.get("screen.protocols", []))
        slugs += [a["llama_slug"] for a in self.assets if a.get("llama_slug")]
        return sorted(set(slugs))

    @property
    def feeds(self) -> list[dict[str, str]]:
        return list(self.get("news.feeds", []))


def load(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        raise SystemExit(
            f"config not found: {p}\n"
            "Copy config/config.example.toml to config/config.toml, or set DFT_CONFIG."
        )
    with p.open("rb") as fh:
        return Config(path=p, raw=tomllib.load(fh))
