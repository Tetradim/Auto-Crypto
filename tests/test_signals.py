from decimal import Decimal

import pytest

from autocrypto.signals import SignalValidationError, normalize_signal


def test_normalizes_tradingview_crypto_signal_with_stable_id():
    payload = {
        "symbol": "btcusdt",
        "side": "long",
        "exchange": "coinbase",
        "quote_amount": "125.50",
        "price": "65000",
        "stop_loss_pct": "2.5",
        "take_profit_pct": "7.5",
        "trailing_stop_pct": "3",
        "breakeven_trigger_pct": "2",
        "max_slippage_bps": 50,
        "strategy_id": "tv-breakout",
    }

    signal = normalize_signal(payload, source="tradingview")
    same_signal = normalize_signal(dict(payload), source="tradingview")

    assert signal.symbol == "BTC/USDT"
    assert signal.side == "buy"
    assert signal.exchange == "coinbase"
    assert signal.quote_amount == Decimal("125.50")
    assert signal.price == Decimal("65000")
    assert signal.stop_loss_pct == Decimal("2.5")
    assert signal.take_profit_pct == Decimal("7.5")
    assert signal.trailing_stop_pct == Decimal("3")
    assert signal.breakeven_trigger_pct == Decimal("2")
    assert signal.max_slippage_bps == 50
    assert signal.strategy_id == "tv-breakout"
    assert signal.signal_id == same_signal.signal_id


def test_normalizes_staged_take_profit_targets():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_targets": [
                {"pct": "8", "close_pct": "60"},
                {"pct": "4", "close_pct": "40"},
            ],
        },
        source="test",
    )

    assert signal.take_profit_pct == Decimal("4")
    assert [(target.pct, target.close_pct) for target in signal.take_profit_targets] == [
        (Decimal("4"), Decimal("40")),
        (Decimal("8"), Decimal("60")),
    ]


def test_normalizes_absolute_bracket_prices_and_reduce_only_close_short():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "close_short",
            "base_amount": "1",
            "price": "95",
            "stop_loss_price": "105",
            "take_profit_targets": [
                {"trigger_price": "90", "close_pct": "50"},
                {"price": "85", "close_pct": "50"},
            ],
        },
        source="test",
    )

    assert signal.side == "buy"
    assert signal.reduce_only is True
    assert signal.stop_loss_price == Decimal("105")
    assert [(target.trigger_price, target.close_pct) for target in signal.take_profit_targets] == [
        (Decimal("85"), Decimal("50")),
        (Decimal("90"), Decimal("50")),
    ]


def test_normalizes_nested_bracket_order_payload():
    signal = normalize_signal(
        {
            "symbol": "SOL/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50",
            "bracket_order": {
                "stop_loss_pct": "3",
                "take_profit_targets": [
                    {"pct": "5", "close_pct": "40"},
                    {"pct": "10", "close_pct": "60"},
                ],
                "trailing_stop_pct": "4",
                "trailing_activation_pct": "2",
                "breakeven_trigger_pct": "1.5",
            },
        },
        source="test",
    )

    assert signal.stop_loss_pct == Decimal("3")
    assert [(target.pct, target.close_pct) for target in signal.take_profit_targets] == [
        (Decimal("5"), Decimal("40")),
        (Decimal("10"), Decimal("60")),
    ]
    assert signal.trailing_stop_pct == Decimal("4")
    assert signal.trailing_stop_price is None
    assert signal.trailing_activation_pct == Decimal("2")
    assert signal.breakeven_trigger_pct == Decimal("1.5")


def test_normalizes_exact_initial_trailing_stop_price_from_nested_bracket():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "bracket": {
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
                "trailing_stop_pct": "3",
                "trailing_stop_price": "98.25",
            },
        },
        source="test",
    )

    assert signal.trailing_stop_pct == Decimal("3")
    assert signal.trailing_stop_price == Decimal("98.25")


def test_normalizes_amount_trail_and_absolute_activation_from_nested_bracket():
    signal = normalize_signal(
        {
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "bracket": {
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
                "trail_amount": "4",
                "activation_price": "106",
            },
        },
        source="test",
    )

    assert signal.trailing_stop_amount == Decimal("4")
    assert signal.trailing_activation_price == Decimal("106")


def test_normalizes_trailing_step_controls_from_nested_bracket():
    signal = normalize_signal(
        {
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "bracket": {
                "stop_loss_pct": "5",
                "trailing_stop_pct": "4",
                "trailing_step_pct": "1.25",
                "trailing_step_amount": "0.50",
            },
        },
        source="test",
    )

    assert signal.trailing_step_pct == Decimal("1.25")
    assert signal.trailing_step_amount == Decimal("0.50")


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "BTC/USDT", "side": "withdraw", "quote_amount": 10},
        {"symbol": "BTC/USDT", "side": "buy"},
        {"symbol": "", "side": "buy", "quote_amount": 10},
        {"symbol": "BTC/USDT", "side": "buy", "quote_amount": "-10"},
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "take_profit_targets": [{"pct": "2", "close_pct": "60"}, {"pct": "4", "close_pct": "60"}],
        },
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "take_profit_targets": [{"close_pct": "100"}],
        },
        {"symbol": "BTC/USDT", "side": "buy", "quote_amount": "100", "bracket": "stop 2 tp 4"},
    ],
)
def test_rejects_unsafe_or_ambiguous_signals(payload):
    with pytest.raises(SignalValidationError):
        normalize_signal(payload, source="test")
