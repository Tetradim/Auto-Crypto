from decimal import Decimal

from autocrypto.engine import TradingEngine
from autocrypto.execution import PaperExchange
from autocrypto.signals import normalize_signal


def test_paper_exchange_tracks_weighted_average_position_and_realized_pnl():
    exchange = PaperExchange()
    engine = TradingEngine(exchange=exchange)

    first_buy = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50",
            "stop_loss_pct": "2",
        },
        source="test",
    )
    second_buy = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "150",
            "price": "75",
            "stop_loss_pct": "2",
        },
        source="test",
    )
    sell = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "sell",
            "base_amount": "1",
            "price": "80",
        },
        source="test",
    )

    engine.process_signal(first_buy)
    engine.process_signal(second_buy)
    result = engine.process_signal(sell)

    assert result.status == "accepted"
    position = exchange.list_positions()[0]
    assert position["symbol"] == "BTC/USDT"
    assert position["quantity"] == "3.00000000"
    assert position["avg_entry"] == "62.50000000"
    assert position["realized_pnl"] == "17.50000000"

