from decimal import Decimal

from autocrypto.engine import TradingEngine
from autocrypto.execution import PaperExchange
from autocrypto.idempotency import InMemoryIdempotencyStore
from autocrypto.risk import AccountState, RiskConfig
from autocrypto.signals import normalize_signal


def test_engine_executes_approved_signal_in_paper_with_bracket_exits():
    exchange = PaperExchange()
    engine = TradingEngine(
        exchange=exchange,
        risk_config=RiskConfig(max_order_notional=Decimal("500")),
        account_state=AccountState(equity=Decimal("10000")),
        idempotency=InMemoryIdempotencyStore(),
    )
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "200",
            "price": "50000",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
            "trailing_stop_pct": "3",
            "strategy_id": "breakout",
        },
        source="test",
    )

    result = engine.process_signal(signal)

    assert result.status == "accepted"
    assert result.order is not None
    assert result.order.mode == "paper"
    assert result.order.symbol == "BTC/USDT"
    assert result.order.notional == Decimal("200")
    assert [exit_order.kind for exit_order in result.order.exit_orders] == ["stop_loss", "take_profit", "trailing_stop"]
    assert result.order.exit_orders[0].trigger_price == Decimal("49000.00")
    assert result.order.exit_orders[1].trigger_price == Decimal("52000.00")
    assert result.order.exit_orders[2].trigger_price == Decimal("48500.00")
    assert result.order.trailing_stop_pct == Decimal("3")


def test_engine_blocks_duplicate_signal_before_second_order():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange, risk_config=RiskConfig(), idempotency=InMemoryIdempotencyStore())
    signal = normalize_signal(
        {
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "50",
            "price": "3000",
            "stop_loss_pct": "2",
        },
        source="test",
    )

    first = engine.process_signal(signal)
    second = engine.process_signal(signal)

    assert first.status == "accepted"
    assert second.status == "duplicate"
    assert len(exchange.orders) == 1


def test_engine_updates_open_notional_and_blocks_cumulative_exposure():
    account = AccountState(open_notional=Decimal("0"))
    engine = TradingEngine(
        exchange=PaperExchange(),
        risk_config=RiskConfig(max_order_notional=Decimal("500"), max_open_notional=Decimal("150")),
        account_state=account,
        idempotency=InMemoryIdempotencyStore(),
    )
    first_signal = normalize_signal(
        {
            "signal_id": "first-exposure",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
        },
        source="test",
    )
    second_signal = normalize_signal(
        {
            "signal_id": "second-exposure",
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "75",
            "price": "3000",
            "stop_loss_pct": "2",
        },
        source="test",
    )

    first = engine.process_signal(first_signal)
    second = engine.process_signal(second_signal)

    assert first.status == "accepted"
    assert account.open_notional == Decimal("100")
    assert second.status == "rejected"
    assert "max_open_notional_exceeded" in second.decision.reason_codes
    assert len(engine.exchange.orders) == 1


def test_engine_recomputes_open_notional_after_partial_sell_at_exit_price():
    account = AccountState(open_notional=Decimal("0"))
    engine = TradingEngine(
        exchange=PaperExchange(),
        risk_config=RiskConfig(max_order_notional=Decimal("1000"), max_open_notional=Decimal("1000")),
        account_state=account,
        idempotency=InMemoryIdempotencyStore(),
    )
    buy_signal = normalize_signal(
        {
            "signal_id": "partial-sell-entry",
            "symbol": "SOL/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50",
            "stop_loss_pct": "2",
        },
        source="test",
    )
    sell_signal = normalize_signal(
        {
            "signal_id": "partial-sell-exit",
            "symbol": "SOL/USDT",
            "side": "sell",
            "base_amount": "1",
            "price": "60",
        },
        source="test",
    )

    buy = engine.process_signal(buy_signal)
    sell = engine.process_signal(sell_signal)

    assert buy.status == "accepted"
    assert sell.status == "accepted"
    assert engine.exchange.open_notional() == Decimal("50")
    assert account.open_notional == Decimal("50")
