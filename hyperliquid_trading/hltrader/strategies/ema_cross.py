import numpy as np
import pandas as pd

from ..indicators import ema
from ..strategy import Strategy, register


@register
class EmaCross(Strategy):
    name = "ema_cross"
    description = "Tendencia: largo si EMA rápida > EMA lenta, corto si está por debajo (o plano si allow_short=False)."
    defaults = {"fast": 12, "slow": 48, "allow_short": True}

    def validate(self):
        if not 1 < self.params["fast"] < self.params["slow"]:
            raise ValueError("Se requiere 1 < fast < slow")

    @property
    def warmup(self):
        return self.params["slow"] * 3

    def generate(self, df: pd.DataFrame) -> pd.Series:
        fast, slow = ema(df["close"], self.params["fast"]), ema(df["close"], self.params["slow"])
        sig = pd.Series(np.where(fast > slow, 1.0, -1.0 if self.params["allow_short"] else 0.0), index=df.index)
        return sig.where(slow.notna(), 0.0)
