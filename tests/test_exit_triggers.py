from decimal import Decimal

from autocrypto.engine import TradingEngine
from autocrypto.execution import PaperExchange
from autocrypto.signals import normalize_signal


def test_paper_take_profit_trigger_closes_position_and_records_exit_order():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("BTC/USDT", Decimal("105"))

    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "105.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "5.00000000"
    assert exchange.orders[-1].side == "sell"


def test_absolute_bracket_prices_trigger_long_exit_orders():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "95",
            "take_profit_targets": [
                {"trigger_price": "104", "close_pct": "50"},
                {"trigger_price": "108", "close_pct": "50"},
            ],
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("BTC/USDT", Decimal("104"))
    second = exchange.update_price("BTC/USDT", Decimal("94"))

    assert first == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "104.00000000", "quantity": "0.50000000"}
    ]
    assert second == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "94.00000000", "quantity": "0.50000000"}
    ]
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.orders[-1].side == "sell"


def test_paper_stop_loss_trigger_closes_position_once():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("ETH/USDT", Decimal("97"))
    second = exchange.update_price("ETH/USDT", Decimal("96"))

    assert first == [
        {"symbol": "ETH/USDT", "kind": "stop_loss", "price": "97.00000000", "quantity": "1.00000000"}
    ]
    assert second == []
    assert exchange.list_positions()[0]["realized_pnl"] == "-3.00000000"


def test_paper_exits_track_independent_lots_for_same_symbol():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    first_signal = normalize_signal(
        {
            "signal_id": "first-lot",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "5",
        },
        source="test",
    )
    second_signal = normalize_signal(
        {
            "signal_id": "second-lot",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "200",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(first_signal)
    engine.process_signal(second_signal)

    first_trigger = exchange.update_price("BTC/USDT", Decimal("105"))
    position_after_first = exchange.list_positions()[0]
    second_trigger = exchange.update_price("BTC/USDT", Decimal("110"))
    position_after_second = exchange.list_positions()[0]

    assert first_trigger == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "105.00000000", "quantity": "1.00000000"}
    ]
    assert position_after_first["quantity"] == "2.00000000"
    assert position_after_first["realized_pnl"] == "5.00000000"
    assert second_trigger == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "2.00000000"}
    ]
    assert position_after_second["quantity"] == "0.00000000"
    assert position_after_second["realized_pnl"] == "25.00000000"


def test_manual_partial_sell_uses_same_lot_accounting_as_bracket_exits():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    first_signal = normalize_signal(
        {
            "signal_id": "lot-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "50",
        },
        source="test",
    )
    second_signal = normalize_signal(
        {
            "signal_id": "lot-2",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "200",
            "price": "200",
            "stop_loss_pct": "2",
            "take_profit_pct": "10",
        },
        source="test",
    )
    manual_sell = normalize_signal(
        {
            "signal_id": "manual-sell",
            "symbol": "BTC/USDT",
            "side": "sell",
            "base_amount": "1",
            "price": "150",
        },
        source="test",
    )

    engine.process_signal(first_signal)
    engine.process_signal(second_signal)
    engine.process_signal(manual_sell)
    after_manual = exchange.list_positions()[0]
    triggered = exchange.update_price("BTC/USDT", Decimal("220"))
    after_exit = exchange.list_positions()[0]

    assert after_manual["quantity"] == "1.00000000"
    assert after_manual["avg_entry"] == "200.00000000"
    assert after_manual["realized_pnl"] == "50.00000000"
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "220.00000000", "quantity": "1.00000000"}
    ]
    assert after_exit["quantity"] == "0.00000000"
    assert after_exit["realized_pnl"] == "70.00000000"


def test_bracketed_sell_nets_long_lot_before_opening_short_trailing_bracket():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    long_signal = normalize_signal(
        {
            "signal_id": "long-before-reversal",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    reversal_signal = normalize_signal(
        {
            "signal_id": "sell-reversal",
            "symbol": "BTC/USDT",
            "side": "sell",
            "quote_amount": "300",
            "price": "150",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
        source="test",
    )

    engine.process_signal(long_signal)
    result = engine.process_signal(reversal_signal)
    position = exchange.list_positions()[0]
    lots = [(lot.signal_id, lot.direction, lot.remaining_quantity) for lot in exchange.lots]

    assert result.order.netted_quantity == Decimal("1")
    assert result.order.opened_quantity == Decimal("1")
    assert position["quantity"] == "-1.00000000"
    assert position["avg_entry"] == "150.00000000"
    assert position["realized_pnl"] == "50.00000000"
    assert lots == [("sell-reversal", "short", Decimal("1"))]

    exchange.update_price("BTC/USDT", Decimal("140"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    triggered = exchange.update_price("BTC/USDT", Decimal("145.60"))

    assert trailing_exit.trigger_price == Decimal("145.60")
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "145.60000000", "quantity": "1.00000000"}
    ]
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "54.40000000"


def test_bracketed_buy_nets_short_lot_before_opening_long_trailing_bracket():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    short_signal = normalize_signal(
        {
            "signal_id": "short-before-reversal",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "200",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    reversal_signal = normalize_signal(
        {
            "signal_id": "buy-reversal",
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "300",
            "price": "75",
            "stop_loss_pct": "5",
            "take_profit_pct": "30",
            "trailing_stop_pct": "4",
        },
        source="test",
    )

    engine.process_signal(short_signal)
    result = engine.process_signal(reversal_signal)
    position = exchange.list_positions()[0]
    lots = [(lot.signal_id, lot.direction, lot.remaining_quantity) for lot in exchange.lots]

    assert result.order.netted_quantity == Decimal("2")
    assert result.order.opened_quantity == Decimal("2")
    assert position["quantity"] == "2.00000000"
    assert position["avg_entry"] == "75.00000000"
    assert position["realized_pnl"] == "50.00000000"
    assert lots == [("buy-reversal", "long", Decimal("2"))]

    exchange.update_price("ETH/USDT", Decimal("90"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    triggered = exchange.update_price("ETH/USDT", Decimal("86.40"))

    assert trailing_exit.trigger_price == Decimal("86.40")
    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "86.40000000", "quantity": "2.00000000"}
    ]
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "72.80000000"


def test_replayed_reversal_order_history_keeps_only_residual_bracket_lot():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    engine.process_signal(
        normalize_signal(
            {
                "signal_id": "replay-long-before-reversal",
                "symbol": "SOL/USDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
            },
            source="test",
        )
    )
    engine.process_signal(
        normalize_signal(
            {
                "signal_id": "replay-sell-reversal",
                "symbol": "SOL/USDT",
                "side": "sell",
                "quote_amount": "250",
                "price": "125",
                "stop_loss_pct": "4",
                "take_profit_pct": "8",
                "trailing_stop_pct": "3",
            },
            source="test",
        )
    )

    replayed = PaperExchange.from_order_history([order.to_dict() for order in exchange.orders])
    position = replayed.list_positions()[0]

    assert position["quantity"] == "-1.00000000"
    assert position["avg_entry"] == "125.00000000"
    assert position["realized_pnl"] == "25.00000000"
    assert [(lot.signal_id, lot.direction, lot.remaining_quantity) for lot in replayed.lots] == [
        ("replay-sell-reversal", "short", Decimal("1"))
    ]
    assert replayed.orders[-1].netted_quantity == Decimal("1")
    assert replayed.orders[-1].opened_quantity == Decimal("1")


def test_trailing_stop_ratcheted_up_then_triggers_on_pullback():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "trailing-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("BTC/USDT", Decimal("110"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    second = exchange.update_price("BTC/USDT", Decimal("104.50"))

    assert first == []
    assert trailing_exit.trigger_price == Decimal("104.50")
    assert second == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "104.50000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].reduce_only is True
    assert exchange.orders[-1].exit_orders[0].kind == "trailing_stop"
    assert exchange.orders[-1].exit_orders[0].status == "filled"
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "4.50000000"


def test_long_gap_through_stop_and_trailing_uses_nearest_trailing_exit():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "long-gap-through-trail",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)
    exchange.update_price("BTC/USDT", Decimal("120"))

    triggered = exchange.update_price("BTC/USDT", Decimal("90"))

    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "90.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].exit_orders[0].trigger_price == Decimal("114.00")
    assert exchange.orders[-1].canceled_exit_orders


def test_short_gap_through_stop_and_trailing_uses_nearest_trailing_exit():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "short-gap-through-trail",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)
    exchange.update_price("ETH/USDT", Decimal("80"))

    triggered = exchange.update_price("ETH/USDT", Decimal("110"))

    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "110.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].exit_orders[0].trigger_price == Decimal("84.00")
    assert exchange.orders[-1].canceled_exit_orders


def test_partial_trailing_stop_reduces_long_lot_and_keeps_other_exits():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "partial-trailing-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
            "trailing_stop_close_pct": "40",
        },
        source="test",
    )
    engine.process_signal(signal)

    exchange.update_price("BTC/USDT", Decimal("110"))
    triggered = exchange.update_price("BTC/USDT", Decimal("104.50"))
    remaining_exits = [exit_order.kind for exit_order in exchange.lots[0].exit_orders]
    after_partial = exchange.list_positions()[0]
    final = exchange.update_price("BTC/USDT", Decimal("120"))

    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "104.50000000", "quantity": "0.40000000"}
    ]
    assert exchange.orders[-2].exit_orders[0].close_pct == Decimal("40")
    assert remaining_exits == ["stop_loss", "take_profit"]
    assert after_partial["quantity"] == "0.60000000"
    assert final == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "120.00000000", "quantity": "0.60000000"}
    ]
    assert exchange.list_positions()[0]["realized_pnl"] == "13.80000000"


def test_trailing_stop_can_wait_until_first_long_take_profit_fills():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "trail-after-tp-long",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_targets": [
                {"pct": "10", "close_pct": "50"},
                {"pct": "30", "close_pct": "50"},
            ],
            "trailing_stop_pct": "5",
            "trail_after_take_profit": True,
        },
        source="test",
    )
    engine.process_signal(signal)

    dormant_trail = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    before_target = exchange.update_price("BTC/USDT", Decimal("109"))
    first_target = exchange.update_price("BTC/USDT", Decimal("110"))
    active_trail = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    pullback = exchange.update_price("BTC/USDT", Decimal("104.50"))

    assert dormant_trail.status == "pending_take_profit"
    assert before_target == []
    assert first_target == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "0.50000000"}
    ]
    assert active_trail.status == "open"
    assert active_trail.trigger_price == Decimal("104.50")
    assert pullback == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "104.50000000", "quantity": "0.50000000"}
    ]
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"


def test_trailing_stop_can_wait_until_first_short_take_profit_fills():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "trail-after-tp-short",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_targets": [
                {"pct": "10", "close_pct": "50"},
                {"pct": "30", "close_pct": "50"},
            ],
            "trailing_stop_pct": "5",
            "trail_after_take_profit": True,
        },
        source="test",
    )
    engine.process_signal(signal)

    before_target = exchange.update_price("ETH/USDT", Decimal("91"))
    first_target = exchange.update_price("ETH/USDT", Decimal("90"))
    active_trail = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    bounce = exchange.update_price("ETH/USDT", Decimal("94.50"))

    assert before_target == []
    assert first_target == [
        {"symbol": "ETH/USDT", "kind": "take_profit", "price": "90.00000000", "quantity": "0.50000000"}
    ]
    assert active_trail.status == "open"
    assert active_trail.trigger_price == Decimal("94.50")
    assert bounce == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "94.50000000", "quantity": "0.50000000"}
    ]
    assert exchange.orders[-1].side == "buy"


def test_take_profit_gated_trailing_does_not_loosen_breakeven_lock():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "trail-after-tp-with-breakeven",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_targets": [
                {"pct": "2", "close_pct": "50"},
                {"pct": "12", "close_pct": "50"},
            ],
            "trailing_stop_pct": "5",
            "trail_after_take_profit": True,
            "breakeven_after_take_profit": True,
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("BTC/USDT", Decimal("102"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")

    assert triggered == [
        {
            "symbol": "BTC/USDT",
            "kind": "take_profit",
            "price": "102.00000000",
            "quantity": "0.50000000",
            "breakeven_after_take_profit": "true",
        }
    ]
    assert stop_exit.trigger_price == Decimal("100.00")
    assert trailing_exit.trigger_price == Decimal("100.00")
    assert trailing_exit.status == "open"


def test_take_profit_fill_can_lock_remaining_protective_exits_in_profit():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "tp-profit-lock",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "15", "close_pct": "50"},
            ],
            "trailing_stop_pct": "4",
            "profit_lock_after_take_profit_pct": "2",
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("BTC/USDT", Decimal("105"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    pullback = exchange.update_price("BTC/USDT", Decimal("102"))

    assert triggered == [
        {
            "symbol": "BTC/USDT",
            "kind": "take_profit",
            "price": "105.00000000",
            "quantity": "0.50000000",
            "profit_lock_after_take_profit_pct": "2",
        }
    ]
    assert stop_exit.trigger_price == Decimal("102.00")
    assert trailing_exit.trigger_price == Decimal("102.00")
    assert pullback == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "102.00000000", "quantity": "0.50000000"}
    ]
    assert exchange.list_positions()[0]["realized_pnl"] == "3.50000000"


def test_profit_lock_after_take_profit_can_tighten_existing_breakeven_lock():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "profit-lock-after-be",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "15", "close_pct": "50"},
            ],
            "trailing_stop_pct": "4",
            "breakeven_trigger_pct": "2",
            "profit_lock_after_take_profit_pct": "3",
        },
        source="test",
    )
    engine.process_signal(signal)

    exchange.update_price("BTC/USDT", Decimal("102"))
    breakeven_stop = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    triggered = exchange.update_price("BTC/USDT", Decimal("105"))
    locked_stop = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")

    assert breakeven_stop.trigger_price == Decimal("100.00")
    assert triggered[0]["profit_lock_after_take_profit_pct"] == "3"
    assert locked_stop.trigger_price == Decimal("103.00")


def test_short_take_profit_fill_can_lock_remaining_protective_exits_in_profit():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "short-tp-profit-lock",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "15", "close_pct": "50"},
            ],
            "trailing_stop_pct": "4",
            "profit_lock_after_take_profit_pct": "2",
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("ETH/USDT", Decimal("95"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    bounce = exchange.update_price("ETH/USDT", Decimal("98"))

    assert triggered == [
        {
            "symbol": "ETH/USDT",
            "kind": "take_profit",
            "price": "95.00000000",
            "quantity": "0.50000000",
            "profit_lock_after_take_profit_pct": "2",
        }
    ]
    assert stop_exit.trigger_price == Decimal("98.00")
    assert trailing_exit.trigger_price == Decimal("98.00")
    assert bounce == [
        {"symbol": "ETH/USDT", "kind": "stop_loss", "price": "98.00000000", "quantity": "0.50000000"}
    ]
    assert exchange.orders[-1].side == "buy"


def test_paper_bracket_time_exit_closes_after_max_hold_marks():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "timed-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "max_hold_marks": 2,
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("BTC/USDT", Decimal("101"))
    second = exchange.update_price("BTC/USDT", Decimal("102"))

    assert first == []
    assert second == [
        {"symbol": "BTC/USDT", "kind": "time_exit", "price": "102.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].exit_kind == "time_exit"
    assert exchange.orders[-1].canceled_exit_orders
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "2.00000000"


def test_short_time_exit_opens_and_closes_paper_bracket():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "timed-short",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "time_stop_marks": 1,
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("ETH/USDT", Decimal("95"))

    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "time_exit", "price": "95.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].side == "buy"
    assert exchange.list_positions()[0]["realized_pnl"] == "5.00000000"


def test_trailing_stop_step_pct_skips_small_long_ratchets():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "stepped-trailing-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "trailing_stop_pct": "5",
            "trailing_step_pct": "1",
        },
        source="test",
    )
    engine.process_signal(signal)

    small_move = exchange.update_price("BTC/USDT", Decimal("100.50"))
    unchanged = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    larger_move = exchange.update_price("BTC/USDT", Decimal("101.10"))
    ratcheted = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")

    assert small_move == []
    assert larger_move == []
    assert unchanged.trigger_price == Decimal("95.00")
    assert ratcheted.trigger_price == Decimal("96.05")


def test_trailing_stop_step_amount_skips_small_short_ratchets():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "stepped-short-trailing-entry",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "trailing_stop_amount": "5",
            "trailing_step_amount": "2",
        },
        source="test",
    )
    engine.process_signal(signal)

    small_move = exchange.update_price("ETH/USDT", Decimal("99"))
    unchanged = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    larger_move = exchange.update_price("ETH/USDT", Decimal("97"))
    ratcheted = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")

    assert small_move == []
    assert larger_move == []
    assert unchanged.trigger_price == Decimal("105.00")
    assert ratcheted.trigger_price == Decimal("102.00")


def test_exact_initial_trailing_stop_price_is_used_before_ratcheting():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "exact-trailing-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
            "trailing_stop_price": "98.25",
        },
        source="test",
    )
    engine.process_signal(signal)

    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    first = exchange.update_price("BTC/USDT", Decimal("101"))
    ratcheted_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    second = exchange.update_price("BTC/USDT", Decimal("98.24"))

    assert trailing_exit.trigger_price == Decimal("98.25")
    assert first == []
    assert ratcheted_exit.trigger_price == Decimal("98.25")
    assert second == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "98.24000000", "quantity": "1.00000000"}
    ]


def test_trailing_stop_activation_waits_for_favorable_move_before_arming():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "activated-trail-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
            "trailing_activation_pct": "4",
        },
        source="test",
    )
    engine.process_signal(signal)

    before_activation = exchange.update_price("BTC/USDT", Decimal("96"))
    dormant_trailing_exit = next(
        exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop"
    )
    activation_mark = exchange.update_price("BTC/USDT", Decimal("104"))
    assert exchange.lots[0].trailing_activated is True
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    pullback = exchange.update_price("BTC/USDT", Decimal("98.80"))

    assert before_activation == []
    assert dormant_trailing_exit.status == "pending_activation"
    assert activation_mark == []
    assert trailing_exit.status == "open"
    assert trailing_exit.trigger_price == Decimal("98.80")
    assert pullback == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "98.80000000", "quantity": "1.00000000"}
    ]


def test_amount_trailing_stop_uses_fixed_quote_distance_for_long():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "amount-trail-long",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_amount": "4",
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("BTC/USDT", Decimal("110"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    second = exchange.update_price("BTC/USDT", Decimal("106"))

    assert first == []
    assert trailing_exit.trigger_price == Decimal("106.00")
    assert second == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "106.00000000", "quantity": "1.00000000"}
    ]


def test_absolute_trailing_activation_price_arms_short_trail():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "absolute-activation-short",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_amount": "5",
            "trailing_activation_price": "94",
        },
        source="test",
    )
    engine.process_signal(signal)

    before_activation = exchange.update_price("ETH/USDT", Decimal("95"))
    dormant_trailing_exit = next(
        exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop"
    )
    activation_mark = exchange.update_price("ETH/USDT", Decimal("94"))
    trailing_activated = exchange.lots[0].trailing_activated
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    pullback = exchange.update_price("ETH/USDT", Decimal("99"))

    assert before_activation == []
    assert dormant_trailing_exit.status == "pending_activation"
    assert activation_mark == []
    assert trailing_activated is True
    assert trailing_exit.status == "open"
    assert trailing_exit.trigger_price == Decimal("99.00")
    assert pullback == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "99.00000000", "quantity": "1.00000000"}
    ]


def test_breakeven_trigger_raises_protective_stops_to_entry():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "breakeven-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "10",
            "breakeven_trigger_pct": "3",
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("BTC/USDT", Decimal("103"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    second = exchange.update_price("BTC/USDT", Decimal("100"))

    assert first == []
    assert stop_exit.trigger_price == Decimal("100.00")
    assert trailing_exit.trigger_price == Decimal("100.00")
    assert second == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "100.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.list_positions() == []
    assert exchange.orders[-1].side == "sell"
    assert exchange.orders[-1].price == Decimal("100")


def test_staged_take_profit_closes_partial_lot_and_keeps_protective_stop():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "scaled-entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "40"},
                {"pct": "10", "close_pct": "60"},
            ],
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("BTC/USDT", Decimal("105"))
    after_first = exchange.list_positions()[0]
    remaining_exits = list(exchange.lots[0].exit_orders)
    second = exchange.update_price("BTC/USDT", Decimal("95"))

    assert first == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "105.00000000", "quantity": "0.40000000"}
    ]
    assert after_first["quantity"] == "0.60000000"
    assert after_first["realized_pnl"] == "2.00000000"
    assert any(exit_order.kind == "stop_loss" for exit_order in remaining_exits)
    assert second == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "95.00000000", "quantity": "0.60000000"}
    ]


def test_staged_take_profit_can_move_remaining_protective_exits_to_breakeven():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "tp-to-breakeven",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "trailing_stop_pct": "8",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "40"},
                {"pct": "10", "close_pct": "60"},
            ],
            "breakeven_after_take_profit": True,
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("BTC/USDT", Decimal("105"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    breakeven_applied = exchange.lots[0].breakeven_applied
    second = exchange.update_price("BTC/USDT", Decimal("100"))

    assert first == [
        {
            "symbol": "BTC/USDT",
            "kind": "take_profit",
            "price": "105.00000000",
            "quantity": "0.40000000",
            "breakeven_after_take_profit": "true",
        }
    ]
    assert stop_exit.trigger_price == Decimal("100.00")
    assert trailing_exit.trigger_price == Decimal("100.00")
    assert breakeven_applied is True
    assert second == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "100.00000000", "quantity": "0.60000000"}
    ]


def test_short_staged_take_profit_can_move_remaining_stop_to_breakeven():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "short-tp-to-breakeven",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "10", "close_pct": "50"},
            ],
            "breakeven_after_take_profit": True,
        },
        source="test",
    )
    engine.process_signal(signal)

    first = exchange.update_price("ETH/USDT", Decimal("95"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    second = exchange.update_price("ETH/USDT", Decimal("100"))

    assert first == [
        {
            "symbol": "ETH/USDT",
            "kind": "take_profit",
            "price": "95.00000000",
            "quantity": "0.50000000",
            "breakeven_after_take_profit": "true",
        }
    ]
    assert stop_exit.trigger_price == Decimal("100.00")
    assert second == [
        {"symbol": "ETH/USDT", "kind": "stop_loss", "price": "100.00000000", "quantity": "0.50000000"}
    ]


def test_staged_take_profit_fills_all_crossed_targets_on_one_price_mark():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "gap-through-stages",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "40"},
                {"pct": "10", "close_pct": "60"},
            ],
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("BTC/USDT", Decimal("110"))

    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "0.40000000"},
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "0.60000000"},
    ]
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "10.00000000"


def test_final_bracket_exit_records_canceled_oca_siblings():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "oca-final-close",
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
    engine.process_signal(signal)

    triggered = exchange.update_price("BTC/USDT", Decimal("110"))

    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].exit_kind == "take_profit"
    assert exchange.orders[-1].reduce_only is True
    assert [(order.kind, order.status) for order in exchange.orders[-1].exit_orders] == [
        ("take_profit", "filled"),
    ]
    assert [(order.kind, order.status, order.oca_group) for order in exchange.orders[-1].canceled_exit_orders] == [
        ("stop_loss", "canceled", "oca-oca-final-close"),
        ("trailing_stop", "canceled", "oca-oca-final-close"),
    ]


def test_cancel_bracket_removes_exits_without_closing_position():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "cancel-bracket-entry",
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
    engine.process_signal(signal)

    cancel_order = exchange.cancel_bracket("cancel-bracket-entry", reason="operator override")
    triggered = exchange.update_price("BTC/USDT", Decimal("90"))
    position = exchange.list_positions()[0]

    assert cancel_order is not None
    assert cancel_order.side == "cancel"
    assert cancel_order.exit_kind == "bracket_cancel"
    assert cancel_order.status == "canceled"
    assert [(order.kind, order.status) for order in cancel_order.canceled_exit_orders] == [
        ("stop_loss", "canceled"),
        ("take_profit", "canceled"),
        ("trailing_stop", "canceled"),
    ]
    assert triggered == []
    assert position["quantity"] == "1.00000000"
    assert exchange.active_exits == {}


def test_cancel_bracket_records_pending_trailing_stop_as_canceled():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "cancel-pending-trail",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
            "trailing_activation_pct": "3",
        },
        source="test",
    )
    engine.process_signal(signal)

    cancel_order = exchange.cancel_bracket("cancel-pending-trail", reason="operator override")

    assert cancel_order is not None
    assert [(order.kind, order.status) for order in cancel_order.canceled_exit_orders] == [
        ("stop_loss", "canceled"),
        ("take_profit", "canceled"),
        ("trailing_stop", "canceled"),
    ]
    assert exchange.active_exits == {}


def test_close_bracket_closes_long_lot_and_cancels_synthetic_exits():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "close-long-bracket",
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
    engine.process_signal(signal)

    order = exchange.close_bracket("close-long-bracket", Decimal("107"), reason="operator close")
    triggered = exchange.update_price("BTC/USDT", Decimal("110"))
    position = exchange.list_positions()[0]

    assert order is not None
    assert order.side == "sell"
    assert order.reduce_only is True
    assert order.exit_kind == "bracket_manual_close"
    assert order.exit_orders[0].kind == "manual_close"
    assert order.exit_orders[0].status == "filled"
    assert [(exit_order.kind, exit_order.status) for exit_order in order.canceled_exit_orders] == [
        ("stop_loss", "canceled"),
        ("take_profit", "canceled"),
        ("trailing_stop", "canceled"),
    ]
    assert triggered == []
    assert position["quantity"] == "0.00000000"
    assert position["realized_pnl"] == "7.00000000"
    assert exchange.active_exits == {}


def test_close_bracket_can_partially_reduce_long_lot_and_keep_exits():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "partial-close-long-bracket",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(signal)

    order = exchange.close_bracket("partial-close-long-bracket", Decimal("106"), close_pct=Decimal("25"))
    position = exchange.list_positions()[0]
    remaining_exits = exchange.active_exits["BTC/USDT"]
    triggered = exchange.update_price("BTC/USDT", Decimal("110"))

    assert order is not None
    assert order.exit_kind == "bracket_manual_reduce"
    assert order.side == "sell"
    assert order.reduce_only is True
    assert order.notional == Decimal("26.50")
    assert order.canceled_exit_orders == []
    assert position["quantity"] == "0.75000000"
    assert position["realized_pnl"] == "1.50000000"
    assert [exit_order.kind for exit_order in remaining_exits] == ["stop_loss", "take_profit"]
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "0.75000000"}
    ]
    assert exchange.list_positions()[0]["realized_pnl"] == "9.00000000"


def test_close_bracket_closes_short_lot_with_buy_reduce_only_order():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "close-short-bracket",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(signal)

    order = exchange.close_bracket("close-short-bracket", Decimal("94"), reason="operator close")
    position = exchange.list_positions()[0]

    assert order is not None
    assert order.side == "buy"
    assert order.reduce_only is True
    assert order.notional == Decimal("94")
    assert position["quantity"] == "0.00000000"
    assert position["realized_pnl"] == "6.00000000"


def test_amend_long_bracket_stop_tightens_but_does_not_loosen():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "tighten-long-stop",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(signal)

    loosened = exchange.amend_bracket_stop("tighten-long-stop", Decimal("94"))
    amended = exchange.amend_bracket_stop("tighten-long-stop", Decimal("99"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    triggered = exchange.update_price("BTC/USDT", Decimal("99"))

    assert loosened is None
    assert amended is not None
    assert amended.exit_kind == "bracket_stop_amend"
    assert amended.status == "amended"
    assert stop_exit.trigger_price == Decimal("99.00")
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "99.00000000", "quantity": "1.00000000"}
    ]


def test_amend_short_bracket_stop_tightens_downward():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "tighten-short-stop",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "20",
        },
        source="test",
    )
    engine.process_signal(signal)

    loosened = exchange.amend_bracket_stop("tighten-short-stop", Decimal("111"))
    amended = exchange.amend_bracket_stop("tighten-short-stop", Decimal("102"))
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    triggered = exchange.update_price("ETH/USDT", Decimal("102"))

    assert loosened is None
    assert amended is not None
    assert stop_exit.trigger_price == Decimal("102.00")
    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "stop_loss", "price": "102.00000000", "quantity": "1.00000000"}
    ]


def test_short_bracket_take_profit_closes_with_buy_exit():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "short-entry",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("ETH/USDT", Decimal("90"))

    assert exchange.orders[0].side == "sell"
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "10.00000000"
    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "take_profit", "price": "90.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].side == "buy"


def test_absolute_bracket_prices_trigger_short_buy_exit_orders():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "absolute-short",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "106",
            "take_profit_price": "92",
        },
        source="test",
    )
    engine.process_signal(signal)

    triggered = exchange.update_price("ETH/USDT", Decimal("92"))

    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "take_profit", "price": "92.00000000", "quantity": "1.00000000"}
    ]
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "8.00000000"
    assert exchange.orders[-1].side == "buy"


def test_reduce_only_buy_closes_open_short_without_opening_long():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    short_signal = normalize_signal(
        {
            "signal_id": "short-to-close",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    close_signal = normalize_signal(
        {
            "signal_id": "reduce-only-short-close",
            "symbol": "ETH/USDT",
            "side": "close_short",
            "base_amount": "0.4",
            "price": "90",
        },
        source="test",
    )

    engine.process_signal(short_signal)
    engine.process_signal(close_signal)

    position = exchange.list_positions()[0]
    assert exchange.orders[-1].side == "buy"
    assert exchange.orders[-1].reduce_only is True
    assert position["quantity"] == "-0.60000000"
    assert position["avg_entry"] == "100.00000000"
    assert position["realized_pnl"] == "4.00000000"


def test_short_trailing_stop_ratcheted_down_then_triggers_on_bounce():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "short-trailing-entry",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
            "trailing_activation_pct": "4",
        },
        source="test",
    )
    engine.process_signal(signal)

    before_activation = exchange.update_price("ETH/USDT", Decimal("97"))
    dormant_trailing_exit = next(
        exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop"
    )
    activation_mark = exchange.update_price("ETH/USDT", Decimal("96"))
    trailing_activated = exchange.lots[0].trailing_activated
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    pullback = exchange.update_price("ETH/USDT", Decimal("100.80"))

    assert before_activation == []
    assert dormant_trailing_exit.status == "pending_activation"
    assert activation_mark == []
    assert trailing_activated is True
    assert trailing_exit.status == "open"
    assert trailing_exit.trigger_price == Decimal("100.80")
    assert pullback == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "100.80000000", "quantity": "1.00000000"}
    ]
    assert exchange.orders[-1].side == "buy"
    assert exchange.orders[-1].reduce_only is True


def test_partial_trailing_stop_reduces_short_lot_and_keeps_other_exits():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "partial-short-trail",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
            "trailing_stop_close_pct": "25",
        },
        source="test",
    )
    engine.process_signal(signal)

    exchange.update_price("ETH/USDT", Decimal("90"))
    triggered = exchange.update_price("ETH/USDT", Decimal("94.50"))
    remaining_exits = [exit_order.kind for exit_order in exchange.lots[0].exit_orders]
    after_partial = exchange.list_positions()[0]
    final = exchange.update_price("ETH/USDT", Decimal("80"))

    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "94.50000000", "quantity": "0.25000000"}
    ]
    assert remaining_exits == ["stop_loss", "take_profit"]
    assert after_partial["quantity"] == "-0.75000000"
    assert final == [
        {"symbol": "ETH/USDT", "kind": "take_profit", "price": "80.00000000", "quantity": "0.75000000"}
    ]
    assert exchange.list_positions()[0]["realized_pnl"] == "16.37500000"


def test_amend_long_trailing_stop_tightens_and_arms_pending_trail():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "tighten-long-trail",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
            "trailing_activation_pct": "4",
        },
        source="test",
    )
    engine.process_signal(signal)

    loosened = exchange.amend_bracket_trailing_stop("tighten-long-trail", Decimal("94"))
    amended = exchange.amend_bracket_trailing_stop("tighten-long-trail", Decimal("98"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    trailing_activated = exchange.lots[0].trailing_activated
    triggered = exchange.update_price("BTC/USDT", Decimal("98"))

    assert loosened is None
    assert amended is not None
    assert amended.exit_kind == "bracket_trailing_stop_amend"
    assert amended.status == "amended"
    assert trailing_exit.status == "open"
    assert trailing_exit.trigger_price == Decimal("98.00")
    assert trailing_activated is True
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "98.00000000", "quantity": "1.00000000"}
    ]


def test_tighten_long_trailing_stop_to_mark_derives_protective_trigger():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "trail-from-long-mark",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
            "trailing_activation_pct": "4",
        },
        source="test",
    )
    engine.process_signal(signal)

    rejected = exchange.tighten_bracket_trailing_stop_to_mark("trail-from-long-mark", Decimal("102"))
    amended = exchange.tighten_bracket_trailing_stop_to_mark("trail-from-long-mark", Decimal("110"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    high_water_mark = exchange.lots[0].high_water_mark
    triggered = exchange.update_price("BTC/USDT", Decimal("104.50"))

    assert rejected is None
    assert amended is not None
    assert amended.exit_kind == "bracket_trailing_stop_mark_amend"
    assert amended.price == Decimal("110.00")
    assert trailing_exit.status == "open"
    assert trailing_exit.trigger_price == Decimal("104.50")
    assert high_water_mark == Decimal("110.0")
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "104.50000000", "quantity": "1.00000000"}
    ]


def test_tighten_trailing_stop_to_mark_respects_trailing_step():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "trail-from-mark-step",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
            "trailing_step_amount": "2",
        },
        source="test",
    )
    engine.process_signal(signal)

    small_step = exchange.tighten_bracket_trailing_stop_to_mark("trail-from-mark-step", Decimal("101"))
    amended = exchange.tighten_bracket_trailing_stop_to_mark("trail-from-mark-step", Decimal("103"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")

    assert small_step is None
    assert amended is not None
    assert trailing_exit.trigger_price == Decimal("97.85")


def test_manual_breakeven_moves_stop_and_trailing_exit_to_entry():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "manual-breakeven",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "8",
            "trailing_activation_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)

    order = exchange.move_bracket_to_breakeven("manual-breakeven", reason="operator locked risk")
    stop_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "stop_loss")
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    breakeven_applied = exchange.lots[0].breakeven_applied
    trailing_activated = exchange.lots[0].trailing_activated
    triggered = exchange.update_price("BTC/USDT", Decimal("100"))

    assert order is not None
    assert order.exit_kind == "bracket_breakeven"
    assert [(exit_order.kind, exit_order.trigger_price, exit_order.status) for exit_order in order.exit_orders] == [
        ("stop_loss", Decimal("100.00"), "open"),
        ("trailing_stop", Decimal("100.00"), "open"),
    ]
    assert stop_exit.trigger_price == Decimal("100.00")
    assert trailing_exit.trigger_price == Decimal("100.00")
    assert breakeven_applied is True
    assert trailing_activated is True
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "100.00000000", "quantity": "1.00000000"}
    ]


def test_amend_short_trailing_stop_tightens_downward():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "tighten-short-trail",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)

    loosened = exchange.amend_bracket_trailing_stop("tighten-short-trail", Decimal("106"))
    amended = exchange.amend_bracket_trailing_stop("tighten-short-trail", Decimal("102"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    triggered = exchange.update_price("ETH/USDT", Decimal("102"))

    assert loosened is None
    assert amended is not None
    assert trailing_exit.trigger_price == Decimal("102.00")
    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "102.00000000", "quantity": "1.00000000"}
    ]


def test_tighten_short_trailing_stop_to_mark_derives_protective_trigger():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "trail-from-short-mark",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_amount": "4",
            "trailing_activation_pct": "3",
        },
        source="test",
    )
    engine.process_signal(signal)

    rejected = exchange.tighten_bracket_trailing_stop_to_mark("trail-from-short-mark", Decimal("98"))
    amended = exchange.tighten_bracket_trailing_stop_to_mark("trail-from-short-mark", Decimal("94"))
    trailing_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "trailing_stop")
    low_water_mark = exchange.lots[0].low_water_mark
    triggered = exchange.update_price("ETH/USDT", Decimal("98"))

    assert rejected is None
    assert amended is not None
    assert amended.exit_kind == "bracket_trailing_stop_mark_amend"
    assert trailing_exit.status == "open"
    assert trailing_exit.trigger_price == Decimal("98.00")
    assert low_water_mark == Decimal("94.00")
    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "trailing_stop", "price": "98.00000000", "quantity": "1.00000000"}
    ]


def test_amend_long_take_profit_only_moves_farther_into_profit():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "raise-long-target",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(signal)

    reduced_reward = exchange.amend_bracket_take_profit("raise-long-target", Decimal("108"))
    amended = exchange.amend_bracket_take_profit("raise-long-target", Decimal("112"))
    target_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "take_profit")
    triggered = exchange.update_price("BTC/USDT", Decimal("112"))

    assert reduced_reward is None
    assert amended is not None
    assert amended.exit_kind == "bracket_take_profit_amend"
    assert amended.status == "amended"
    assert target_exit.trigger_price == Decimal("112.00")
    assert triggered == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "112.00000000", "quantity": "1.00000000"}
    ]


def test_amend_short_take_profit_only_moves_farther_downward():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "lower-short-target",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(signal)

    reduced_reward = exchange.amend_bracket_take_profit("lower-short-target", Decimal("92"))
    amended = exchange.amend_bracket_take_profit("lower-short-target", Decimal("88"))
    target_exit = next(exit_order for exit_order in exchange.lots[0].exit_orders if exit_order.kind == "take_profit")
    triggered = exchange.update_price("ETH/USDT", Decimal("88"))

    assert reduced_reward is None
    assert amended is not None
    assert target_exit.trigger_price == Decimal("88.00")
    assert triggered == [
        {"symbol": "ETH/USDT", "kind": "take_profit", "price": "88.00000000", "quantity": "1.00000000"}
    ]


def test_close_bracket_at_protective_exit_uses_nearest_stop_or_trail():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    signal = normalize_signal(
        {
            "signal_id": "protective-close",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
        },
        source="test",
    )
    engine.process_signal(signal)
    exchange.update_price("BTC/USDT", Decimal("110"))

    order = exchange.close_bracket_at_protective_exit("protective-close")

    assert order is not None
    assert order.price == Decimal("104.50")
    assert order.exit_kind == "bracket_manual_close"
    assert order.reduce_only is True
    assert exchange.list_positions()[0]["quantity"] == "0.00000000"
    assert exchange.list_positions()[0]["realized_pnl"] == "4.50000000"


def test_short_open_notional_counts_against_exposure_cap():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    short_signal = normalize_signal(
        {
            "signal_id": "short-exposure",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    engine.process_signal(short_signal)

    assert exchange.open_notional() == Decimal("100")
    assert exchange.list_positions()[0]["quantity"] == "-1.00000000"


def test_plain_sell_does_not_disturb_open_short_lot():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)
    short_signal = normalize_signal(
        {
            "signal_id": "short-kept",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
        source="test",
    )
    plain_sell = normalize_signal(
        {
            "signal_id": "plain-sell",
            "symbol": "ETH/USDT",
            "side": "sell",
            "base_amount": "1",
            "price": "95",
        },
        source="test",
    )

    engine.process_signal(short_signal)
    engine.process_signal(plain_sell)

    assert exchange.list_positions()[0]["quantity"] == "-1.00000000"
    assert exchange.open_notional() == Decimal("100")
    assert len(exchange.lots) == 1
