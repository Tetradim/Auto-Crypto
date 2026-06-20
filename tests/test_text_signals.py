from decimal import Decimal

import pytest

from autocrypto.signals import SignalValidationError
from autocrypto.text_signals import parse_text_signal


def test_parse_text_signal_accepts_common_crypto_alert_format():
    signal = parse_text_signal("BUY BTCUSDT $125 @ 50000 SL 2.5% TP 5% TRAIL 3% ACT 2% BE 2%", source="discord")

    assert signal.symbol == "BTC/USDT"
    assert signal.side == "buy"
    assert signal.quote_amount == Decimal("125")
    assert signal.price == Decimal("50000")
    assert signal.stop_loss_pct == Decimal("2.5")
    assert signal.take_profit_pct == Decimal("5")
    assert signal.trailing_stop_pct == Decimal("3")
    assert signal.trailing_activation_pct == Decimal("2")
    assert signal.breakeven_trigger_pct == Decimal("2")


def test_parse_text_signal_accepts_ts_alias_for_trailing_stop():
    signal = parse_text_signal("BUY SOLUSDT $50 @ 150 SL 3% TP 8% TS 4%", source="discord")

    assert signal.symbol == "SOL/USDT"
    assert signal.trailing_stop_pct == Decimal("4")


def test_parse_text_signal_accepts_trailing_step_pct():
    signal = parse_text_signal("BUY BTCUSDT $100 @ 50000 SL 2% TP 5% TRAIL 4% STEP 1%", source="discord")

    assert signal.trailing_stop_pct == Decimal("4")
    assert signal.trailing_step_pct == Decimal("1")


def test_parse_text_signal_accepts_breakeven_after_take_profit_flag():
    signal = parse_text_signal(
        "BUY BTCUSDT $100 @ 50000 SL 2% TP1 4% 50% TP2 8% 50% TRAIL 4% BEAFTERTP TRAILAFTERTP",
        source="discord",
    )

    assert signal.breakeven_after_take_profit is True
    assert signal.trail_after_take_profit is True


def test_parse_text_signal_accepts_absolute_bracket_prices():
    signal = parse_text_signal("BUY BTCUSDT $125 @ 50000 SL @ 49000 TP @ 51500 TRAIL 3%", source="discord")

    assert signal.symbol == "BTC/USDT"
    assert signal.side == "buy"
    assert signal.stop_loss_price == Decimal("49000")
    assert signal.take_profit_price == Decimal("51500")
    assert signal.take_profit_targets[0].trigger_price == Decimal("51500")


def test_parse_text_signal_accepts_staged_take_profit_targets():
    signal = parse_text_signal(
        "SHORT ETHUSDT $75 @ 3000 SL @ 3060 TP1 3% 40% TP2 @ 2820 60% TRAIL 2% ACT 1%",
        source="discord",
    )

    assert signal.symbol == "ETH/USDT"
    assert signal.side == "sell"
    assert signal.stop_loss_price == Decimal("3060")
    assert [(target.pct, target.trigger_price, target.close_pct) for target in signal.take_profit_targets] == [
        (Decimal("3"), None, Decimal("40")),
        (None, Decimal("2820"), Decimal("60")),
    ]
    assert signal.trailing_activation_pct == Decimal("1")


def test_parse_text_signal_supports_base_quantity_and_slash_symbol():
    signal = parse_text_signal("SELL ETH/USDT 0.25 @ 3000", source="discord")

    assert signal.symbol == "ETH/USDT"
    assert signal.side == "sell"
    assert signal.base_amount == Decimal("0.25")
    assert signal.price == Decimal("3000")


@pytest.mark.parametrize("message", ["BTC looks strong", "BUY BTCUSDT", "WITHDRAW BTCUSDT $100 @ 1"])
def test_parse_text_signal_rejects_ambiguous_messages(message):
    with pytest.raises(SignalValidationError):
        parse_text_signal(message, source="discord")
