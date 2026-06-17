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

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "data": self.data}


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
                INSERT OR REPLACE INTO signals (signal_id, payload)
                VALUES (?, ?)
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
                    INSERT OR REPLACE INTO signals (signal_id, payload)
                    VALUES (?, ?)
                    """,
                    (signal.signal_id, json.dumps(payload, sort_keys=True)),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def list_signals(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM signals ORDER BY rowid ASC").fetchall()
        return [json.loads(row["payload"]) for row in rows]

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
            rows = conn.execute("SELECT payload FROM pending_approvals ORDER BY rowid ASC").fetchall()
        return [_pending_summary(_signal_from_dict(json.loads(row["payload"]))) for row in rows]

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
                INSERT OR REPLACE INTO orders (order_id, signal_id, payload)
                VALUES (?, ?, ?)
                """,
                (order.order_id, order.signal_id, json.dumps(order.to_dict(), sort_keys=True)),
            )

    def list_orders(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM orders ORDER BY rowid ASC").fetchall()
        return [json.loads(row["payload"]) for row in rows]

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
                "SELECT event_type, payload FROM audit_events ORDER BY rowid ASC"
            ).fetchall()
        return [AuditEvent(event_type=row["event_type"], data=json.loads(row["payload"])) for row in rows]

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    payload TEXT NOT NULL
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
        "take_profit_pct": str(signal.take_profit_pct) if signal.take_profit_pct is not None else None,
        "leverage": str(signal.leverage),
        "max_slippage_bps": signal.max_slippage_bps,
        "strategy_id": signal.strategy_id,
    }


def _signal_from_dict(payload: dict[str, Any]) -> CryptoSignal:
    return normalize_signal(payload, source=str(payload["source"]))


def _pending_summary(signal: CryptoSignal) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "side": signal.side,
        "exchange": signal.exchange,
        "quote_amount": str(signal.quote_amount) if signal.quote_amount is not None else None,
        "base_amount": str(signal.base_amount) if signal.base_amount is not None else None,
        "price": str(signal.price) if signal.price is not None else None,
        "strategy_id": signal.strategy_id,
    }
