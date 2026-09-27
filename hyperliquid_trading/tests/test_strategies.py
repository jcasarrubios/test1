import pandas as pd
import pytest

from hltrader import get_strategy, list_strategies
from hltrader.strategy import Strategy


@pytest.mark.parametrize("name", list(list_strategies()))
def test_signals_are_causal(name, candles):
    """The signal at bar t must not change when future bars are appended (no look-ahead)."""
    strat = get_strategy(name)()
    full = strat.signals(candles)
    for cut in (400, 800, 1200):
        partial = strat.signals(candles.iloc[:cut])
        pd.testing.assert_series_equal(partial, full.iloc[:cut], check_names=False)


@pytest.mark.parametrize("name", list(list_strategies()))
def test_signals_bounded_and_trade(name, candles):
    s = get_strategy(name)().signals(candles)
    assert s.between(-1, 1).all()
    assert (s.diff().fillna(0) != 0).sum() > 0


def test_long_only_flag(candles):
    s = get_strategy("ema_cross")(allow_short=False).signals(candles)
    assert (s >= 0).all()


def test_param_validation_and_coercion():
    with pytest.raises(ValueError):
        get_strategy("ema_cross")(fast=50, slow=10)
    with pytest.raises(ValueError):
        get_strategy("ema_cross")(nope=1)
    s = get_strategy("ema_cross")(fast="5", slow=20.0, allow_short="false")
    assert s.params == {"fast": 5, "slow": 20, "allow_short": False}
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")


def test_custom_strategy_registration(candles):
    from hltrader.strategy import register

    @register
    class AlwaysLong(Strategy):
        name = "test_always_long"
        defaults = {}

        def generate(self, df):
            return pd.Series(1.0, index=df.index)

    assert get_strategy("test_always_long")().signals(candles).eq(1).all()
