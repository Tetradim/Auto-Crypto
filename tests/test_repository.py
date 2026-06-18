import json
import sqlite3
from decimal import Decimal

from autocrypto.execution import ExitOrder, PaperOrder
from autocrypto.repository import SQLiteRepository
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

    persisted_signal = reopened.list_signals()[0]
    assert persisted_signal["signal_id"] == signal.signal_id
    assert persisted_signal["created_at"]
    assert reopened.list_orders()[0]["order_id"] == "paper-1"
    assert reopened.list_orders()[0]["exit_orders"][0]["kind"] == "stop_loss"
    audit_event = reopened.list_audit()[0]
    assert audit_event.event_type == "order.accepted"
    assert audit_event.data == {"order_id": "paper-1"}
    assert audit_event.created_at


def test_sqlite_repository_claims_signal_once(tmp_path):
    repo = SQLiteRepository(tmp_path / "claims.sqlite3")
    signal = normalize_signal(
        {
            "signal_id": "duplicate-safe",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "50000",
            "stop_loss_pct": "2",
        },
        source="test",
    )

    assert repo.claim_signal(signal) is True
    assert repo.claim_signal(signal) is False
    assert len(repo.list_signals()) == 1


def test_sqlite_repository_persists_and_pops_pending_approval(tmp_path):
    repo = SQLiteRepository(tmp_path / "pending.sqlite3")
    signal = normalize_signal(
        {
            "signal_id": "needs-review",
            "symbol": "ETH/USDT",
            "side": "buy",
            "quote_amount": "40",
            "price": "3000",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
        },
        source="test",
    )

    repo.save_pending_approval(signal)
    reopened = SQLiteRepository(repo.path)

    pending = reopened.list_pending_approvals()
    assert pending == [
        {
            "signal_id": "needs-review",
            "source": "test",
            "symbol": "ETH/USDT",
            "side": "buy",
            "exchange": "paper",
            "quote_amount": "40",
            "base_amount": None,
            "price": "3000",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
            "strategy_id": "manual",
            "created_at": pending[0]["created_at"],
        }
    ]
    assert pending[0]["created_at"]

    popped = reopened.pop_pending_approval("needs-review")

    assert popped is not None
    assert popped.signal_id == "needs-review"
    assert popped.symbol == "ETH/USDT"
    assert reopened.pop_pending_approval("needs-review") is None
    assert reopened.list_pending_approvals() == []


def test_sqlite_repository_backfills_idempotency_claims_from_existing_orders(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    signal = normalize_signal(
        {
            "signal_id": "already-executed",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "50000",
            "stop_loss_pct": "2",
        },
        source="test",
    )
    order = PaperOrder(
        order_id="paper-already-executed",
        signal_id=signal.signal_id,
        mode="paper",
        exchange="paper",
        symbol="BTC/USDT",
        side="buy",
        notional=Decimal("25"),
        price=Decimal("50000"),
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE signals (
                signal_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );

            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE audit_events (
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO signals (signal_id, payload) VALUES (?, ?)",
            (signal.signal_id, json.dumps(signal.raw_payload, sort_keys=True)),
        )
        conn.execute(
            "INSERT INTO orders (order_id, signal_id, payload) VALUES (?, ?, ?)",
            (order.order_id, order.signal_id, json.dumps(order.to_dict(), sort_keys=True)),
        )

    repo = SQLiteRepository(db_path)

    assert repo.claim_signal(signal) is False
    assert repo.list_signals()[0]["created_at"]
