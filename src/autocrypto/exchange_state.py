from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from .exchange_adapters import ExchangeAdapterStatus, generic_adapter_status, paper_adapter_status
from .exchanges.bitunix_adapter import (
    BitunixRestClient,
    bitunix_credentials_configured,
    bitunix_live_execution_enabled,
    load_bitunix_credentials_from_env,
)
from .exchanges.ccxt_adapter import CcxtExchangeAdapter, CcxtNotInstalledError, ExchangeCapabilities
from .exchanges.platform_registry import get_platform, platform_rows
from .execution import PaperExchange


def paper_capabilities() -> ExchangeCapabilities:
    return ExchangeCapabilities(
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


def capabilities_for_exchange(exchange_id: str) -> ExchangeCapabilities:
    normalized = exchange_id.strip().lower()
    if normalized == "paper":
        return paper_capabilities()
    if normalized == "bitunix":
        return BitunixRestClient(credentials=load_bitunix_credentials_from_env()).capabilities()
    platform = get_platform(normalized)
    if platform and platform.ccxt_id:
        normalized = platform.ccxt_id
    return CcxtExchangeAdapter(normalized).capabilities()


def exchange_rows(ccxt_exchange_ids: Iterable[str]) -> list[dict[str, Any]]:
    rows = [_exchange_row("paper", "paper"), bitunix_exchange_row()]
    rows.extend(_exchange_row(exchange_id, "ccxt") for exchange_id in ccxt_exchange_ids)
    return rows


def platform_state_rows(ccxt_exchange_ids: set[str] | None) -> list[dict[str, Any]]:
    return platform_rows(ccxt_exchange_ids)


def adapter_status_for_exchange(
    exchange_id: str,
    paper_exchange: PaperExchange,
    *,
    equity: Decimal,
) -> ExchangeAdapterStatus:
    normalized = exchange_id.strip().lower()
    if normalized == "paper":
        return paper_adapter_status(
            paper_exchange,
            paper_capabilities(),
            equity=equity,
        )
    if normalized == "bitunix":
        capabilities = BitunixRestClient(credentials=load_bitunix_credentials_from_env()).capabilities()
        return generic_adapter_status(
            exchange_id="bitunix",
            driver="bitunix-native",
            capabilities=capabilities,
            live_execution_enabled=bitunix_live_execution_enabled(),
            funding_supported=True,
        )
    platform = get_platform(normalized)
    ccxt_exchange_id = platform.ccxt_id if platform and platform.ccxt_id else normalized
    capabilities = CcxtExchangeAdapter(ccxt_exchange_id).capabilities()
    return generic_adapter_status(
        exchange_id=ccxt_exchange_id,
        driver="ccxt",
        capabilities=capabilities,
        live_execution_enabled=False,
        funding_supported=capabilities.swap or capabilities.future,
    )


def exchange_integration_payload(exchange_id: str, ccxt_exchange_ids: set[str] | None) -> dict[str, Any]:
    platform = get_platform(exchange_id)
    if platform is None:
        raise ValueError(f"unsupported platform: {exchange_id}")

    payload: dict[str, Any] = {"platform": platform.to_dict(ccxt_exchange_ids=ccxt_exchange_ids)}
    if platform.exchange_id == "bitunix":
        payload["capabilities"] = BitunixRestClient(
            credentials=load_bitunix_credentials_from_env()
        ).capabilities().to_dict()
        return payload
    if platform.ccxt_id and platform.driver_available(ccxt_exchange_ids):
        try:
            payload["capabilities"] = CcxtExchangeAdapter(platform.ccxt_id).capabilities().to_dict()
        except (CcxtNotInstalledError, ValueError) as exc:
            payload["capability_error"] = str(exc)
    return payload


def bitunix_exchange_row() -> dict[str, Any]:
    return {
        "exchange_id": "bitunix",
        "driver": "bitunix-native",
        "driver_available": True,
        "credentials_configured": bitunix_credentials_configured(),
        "live_execution_enabled": bitunix_live_execution_enabled(),
    }


def _exchange_row(exchange_id: str, driver: str) -> dict[str, Any]:
    return {
        "exchange_id": exchange_id,
        "driver": driver,
        "driver_available": True,
        "credentials_configured": False,
        "live_execution_enabled": False,
    }
