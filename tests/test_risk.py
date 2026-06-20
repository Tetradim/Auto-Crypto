from decimal import Decimal

from autocrypto.risk import AccountState, RiskConfig, evaluate_signal
from autocrypto.signals import normalize_signal


def test_risk_rejects_signal_without_required_stop_loss():
    signal = normalize_signal(
        {"symbol": "ETH/USDT", "side": "buy", "quote_amount": "100", "price": "3000"},
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(require_stop_loss=True), AccountState())

    assert decision.approved is False
    assert "stop_loss_required" in decision.reason_codes


def test_risk_rejects_oversized_and_overlevered_trade():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "1000",
            "price": "65000",
            "stop_loss_pct": "2",
            "leverage": "5",
        },
        source="test",
    )
    config = RiskConfig(max_order_notional=Decimal("500"), max_leverage=Decimal("3"))

    decision = evaluate_signal(signal, config, AccountState())

    assert decision.approved is False
    assert set(decision.reason_codes) == {"max_order_notional_exceeded", "max_leverage_exceeded"}


def test_risk_approves_valid_signal_and_reports_notional():
    signal = normalize_signal(
        {
            "symbol": "SOL/USDT",
            "side": "buy",
            "base_amount": "2",
            "price": "150",
            "stop_loss_pct": "3",
            "take_profit_pct": "6",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(max_order_notional=Decimal("500")), AccountState())

    assert decision.approved is True
    assert decision.order_notional == Decimal("300")
    assert decision.reason_codes == []


def test_risk_rejects_exchange_not_allowlisted_by_default():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
            "exchange": "binance",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(), AccountState())

    assert decision.approved is False
    assert "exchange_not_allowed" in decision.reason_codes


def test_risk_allows_explicitly_allowlisted_exchange():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
            "exchange": "binance",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(allowed_exchanges={"paper", "binance"}),
        AccountState(),
    )

    assert decision.approved is True
    assert decision.reason_codes == []


def test_risk_rejects_buy_that_would_exceed_max_open_notional():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "75",
            "price": "50000",
            "stop_loss_pct": "2",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(max_open_notional=Decimal("100")),
        AccountState(open_notional=Decimal("50")),
    )

    assert decision.approved is False
    assert "max_open_notional_exceeded" in decision.reason_codes


def test_risk_treats_bracketed_sell_as_position_opening_short():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "75",
            "price": "100",
            "take_profit_pct": "5",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(max_open_notional=Decimal("100"), require_stop_loss=True),
        AccountState(open_notional=Decimal("50")),
    )

    assert decision.approved is False
    assert "stop_loss_required" in decision.reason_codes
    assert "max_open_notional_exceeded" in decision.reason_codes


def test_risk_rejects_position_above_equity_percentage_cap():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "600",
            "price": "50000",
            "stop_loss_pct": "2",
            "take_profit_pct": "6",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(max_position_equity_pct=Decimal("5")),
        AccountState(equity=Decimal("10000")),
    )

    assert decision.approved is False
    assert "max_position_equity_pct_exceeded" in decision.reason_codes


def test_risk_rejects_wide_stop_or_weak_reward_risk():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "3000",
            "stop_loss_pct": "8",
            "take_profit_pct": "10",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(max_stop_loss_pct=Decimal("5"), min_reward_risk_ratio=Decimal("2")),
        AccountState(),
    )

    assert decision.approved is False
    assert "max_stop_loss_pct_exceeded" in decision.reason_codes
    assert "min_reward_risk_ratio_not_met" in decision.reason_codes


def test_risk_applies_stop_width_and_reward_ratio_to_absolute_bracket_prices():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "94",
            "take_profit_price": "108",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(max_stop_loss_pct=Decimal("5"), min_reward_risk_ratio=Decimal("2")),
        AccountState(),
    )

    assert decision.approved is False
    assert "max_stop_loss_pct_exceeded" in decision.reason_codes
    assert "min_reward_risk_ratio_not_met" in decision.reason_codes


def test_risk_rejects_inverted_absolute_bracket_prices():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "95",
            "take_profit_price": "105",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(require_stop_loss=True), AccountState())

    assert decision.approved is False
    assert "invalid_stop_loss_price" in decision.reason_codes
    assert "invalid_take_profit_price" in decision.reason_codes


def test_risk_rejects_later_staged_take_profit_target_below_long_entry():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "95",
            "take_profit_targets": [
                {"trigger_price": "104", "close_pct": "50"},
                {"trigger_price": "99", "close_pct": "50"},
            ],
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(require_stop_loss=True), AccountState())

    assert decision.approved is False
    assert "invalid_take_profit_price" in decision.reason_codes


def test_risk_rejects_staged_take_profit_target_above_short_entry():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "105",
            "take_profit_targets": [
                {"trigger_price": "96", "close_pct": "50"},
                {"trigger_price": "101", "close_pct": "50"},
            ],
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(require_stop_loss=True), AccountState())

    assert decision.approved is False
    assert "invalid_take_profit_price" in decision.reason_codes


def test_risk_treats_reduce_only_close_as_non_opening_even_without_stop_loss():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "close_short",
            "base_amount": "1",
            "price": "95",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(require_stop_loss=True, max_open_notional=Decimal("100")),
        AccountState(open_notional=Decimal("100")),
    )

    assert decision.approved is True
    assert decision.reason_codes == []


def test_risk_ignores_bracket_fields_on_reduce_only_close():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "close_short",
            "base_amount": "1",
            "price": "95",
            "stop_loss_price": "105",
            "take_profit_price": "90",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(require_stop_loss=True), AccountState())

    assert decision.approved is True
    assert decision.reason_codes == []


def test_risk_rejects_wide_trailing_stop_or_activation_without_trailing_stop():
    wide_trailing_signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "3000",
            "stop_loss_pct": "3",
            "trailing_stop_pct": "8",
        },
        source="test",
    )
    activation_only_signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "3000",
            "stop_loss_pct": "3",
            "trailing_activation_pct": "4",
        },
        source="test",
    )
    config = RiskConfig(max_trailing_stop_pct=Decimal("5"))

    wide_decision = evaluate_signal(wide_trailing_signal, config, AccountState())
    activation_decision = evaluate_signal(activation_only_signal, config, AccountState())

    assert wide_decision.approved is False
    assert "max_trailing_stop_pct_exceeded" in wide_decision.reason_codes
    assert activation_decision.approved is False
    assert "trailing_stop_required_for_activation" in activation_decision.reason_codes


def test_risk_rejects_when_consecutive_loss_limit_is_reached():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
            "take_profit_pct": "6",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(max_consecutive_losses=2),
        AccountState(consecutive_losses=2),
    )

    assert decision.approved is False
    assert "consecutive_loss_limit_exceeded" in decision.reason_codes
