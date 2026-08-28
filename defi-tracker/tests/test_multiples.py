from dft.metrics.multiples import compute, screen


ASSET = {"symbol": "UNI", "fdv_usd": 6e9, "market_cap_usd": 4e9, "category": "Dexes",
         "llama_slug": "uniswap"}
PROTOCOL = {"tvl_usd": 3e9, "fees_30d": 30e6, "revenue_30d": 2e6, "category": "Dexes",
            "llama_slug": "uniswap"}


def test_compute_basic_ratios():
    m = compute(ASSET, PROTOCOL, min_annualised_fees_usd=2e6, double_count_categories=set())
    assert round(m.annualised_fees) == round(30e6 * 365 / 30)
    assert m.price_to_fees == 6e9 / m.annualised_fees
    assert m.price_to_sales == 6e9 / m.annualised_revenue
    assert m.mcap_to_tvl == 4e9 / 3e9
    assert not m.below_fee_floor


def test_below_fee_floor_suppresses_ratios():
    proto = {"fees_30d": 1000, "category": "Dexes"}
    m = compute({"symbol": "X", "fdv_usd": 1e9, "market_cap_usd": 1e9, "llama_slug": "x"},
               proto, min_annualised_fees_usd=2e6, double_count_categories=set())
    assert m.below_fee_floor
    assert m.price_to_fees is None
    assert m.price_to_sales is None


def test_missing_protocol_data_is_all_none():
    m = compute({"symbol": "BTC", "fdv_usd": 6e11, "market_cap_usd": 4.6e11}, None, 2e6, set())
    assert m.tvl_usd is None
    assert m.price_to_fees is None
    assert m.mcap_to_tvl is None


def test_double_count_flag():
    proto = {"tvl_usd": 1e9, "fees_30d": 1e7, "category": "Liquid Staking"}
    m = compute({"symbol": "LDO", "fdv_usd": 2e9, "market_cap_usd": 2e9, "llama_slug": "lido"},
               proto, 2e6, {"Liquid Staking"})
    assert m.tvl_double_count_risk


def test_annualise_prefers_longest_window():
    from dft.metrics.multiples import _annualise
    assert _annualise(None, None, None, 365e6) == 365e6
    assert _annualise(None, None,30e6, None) == 30e6 * 365 / 30
    assert _annualise(1e6, None, None, None) == 1e6 * 365


def test_screen_handles_assets_without_protocol():
    rows = screen(
        [{"symbol": "BTC", "fdv_usd": 1e12, "market_cap_usd": 9e11}],
        {}, 2e6, [],
    )
    assert rows[0].symbol == "BTC"
    assert rows[0].price_to_fees is None
