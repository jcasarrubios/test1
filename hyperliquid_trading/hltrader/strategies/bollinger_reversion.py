import numpy as np
import pandas as pd

from ..indicators import bollinger
from ..strategy import Strategy, register


@register
class BollingerReversion(Strategy):
    name = "bollinger_reversion"
    description = "Reversión: largo al cerrar bajo la banda inferior, corto sobre la superior; sale en la media."
    defaults = {"period": 20, "k": 2.0, "allow_short": True}

    @property
    def warmup(self):
        return self.params["period"] + 5

    def generate(self, df: pd.DataFrame) -> pd.Series:
        lower, mid, upper = (x.to_numpy() for x in bollinger(df["close"], self.params["period"], self.params["k"]))
        close = df["close"].to_numpy()
        out = np.zeros(len(df))
        pos = 0.0
        for i in range(len(df)):
            if np.isnan(mid[i]):
                continue
            if pos > 0 and close[i] >= mid[i]:
                pos = 0.0
            elif pos < 0 and close[i] <= mid[i]:
                pos = 0.0
            if pos == 0:
                if close[i] < lower[i]:
                    pos = 1.0
                elif close[i] > upper[i] and self.params["allow_short"]:
                    pos = -1.0
            out[i] = pos
        return pd.Series(out, index=df.index)
