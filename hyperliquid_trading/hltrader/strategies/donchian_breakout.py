import numpy as np
import pandas as pd

from ..indicators import donchian
from ..strategy import Strategy, register


@register
class DonchianBreakout(Strategy):
    name = "donchian_breakout"
    description = "Ruptura estilo Turtle: entra al romper el máximo/mínimo de 'entry' barras, sale con el canal de 'exit' barras."
    defaults = {"entry": 55, "exit": 20, "allow_short": True}

    def validate(self):
        if not 1 < self.params["exit"] <= self.params["entry"]:
            raise ValueError("Se requiere 1 < exit <= entry")

    @property
    def warmup(self):
        return self.params["entry"] + 5

    def generate(self, df: pd.DataFrame) -> pd.Series:
        lo_in, hi_in = donchian(df, self.params["entry"])
        lo_out, hi_out = donchian(df, self.params["exit"])
        close = df["close"].to_numpy()
        lo_in, hi_in, lo_out, hi_out = (x.to_numpy() for x in (lo_in, hi_in, lo_out, hi_out))
        out = np.zeros(len(df))
        pos = 0.0
        for i in range(len(df)):
            if np.isnan(hi_in[i]):
                continue
            if pos > 0 and close[i] < lo_out[i]:
                pos = 0.0
            elif pos < 0 and close[i] > hi_out[i]:
                pos = 0.0
            if pos == 0:
                if close[i] > hi_in[i]:
                    pos = 1.0
                elif close[i] < lo_in[i] and self.params["allow_short"]:
                    pos = -1.0
            out[i] = pos
        return pd.Series(out, index=df.index)
