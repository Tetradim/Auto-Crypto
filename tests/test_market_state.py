from decimal import Decimal

from autocrypto.market_state import MarketStatePolicy, MarketStateSnapshot, evaluate_market_state


def test_market_state_normal_allows_entries():
    state = evaluate_market_state(
        MarketStateSnapshot(
            volatility_pct=Decimal("2"),
            spread_bps=Decimal("5"),
            depth_notional=Decimal("250000"),
            funding_rate_bps=Decimal("1"),
            minutes_to_funding=120,
            liquidation_buffer_pct=Decimal("20"),
            data_stale_seconds=3,
            exchange_status="ok",
        ),
        MarketStatePolicy(),
    )

    assert state.name == "normal"
    assert state.reason_codes == []
    assert state.no_new_entries is False
    assert state.approval_required is False
    assert state.size_multiplier == Decimal("1")


def test_market_state_stressed_requires_approval_and_reduces_size():
    state = evaluate_market_state(
        MarketStateSnapshot(
            volatility_pct=Decimal("9"),
            spread_bps=Decimal("45"),
            depth_notional=Decimal("25000"),
            funding_rate_bps=Decimal("8"),
            minutes_to_funding=10,
            liquidation_buffer_pct=Decimal("12"),
            data_stale_seconds=5,
            exchange_status="ok",
        ),
        MarketStatePolicy(),
    )

    assert state.name == "stressed"
    assert state.approval_required is True
    assert state.no_new_entries is False
    assert state.size_multiplier == Decimal("0.25")
    assert state.reason_codes == [
        "high_volatility",
        "wide_spread",
        "thin_liquidity",
        "funding_window",
    ]


def test_market_state_halts_new_entries_when_exchange_or_data_is_degraded():
    state = evaluate_market_state(
        MarketStateSnapshot(
            volatility_pct=Decimal("2"),
            spread_bps=Decimal("5"),
            depth_notional=Decimal("250000"),
            funding_rate_bps=Decimal("0"),
            minutes_to_funding=120,
            liquidation_buffer_pct=Decimal("3"),
            data_stale_seconds=120,
            exchange_status="degraded",
        ),
        MarketStatePolicy(min_liquidation_buffer_pct=Decimal("5"), data_stale_after_seconds=30),
    )

    assert state.name == "halted"
    assert state.no_new_entries is True
    assert state.approval_required is True
    assert state.size_multiplier == Decimal("0")
    assert state.reason_codes == [
        "exchange_degraded",
        "market_data_stale",
        "liquidation_buffer_danger",
    ]
