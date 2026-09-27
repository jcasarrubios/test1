import numpy as np
import pandas as pd

from ..indicators import rsi, sma
from ..strategy import Strategy, register


@register
class RsiReversion(Strategy):
    name = "rsi_reversion"
    description = "Reversión a la media: compra con RSI sobrevendido, vende con sobrecomprado, sale al cruzar exit_level."
    defaults = {"period": 14, "oversold": 30.0, "overbought": 70.0, "exit_level": 50.0, "trend_filter": 0, "allow_short": True}

    def validate(self):
        p = self.params
        if not 0 < p["oversold"] < p["exit_level"] < p["overbought"] < 100:
            raise ValueError("Se requiere 0 < oversold < exit_level < overbought < 100")

    @property
    def warmup(self):
        return max(self.params["period"] * 5, self.params["trend_filter"] + 1)

    def generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        r = rsi(df["close"], p["period"]).to_numpy()
        trend = sma(df["close"], p["trend_filter"]).to_numpy() if p["trend_filter"] > 0 else None
        close = df["close"].to_numpy()
        out = np.zeros(len(df))
        pos = 0.0
        for i in range(len(df)):
            if np.isnan(r[i]):
                continue
            long_ok = trend is None or (not np.isnan(trend[i]) and close[i] > trend[i])
            short_ok = p["allow_short"] and (trend is None or (not np.isnan(trend[i]) and close[i] < trend[i]))
            if pos == 0:
                if r[i] < p["oversold"] and long_ok:
                    pos = 1.0
                elif r[i] > p["overbought"] and short_ok:
                    pos = -1.0
            elif pos > 0 and r[i] >= p["exit_level"]:
                pos = 0.0
            elif pos < 0 and r[i] <= p["exit_level"]:
                pos = 0.0
            out[i] = pos
        return pd.Series(out, index=df.index)
