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


def test_risk_sizes_order_from_risk_percent_and_stop_distance():
    signal = normalize_signal(
        {
            "symbol": "SOL/USDT",
            "side": "buy",
            "risk_pct": "1",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(max_order_notional=Decimal("5000")), AccountState(equity=Decimal("10000")))

    assert decision.approved is True
    assert decision.order_notional == Decimal("2000")
    assert decision.reason_codes == []


def test_risk_rejects_risk_sizing_without_stop_or_above_risk_pct_cap():
    missing_stop = normalize_signal(
        {
            "symbol": "SOL/USDT",
            "side": "buy",
            "risk_pct": "1",
            "price": "100",
        },
        source="test",
    )
    too_much_risk = normalize_signal(
        {
            "symbol": "SOL/USDT",
            "side": "buy",
            "risk_pct": "3",
            "price": "100",
            "stop_loss_pct": "5",
        },
        source="test",
    )

    missing_stop_decision = evaluate_signal(missing_stop, RiskConfig(require_stop_loss=True), AccountState())
    too_much_risk_decision = evaluate_signal(
        too_much_risk,
        RiskConfig(max_order_notional=Decimal("10000"), max_risk_per_trade_pct=Decimal("2")),
        AccountState(equity=Decimal("10000")),
    )

    assert missing_stop_decision.approved is False
    assert "risk_sizing_requires_stop_loss" in missing_stop_decision.reason_codes
    assert too_much_risk_decision.approved is False
    assert "max_risk_per_trade_pct_exceeded" in too_much_risk_decision.reason_codes


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


def test_risk_rejects_buy_that_would_exceed_symbol_concentration_cap():
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
        RiskConfig(max_symbol_open_notional=Decimal("100")),
        AccountState(symbol_open_notional=Decimal("50")),
    )

    assert decision.approved is False
    assert "max_symbol_open_notional_exceeded" in decision.reason_codes


def test_risk_rejects_entries_when_volatility_regime_exceeds_cap():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
            "volatility_pct": "7.5",
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(max_entry_volatility_pct=Decimal("5")),
        AccountState(),
    )

    assert decision.approved is False
    assert "max_entry_volatility_pct_exceeded" in decision.reason_codes


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


def test_risk_rejects_staged_plan_with_weak_total_reward_risk():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "10", "close_pct": "50"},
            ],
        },
        source="test",
    )

    decision = evaluate_signal(
        signal,
        RiskConfig(min_reward_risk_ratio=Decimal("1"), min_total_reward_risk_ratio=Decimal("2")),
        AccountState(),
    )

    assert decision.approved is False
    assert "min_reward_risk_ratio_not_met" not in decision.reason_codes
    assert "min_total_reward_risk_ratio_not_met" in decision.reason_codes


def test_risk_can_cap_staged_take_profit_target_count():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "30"},
                {"pct": "8", "close_pct": "30"},
                {"pct": "12", "close_pct": "40"},
            ],
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(max_take_profit_targets=2), AccountState())

    assert decision.approved is False
    assert "max_take_profit_targets_exceeded" in decision.reason_codes


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


def test_risk_rejects_trailing_step_without_trailing_stop():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "3000",
            "stop_loss_pct": "3",
            "trailing_step_pct": "1",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(), AccountState())

    assert decision.approved is False
    assert "trailing_stop_required_for_step" in decision.reason_codes


def test_risk_rejects_invalid_trailing_stop_close_pct():
    oversized = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "3000",
            "stop_loss_pct": "3",
            "trailing_stop_pct": "4",
            "trailing_stop_close_pct": "125",
        },
        source="test",
    )
    missing_trail = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "3000",
            "stop_loss_pct": "3",
            "trailing_stop_close_pct": "50",
        },
        source="test",
    )

    oversized_decision = evaluate_signal(oversized, RiskConfig(), AccountState())
    missing_trail_decision = evaluate_signal(missing_trail, RiskConfig(), AccountState())

    assert oversized_decision.approved is False
    assert "invalid_trailing_stop_close_pct" in oversized_decision.reason_codes
    assert missing_trail_decision.approved is False
    assert "trailing_stop_required_for_close_pct" in missing_trail_decision.reason_codes


def test_risk_applies_trailing_amount_to_max_trailing_stop_pct_cap():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "3",
            "trailing_stop_amount": "8",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(max_trailing_stop_pct=Decimal("5")), AccountState())

    assert decision.approved is False
    assert "max_trailing_stop_pct_exceeded" in decision.reason_codes


def test_risk_rejects_invalid_trailing_activation_price():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "3",
            "trailing_stop_amount": "4",
            "trailing_activation_price": "102",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(), AccountState())

    assert decision.approved is False
    assert "invalid_trailing_activation_price" in decision.reason_codes


def test_risk_still_validates_initial_trailing_price_when_amount_sets_distance():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "3",
            "trailing_stop_amount": "4",
            "trailing_stop_price": "101",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(), AccountState())

    assert decision.approved is False
    assert "invalid_trailing_stop_price" in decision.reason_codes


def test_risk_rejects_breakeven_without_protective_exit_to_move():
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "take_profit_pct": "5",
            "breakeven_trigger_pct": "2",
        },
        source="test",
    )

    decision = evaluate_signal(signal, RiskConfig(require_stop_loss=False), AccountState())

    assert decision.approved is False
    assert "breakeven_requires_protective_exit" in decision.reason_codes


def test_risk_rejects_breakeven_after_take_profit_without_target_or_protection():
    missing_target = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "breakeven_after_take_profit": True,
        },
        source="test",
    )
    missing_protection = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "take_profit_pct": "5",
            "breakeven_after_take_profit": True,
        },
        source="test",
    )

    missing_target_decision = evaluate_signal(missing_target, RiskConfig(), AccountState())
    missing_protection_decision = evaluate_signal(missing_protection, RiskConfig(), AccountState())

    assert "breakeven_after_take_profit_requires_take_profit" in missing_target_decision.reason_codes
    assert "breakeven_requires_protective_exit" in missing_protection_decision.reason_codes


def test_risk_rejects_trail_after_take_profit_without_trail_or_target():
    missing_trail = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trail_after_take_profit": True,
        },
        source="test",
    )
    missing_target = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "trailing_stop_pct": "4",
            "trail_after_take_profit": True,
        },
        source="test",
    )

    missing_trail_decision = evaluate_signal(missing_trail, RiskConfig(), AccountState())
    missing_target_decision = evaluate_signal(missing_target, RiskConfig(), AccountState())

    assert "trailing_stop_required_for_take_profit_delay" in missing_trail_decision.reason_codes
    assert "trail_after_take_profit_requires_take_profit" in missing_target_decision.reason_codes


def test_risk_rejects_invalid_or_incomplete_trailing_stop_price():
    inverted = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "trailing_stop_pct": "3",
            "trailing_stop_price": "101",
        },
        source="test",
    )
    missing_pct = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "trailing_stop_price": "103",
        },
        source="test",
    )

    inverted_decision = evaluate_signal(inverted, RiskConfig(), AccountState())
    missing_pct_decision = evaluate_signal(missing_pct, RiskConfig(), AccountState())

    assert inverted_decision.approved is False
    assert "invalid_trailing_stop_price" in inverted_decision.reason_codes
    assert missing_pct_decision.approved is False
    assert "trailing_stop_pct_required_for_price" in missing_pct_decision.reason_codes


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
