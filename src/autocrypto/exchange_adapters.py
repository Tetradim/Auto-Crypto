from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .execution import PaperExchange
from .exchanges.ccxt_adapter import ExchangeCapabilities


@dataclass(frozen=True)
class AdapterBalance:
    asset: str
    total: Decimal
    available: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {"asset": self.asset, "total": _plain(self.total), "available": _plain(self.available)}


@dataclass(frozen=True)
class AdapterFunding:
    supported: bool
    rate_bps: Decimal | None = None
    next_funding_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "rate_bps": _plain(self.rate_bps) if self.rate_bps is not None else None,
            "next_funding_at": self.next_funding_at,
        }


@dataclass(frozen=True)
class AdapterSymbolFilter:
    symbol: str
    min_notional: Decimal
    price_increment: Decimal
    quantity_increment: Decimal
    max_leverage: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "min_notional": _plain(self.min_notional),
            "price_increment": _plain(self.price_increment),
            "quantity_increment": _plain(self.quantity_increment),
            "max_leverage": _plain(self.max_leverage),
        }


@dataclass(frozen=True)
class AdapterReconciliation:
    status: str
    reason: str = ""
    open_order_count: int = 0
    position_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "open_order_count": self.open_order_count,
            "position_count": self.position_count,
        }


@dataclass(frozen=True)
class ExchangeAdapterStatus:
    exchange_id: str
    driver: str
    live_execution_enabled: bool
    capabilities: ExchangeCapabilities
    balances: tuple[AdapterBalance, ...] = field(default_factory=tuple)
    positions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    funding: AdapterFunding = field(default_factory=lambda: AdapterFunding(supported=False))
    symbol_filters: tuple[AdapterSymbolFilter, ...] = field(default_factory=tuple)
    reconciliation: AdapterReconciliation = field(
        default_factory=lambda: AdapterReconciliation(status="unknown")
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "driver": self.driver,
            "live_execution_enabled": self.live_execution_enabled,
            "capabilities": self.capabilities.to_dict(),
            "balances": [balance.to_dict() for balance in self.balances],
            "positions": list(self.positions),
            "funding": self.funding.to_dict(),
            "symbol_filters": [symbol_filter.to_dict() for symbol_filter in self.symbol_filters],
            "reconciliation": self.reconciliation.to_dict(),
        }


def paper_adapter_status(
    exchange: PaperExchange,
    capabilities: ExchangeCapabilities,
    *,
    equity: Decimal = Decimal("10000"),
) -> ExchangeAdapterStatus:
    positions = tuple(exchange.list_positions())
    return ExchangeAdapterStatus(
        exchange_id="paper",
        driver="paper",
        live_execution_enabled=False,
        capabilities=capabilities,
        balances=(AdapterBalance(asset="USDT", total=equity, available=equity - exchange.open_notional()),),
        positions=positions,
        funding=AdapterFunding(supported=False),
        symbol_filters=(
            AdapterSymbolFilter(
                symbol="BTC/USDT",
                min_notional=Decimal("1"),
                price_increment=Decimal("0.01"),
                quantity_increment=Decimal("0.00000001"),
                max_leverage=Decimal("1"),
            ),
            AdapterSymbolFilter(
                symbol="ETH/USDT",
                min_notional=Decimal("1"),
                price_increment=Decimal("0.01"),
                quantity_increment=Decimal("0.00000001"),
                max_leverage=Decimal("1"),
            ),
            AdapterSymbolFilter(
                symbol="SOL/USDT",
                min_notional=Decimal("1"),
                price_increment=Decimal("0.01"),
                quantity_increment=Decimal("0.00000001"),
                max_leverage=Decimal("1"),
            ),
        ),
        reconciliation=AdapterReconciliation(
            status="paper_only",
            reason="paper exchange state is local and reconstructed from repository orders",
            open_order_count=len(exchange.lots),
            position_count=len(positions),
        ),
    )


def generic_adapter_status(
    *,
    exchange_id: str,
    driver: str,
    capabilities: ExchangeCapabilities,
    live_execution_enabled: bool = False,
    funding_supported: bool = False,
) -> ExchangeAdapterStatus:
    return ExchangeAdapterStatus(
        exchange_id=exchange_id,
        driver=driver,
        live_execution_enabled=live_execution_enabled,
        capabilities=capabilities,
        funding=AdapterFunding(supported=funding_supported),
        reconciliation=AdapterReconciliation(
            status="not_reconciled",
            reason="private balance, position, and order reconciliation is not enabled for this adapter",
        ),
    )


def _plain(value: Decimal) -> str:
    return format(value, "f")
