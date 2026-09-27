"""Strategy interface and registry.

A strategy maps a causal OHLCV DataFrame to a *target exposure* series in [-1, 1]:
+1 = fully long, -1 = fully short, 0 = flat (fractions allowed). The same method is used by the
backtester (on the whole history) and by the live runner (on the latest closed candles), so what you
test is exactly what you run. The value at bar t must only depend on data up to and including bar t;
the engine executes it at the open of bar t+1.
"""
from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

_REGISTRY: dict[str, type["Strategy"]] = {}


def register(cls: type["Strategy"]) -> type["Strategy"]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} necesita un atributo 'name'")
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str) -> type["Strategy"]:
    from . import strategies  # noqa: F401  (populate registry)

    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise KeyError(f"Estrategia desconocida '{name}'. Disponibles: {sorted(_REGISTRY)}") from e


def list_strategies() -> dict[str, type["Strategy"]]:
    from . import strategies  # noqa: F401

    return dict(sorted(_REGISTRY.items()))


class Strategy:
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    defaults: ClassVar[dict[str, Any]] = {}

    def __init__(self, **params: Any):
        unknown = set(params) - set(self.defaults)
        if unknown:
            raise ValueError(f"Parámetros desconocidos para {self.name}: {sorted(unknown)}. Válidos: {sorted(self.defaults)}")
        self.params = {**self.defaults, **params}
        for k, default in self.defaults.items():
            v = self.params[k]
            if isinstance(default, bool) and isinstance(v, str):
                self.params[k] = v.strip().lower() in ("1", "true", "yes", "si", "sí")
            elif default is not None and not isinstance(v, type(default)):
                self.params[k] = type(default)(v)
        self.validate()

    def validate(self) -> None:
        """Override to check parameter consistency."""

    @property
    def warmup(self) -> int:
        """Number of bars needed before signals are meaningful (live runner fetches at least this many)."""
        return 200

    def generate(self, df: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def signals(self, df: pd.DataFrame) -> pd.Series:
        s = self.generate(df).reindex(df.index)
        return s.astype(float).fillna(0.0).clip(-1.0, 1.0)

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({args})"
