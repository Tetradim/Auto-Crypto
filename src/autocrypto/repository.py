from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .execution import PaperOrder
from .signals import CryptoSignal, normalize_signal


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    data: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "data": self.data, "created_at": self.created_at}


class SQLiteRepository:
    """Small SQLite repository for signals, orders, and audit events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def save_signal(self, signal: CryptoSignal) -> None:
        payload = _signal_to_dict(signal)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO signals (signal_id, payload, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(signal_id) DO UPDATE SET payload = excluded.payload
                """,
                (signal.signal_id, json.dumps(payload, sort_keys=True)),
            )

    def claim_signal(self, signal: CryptoSignal) -> bool:
        payload = _signal_to_dict(signal)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO signal_claims (signal_id)
                    VALUES (?)
                    """,
                    (signal.signal_id,),
                )
                conn.execute(
                    """
                    INSERT INTO signals (signal_id, payload, created_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(signal_id) DO UPDATE SET payload = excluded.payload
                    """,
                    (signal.signal_id, json.dumps(payload, sort_keys=True)),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def list_signals(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload, created_at FROM signals ORDER BY rowid ASC").fetchall()
        signals = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["created_at"] = row["created_at"]
            signals.append(payload)
        return signals

    def save_pending_approval(self, signal: CryptoSignal) -> None:
        payload = _signal_to_dict(signal)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_approvals (signal_id, payload)
                VALUES (?, ?)
                """,
                (signal.signal_id, json.dumps(payload, sort_keys=True)),
            )

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload, created_at FROM pending_approvals ORDER BY rowid ASC"
            ).fetchall()
        return [
            _pending_summary(_signal_from_dict(json.loads(row["payload"])), created_at=row["created_at"])
            for row in rows
        ]

    def get_pending_approval(self, signal_id: str) -> CryptoSignal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM pending_approvals WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()
        return _signal_from_dict(json.loads(row["payload"])) if row else None

    def pop_pending_approval(self, signal_id: str) -> CryptoSignal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM pending_approvals WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM pending_approvals WHERE signal_id = ?", (signal_id,))
        return _signal_from_dict(json.loads(row["payload"]))

    def save_order(self, order: PaperOrder) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orders (order_id, signal_id, payload, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(order_id) DO UPDATE SET
                    signal_id = excluded.signal_id,
                    payload = excluded.payload
                """,
                (order.order_id, order.signal_id, json.dumps(order.to_dict(), sort_keys=True)),
            )

    def list_orders(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload, created_at FROM orders ORDER BY rowid ASC").fetchall()
        orders = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["created_at"] = row["created_at"]
            orders.append(payload)
        return orders

    def record_audit(self, event_type: str, data: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (event_type, payload)
                VALUES (?, ?)
                """,
                (event_type, json.dumps(data, sort_keys=True)),
            )

    def list_audit(self) -> list[AuditEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, payload, created_at FROM audit_events ORDER BY rowid ASC"
            ).fetchall()
        return [
            AuditEvent(
                event_type=row["event_type"],
                data=json.loads(row["payload"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS signal_claims (
                    signal_id TEXT PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS pending_approvals (
                    signal_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                INSERT OR IGNORE INTO signal_claims (signal_id)
                SELECT DISTINCT signal_id FROM orders;
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
            if "created_at" not in columns:
                conn.execute("ALTER TABLE signals ADD COLUMN created_at TEXT")
            conn.execute("UPDATE signals SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
            order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
            if "created_at" not in order_columns:
                conn.execute("ALTER TABLE orders ADD COLUMN created_at TEXT")
            conn.execute("UPDATE orders SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _signal_to_dict(signal: CryptoSignal) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "source": signal.source,
        "symbol": signal.symbol,
        "side": signal.side,
        "exchange": signal.exchange,
        "market_type": signal.market_type,
        "quote_amount": str(signal.quote_amount) if signal.quote_amount is not None else None,
        "base_amount": str(signal.base_amount) if signal.base_amount is not None else None,
        "price": str(signal.price) if signal.price is not None else None,
        "stop_loss_pct": str(signal.stop_loss_pct) if signal.stop_loss_pct is not None else None,
        "stop_loss_price": str(signal.stop_loss_price) if signal.stop_loss_price is not None else None,
        "take_profit_pct": str(signal.take_profit_pct) if signal.take_profit_pct is not None else None,
        "take_profit_price": str(signal.take_profit_price) if signal.take_profit_price is not None else None,
        "take_profit_targets": [
            {
                "pct": str(target.pct) if target.pct is not None else None,
                "trigger_price": str(target.trigger_price) if target.trigger_price is not None else None,
                "close_pct": str(target.close_pct),
            }
            for target in signal.take_profit_targets
        ],
        "trailing_stop_pct": str(signal.trailing_stop_pct) if signal.trailing_stop_pct is not None else None,
        "trailing_stop_amount": str(signal.trailing_stop_amount) if signal.trailing_stop_amount is not None else None,
        "trailing_stop_price": str(signal.trailing_stop_price) if signal.trailing_stop_price is not None else None,
        "trailing_step_pct": str(signal.trailing_step_pct) if signal.trailing_step_pct is not None else None,
        "trailing_step_amount": str(signal.trailing_step_amount) if signal.trailing_step_amount is not None else None,
        "trailing_activation_pct": str(signal.trailing_activation_pct)
        if signal.trailing_activation_pct is not None
        else None,
        "trailing_activation_price": str(signal.trailing_activation_price)
        if signal.trailing_activation_price is not None
        else None,
        "breakeven_trigger_pct": str(signal.breakeven_trigger_pct)
        if signal.breakeven_trigger_pct is not None
        else None,
        "leverage": str(signal.leverage),
        "max_slippage_bps": signal.max_slippage_bps,
        "reduce_only": signal.reduce_only,
        "strategy_id": signal.strategy_id,
    }


def _signal_from_dict(payload: dict[str, Any]) -> CryptoSignal:
    return normalize_signal(payload, source=str(payload["source"]))


def _pending_summary(signal: CryptoSignal, *, created_at: str | None = None) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "source": signal.source,
        "symbol": signal.symbol,
        "side": signal.side,
        "exchange": signal.exchange,
        "quote_amount": str(signal.quote_amount) if signal.quote_amount is not None else None,
        "base_amount": str(signal.base_amount) if signal.base_amount is not None else None,
        "price": str(signal.price) if signal.price is not None else None,
        "stop_loss_pct": str(signal.stop_loss_pct) if signal.stop_loss_pct is not None else None,
        "stop_loss_price": str(signal.stop_loss_price) if signal.stop_loss_price is not None else None,
        "take_profit_pct": str(signal.take_profit_pct) if signal.take_profit_pct is not None else None,
        "take_profit_price": str(signal.take_profit_price) if signal.take_profit_price is not None else None,
        "take_profit_targets": [
            {
                "pct": str(target.pct) if target.pct is not None else None,
                "trigger_price": str(target.trigger_price) if target.trigger_price is not None else None,
                "close_pct": str(target.close_pct),
            }
            for target in signal.take_profit_targets
        ],
        "trailing_stop_pct": str(signal.trailing_stop_pct) if signal.trailing_stop_pct is not None else None,
        "trailing_stop_amount": str(signal.trailing_stop_amount) if signal.trailing_stop_amount is not None else None,
        "trailing_stop_price": str(signal.trailing_stop_price) if signal.trailing_stop_price is not None else None,
        "trailing_step_pct": str(signal.trailing_step_pct) if signal.trailing_step_pct is not None else None,
        "trailing_step_amount": str(signal.trailing_step_amount) if signal.trailing_step_amount is not None else None,
        "trailing_activation_pct": str(signal.trailing_activation_pct)
        if signal.trailing_activation_pct is not None
        else None,
        "trailing_activation_price": str(signal.trailing_activation_price)
        if signal.trailing_activation_price is not None
        else None,
        "breakeven_trigger_pct": str(signal.breakeven_trigger_pct)
        if signal.breakeven_trigger_pct is not None
        else None,
        "strategy_id": signal.strategy_id,
        "reduce_only": signal.reduce_only,
        "created_at": created_at,
    }
