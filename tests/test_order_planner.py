from decimal import Decimal

from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.exchanges.ccxt_adapter import ExchangeCapabilities
from autocrypto.exchanges.order_planner import plan_bracket_execution
from autocrypto.signals import normalize_signal


def test_order_planner_keeps_paper_bracket_synthetic_and_not_live_safe():
    signal = normalize_signal(
        {
            "signal_id": "paper-plan",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="paper",
        spot=True,
        margin=False,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=False,
        fetch_balance=False,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "paper_synthetic_bracket"
    assert plan.live_order_safe is False
    assert [exit_leg.role for exit_leg in plan.exits] == ["stop_loss", "take_profit", "trailing_stop"]
    assert [exit_leg.side for exit_leg in plan.exits] == ["sell", "sell", "sell"]
    assert [exit_leg.close_action for exit_leg in plan.exits] == ["sell_to_close_long"] * 3
    assert [exit_leg.intent for exit_leg in plan.exits] == ["protective_exit", "profit_exit", "protective_exit"]
    assert {exit_leg.exchange_order_family for exit_leg in plan.exits} == {"synthetic_paper_oca"}
    assert plan.summary["protective_exit_count"] == 2
    assert plan.summary["entry_position_effect"] == "open_long"
    assert plan.summary["exit_side"] == "sell"
    assert plan.summary["exit_close_action"] == "sell_to_close_long"
    assert plan.summary["take_profit_close_pct"] == "100"
    assert plan.summary["trailing_stop_close_pct"] == "100"
    assert plan.execution_sequence[-1]["step"] == "track_synthetic_exits"
    assert plan.execution_sequence[-1]["mode"] == "paper"
    assert plan.execution_sequence[-1]["exit_count"] == 3
    assert plan.execution_sequence[-1]["exit_side"] == "sell"
    assert plan.execution_sequence[-1]["close_action"] == "sell_to_close_long"
    assert plan.execution_sequence[-1]["live_submission_enabled"] is False


def test_order_planner_uses_attached_strategy_when_venue_advertises_brackets_and_trailing():
    signal = normalize_signal(
        {
            "signal_id": "attached-plan",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "105",
            "take_profit_price": "90",
            "trailing_stop_amount": "3",
            "exchange": "okx",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="okx",
        spot=True,
        margin=True,
        swap=True,
        future=True,
        option=False,
        create_order=True,
        cancel_order=True,
        fetch_balance=True,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "attached_bracket_with_trailing"
    assert plan.entry.position_effect == "open_short"
    assert plan.exits[0].side == "buy"
    assert plan.exits[0].close_action == "buy_to_cover_short"
    assert plan.exits[0].position_effect == "close"
    assert plan.exits[0].reduce_only is True
    assert plan.exits[0].params["reduceOnly"] is True
    assert plan.exits[0].exchange_order_family == "attached_take_profit_stop_loss"
    assert plan.exits[2].params["trailing"]["callbackAmount"] == "3"
    assert plan.exits[2].activation_status == "open"
    assert [step["step"] for step in plan.execution_sequence] == [
        "submit_entry",
        "wait_for_entry_fill",
        "attach_stop_loss_take_profit",
        "place_or_track_trailing_stop",
    ]
    assert plan.execution_sequence[2]["exit_side"] == "buy"
    assert plan.execution_sequence[2]["close_action"] == "buy_to_cover_short"
    assert all(step["live_submission_enabled"] is False for step in plan.execution_sequence)


def test_order_planner_requires_paper_when_native_trailing_is_not_advertised():
    signal = normalize_signal(
        {
            "signal_id": "fallback-plan",
            "symbol": "SOL/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "3",
            "exchange": "kraken",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="kraken",
        spot=True,
        margin=True,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=True,
        fetch_balance=True,
        attached_stop_loss_take_profit=False,
        oco_order=False,
        trailing_order=False,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "paper_required_for_mixed_bracket_trailing"
    assert "trailing_order_not_advertised" in plan.warnings
    assert "native_bracket_not_advertised" in plan.warnings


def test_order_planner_marks_pending_and_partial_trailing_stop_metadata():
    signal = normalize_signal(
        {
            "signal_id": "pending-partial-plan",
            "symbol": "SOL/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "3",
            "trailing_stop_close_pct": "40",
            "trailing_activation_pct": "2",
            "trailing_step_pct": "0.5",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="paper",
        spot=True,
        margin=False,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=False,
        fetch_balance=False,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)
    trailing = plan.exits[2]

    assert trailing.activation_status == "pending_activation"
    assert trailing.partial_close is True
    assert trailing.params["trailing"]["activationPct"] == "2"
    assert trailing.params["trailing"]["stepPct"] == "0.5"
    assert "trailing_stop_starts_pending_activation" in plan.warnings
    assert plan.summary["pending_trailing_stop_count"] == 1
    assert plan.summary["has_partial_trailing_exit"] is True


def test_order_planner_marks_take_profit_gated_trailing_as_paper_mapping():
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
            "trail_after_take_profit": True,
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="coinbase",
        spot=True,
        margin=False,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=True,
        fetch_balance=True,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)
    trailing = next(exit_leg for exit_leg in plan.exits if exit_leg.role == "trailing_stop")

    assert plan.strategy == "paper_required_for_staged_or_partial_bracket"
    assert trailing.activation_status == "pending_take_profit"
    assert trailing.params["trailing"]["trailAfterTakeProfit"] is True
    assert "trailing_stop_waits_for_take_profit" in plan.warnings
    assert plan.summary["take_profit_gated_trailing_stop_count"] == 1
    assert plan.summary["requires_custom_native_mapping"] is True


def test_signal_exchange_plan_endpoint_returns_non_executing_paper_plan():
    client = TestClient(create_app())

    response = client.post(
        "/signals/exchange-plan",
        json={
            "signal_id": "endpoint-plan",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
            "trailing_stop_pct": "3",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capabilities"]["exchange_id"] == "paper"
    assert body["plan"]["strategy"] == "paper_synthetic_bracket"
    assert body["plan"]["live_order_safe"] is False
    assert body["plan"]["exits"][0]["trigger_price"] == str(Decimal("49000.00"))
    assert body["plan"]["exits"][0]["intent"] == "protective_exit"
    assert body["plan"]["summary"]["protective_exit_count"] == 2
    assert body["plan"]["execution_sequence"][0]["step"] == "submit_entry"


def test_order_planner_requires_paper_for_staged_or_partial_native_bracket_shape():
    signal = normalize_signal(
        {
            "signal_id": "staged-native-plan",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "7", "close_pct": "50"},
                {"pct": "12", "close_pct": "50"},
            ],
            "trailing_stop_pct": "4",
            "trailing_stop_close_pct": "25",
            "exchange": "coinbase",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="coinbase",
        spot=True,
        margin=False,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=True,
        fetch_balance=True,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "paper_required_for_staged_or_partial_bracket"
    assert "staged_or_partial_exits_require_paper_or_custom_native_mapping" in plan.warnings
    assert plan.summary["has_staged_take_profit"] is True
    assert plan.summary["has_partial_take_profit"] is True
    assert plan.summary["has_partial_trailing_exit"] is True
    assert plan.summary["requires_custom_native_mapping"] is True
    assert {exit_leg.exchange_order_family for exit_leg in plan.exits} == {"paper_or_custom_native_mapping"}
    assert plan.execution_sequence[-1]["step"] == "track_synthetic_exits"
    assert plan.execution_sequence[-1]["live_submission_enabled"] is False


def test_order_planner_flags_reduce_only_signals_that_include_bracket_fields():
    signal = normalize_signal(
        {
            "signal_id": "close-with-bracket-fields",
            "symbol": "BTC/USDT",
            "side": "close_long",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="paper",
        spot=True,
        margin=False,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=False,
        fetch_balance=False,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "single_order"
    assert plan.entry.position_effect == "reduce_only"
    assert plan.entry.reduce_only is True
    assert plan.exits == ()
    assert "reduce_only_signal_ignores_bracket_exit_fields" in plan.warnings
    assert plan.summary["ignored_bracket_fields"] is True
