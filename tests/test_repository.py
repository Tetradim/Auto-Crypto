from decimal import Decimal

from autocrypto.execution import ExitOrder, PaperOrder
from autocrypto.repository import AuditEvent, SQLiteRepository
from autocrypto.signals import normalize_signal


def test_sqlite_repository_persists_signals_orders_and_audit_events(tmp_path):
    db_path = tmp_path / "auto_crypto.sqlite3"
    repo = SQLiteRepository(db_path)
    signal = normalize_signal(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "50000",
            "stop_loss_pct": "2",
        },
        source="test",
    )
    order = PaperOrder(
        order_id="paper-1",
        signal_id=signal.signal_id,
        mode="paper",
        exchange="paper",
        symbol="BTC/USDT",
        side="buy",
        notional=Decimal("25"),
        price=Decimal("50000"),
        exit_orders=[ExitOrder(kind="stop_loss", trigger_price=Decimal("49000.00"))],
    )

    repo.save_signal(signal)
    repo.save_order(order)
    repo.record_audit("order.accepted", {"order_id": order.order_id})

    reopened = SQLiteRepository(db_path)

    assert reopened.list_signals()[0]["signal_id"] == signal.signal_id
    assert reopened.list_orders()[0]["order_id"] == "paper-1"
    assert reopened.list_orders()[0]["exit_orders"][0]["kind"] == "stop_loss"
    assert reopened.list_audit()[0] == AuditEvent(
        event_type="order.accepted",
        data={"order_id": "paper-1"},
    )

