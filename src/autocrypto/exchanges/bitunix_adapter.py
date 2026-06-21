from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .ccxt_adapter import ExchangeCapabilities


BITUNIX_FUTURES_BASE_URL = "https://fapi.bitunix.com"
BITUNIX_SPOT_BASE_URL = "https://openapi.bitunix.com"


class BitunixConfigurationError(RuntimeError):
    """Raised when Bitunix credentials are required but missing."""


class BitunixRequestError(RuntimeError):
    """Raised when Bitunix returns an HTTP or transport error."""


@dataclass(frozen=True)
class BitunixCredentials:
    api_key: str
    secret_key: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)


class BitunixRestClient:
    """Small native Bitunix REST client for capability and account checks.

    Live order placement is intentionally not exposed here yet. The adapter
    verifies credentials and market/account connectivity behind the same
    exchange boundary the rest of Auto-Crypto already uses.
    """

    def __init__(
        self,
        *,
        credentials: BitunixCredentials | None = None,
        base_url: str = BITUNIX_FUTURES_BASE_URL,
        signing_style: str = "futures",
        nonce_factory: Callable[[], str] | None = None,
        clock_ms: Callable[[], int] | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.signing_style = signing_style
        self.nonce_factory = nonce_factory or (lambda: uuid.uuid4().hex)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.opener = opener or urlopen

    def capabilities(self) -> ExchangeCapabilities:
        return ExchangeCapabilities(
            exchange_id="bitunix",
            spot=True,
            margin=False,
            swap=True,
            future=True,
            option=False,
            create_order=True,
            cancel_order=True,
            fetch_balance=True,
            attached_stop_loss_take_profit=False,
            oco_order=False,
            trailing_order=False,
            reduce_only=True,
        )

    def get_futures_trading_pairs(self, symbols: str | None = None) -> dict[str, Any]:
        query = {"symbols": symbols} if symbols else None
        return self.request_json("GET", "/api/v1/futures/market/trading_pairs", query=query)

    def get_futures_tickers(self, symbols: str | None = None) -> dict[str, Any]:
        query = {"symbols": symbols} if symbols else None
        return self.request_json("GET", "/api/v1/futures/market/tickers", query=query)

    def get_futures_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int | None = None,
        price_type: str | None = None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"symbol": symbol.upper(), "interval": interval}
        if start_time is not None:
            query["startTime"] = start_time
        if end_time is not None:
            query["endTime"] = end_time
        if limit is not None:
            query["limit"] = limit
        if price_type:
            query["type"] = price_type.upper()
        return self.request_json("GET", "/api/v1/futures/market/kline", query=query)

    def get_futures_account(self, margin_coin: str = "USDT") -> dict[str, Any]:
        return self.request_json(
            "GET",
            "/api/v1/futures/account",
            query={"marginCoin": margin_coin.upper()},
            signed=True,
        )

    def get_pending_positions(self, symbol: str | None = None) -> dict[str, Any]:
        query = {"symbol": symbol.upper()} if symbol else None
        return self.request_json("GET", "/api/v1/futures/position/get_pending_positions", query=query, signed=True)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | list[Any] | None = None,
        signed: bool = False,
        timeout: int = 10,
    ) -> dict[str, Any]:
        request = self.build_request(method, path, query=query, body=body, signed=signed)
        try:
            with self.opener(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BitunixRequestError(f"Bitunix HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise BitunixRequestError(f"Bitunix request failed: {exc.reason}") from exc

        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BitunixRequestError("Bitunix returned non-JSON response") from exc
        if not isinstance(decoded, dict):
            raise BitunixRequestError("Bitunix returned unexpected JSON response")
        return decoded

    def build_request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | list[Any] | None = None,
        signed: bool = False,
    ) -> Request:
        method = method.upper()
        body_string = compact_json(body) if body is not None else ""
        query_string = urlencode(sorted((str(key), str(value)) for key, value in (query or {}).items()))
        url = f"{self.base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        data = body_string.encode("utf-8") if method in {"POST", "PUT", "DELETE"} and body is not None else None
        headers = {"Content-Type": "application/json", "language": "en-US"}
        if signed:
            headers.update(self.signed_headers(query or {}, body_string))
        return Request(url=url, data=data, headers=headers, method=method)

    def signed_headers(self, query: Mapping[str, Any] | None = None, body_string: str = "") -> dict[str, str]:
        if self.credentials is None or not self.credentials.configured:
            raise BitunixConfigurationError("Bitunix API key and secret are required for private requests")

        nonce = self.nonce_factory()
        timestamp = str(self.clock_ms())
        canonical_query = canonical_rest_query(query or {}, style=self.signing_style)
        signature = build_rest_signature(
            api_key=self.credentials.api_key,
            secret_key=self.credentials.secret_key,
            nonce=nonce,
            timestamp=timestamp,
            query_params=canonical_query,
            body=body_string,
        )
        return {
            "api-key": self.credentials.api_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": signature,
        }


def load_bitunix_credentials_from_env() -> BitunixCredentials | None:
    api_key = _empty_to_none(os.getenv("AUTO_CRYPTO_BITUNIX_API_KEY"))
    secret_key = _empty_to_none(os.getenv("AUTO_CRYPTO_BITUNIX_SECRET_KEY"))
    if not api_key and not secret_key:
        return None
    return BitunixCredentials(api_key=api_key or "", secret_key=secret_key or "")


def bitunix_credentials_configured() -> bool:
    credentials = load_bitunix_credentials_from_env()
    return bool(credentials and credentials.configured)


def bitunix_live_execution_enabled() -> bool:
    return os.getenv("AUTO_CRYPTO_BITUNIX_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def compact_json(payload: Mapping[str, Any] | list[Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def canonical_rest_query(query: Mapping[str, Any], *, style: str = "futures") -> str:
    if not query:
        return ""
    separator = "=" if style == "spot" else ""
    return "".join(f"{key}{separator}{value}" for key, value in sorted((str(k), str(v)) for k, v in query.items()))


def build_rest_signature(
    *,
    api_key: str,
    secret_key: str,
    nonce: str,
    timestamp: str,
    query_params: str = "",
    body: str = "",
) -> str:
    digest = _sha256_hex(f"{nonce}{timestamp}{api_key}{query_params}{body}")
    return _sha256_hex(f"{digest}{secret_key}")


def build_websocket_signature(
    *,
    api_key: str,
    secret_key: str,
    nonce: str,
    timestamp: str,
    params: Mapping[str, Any] | None = None,
    include_auth_fields: bool = True,
) -> str:
    params = dict(params or {})
    if include_auth_fields:
        params.update({"apiKey": api_key, "nonce": nonce, "timestamp": timestamp})
    canonical_params = "".join(f"{key}{value}" for key, value in sorted((str(k), str(v)) for k, v in params.items()))
    digest = _sha256_hex(f"{nonce}{timestamp}{api_key}{canonical_params}")
    return _sha256_hex(f"{digest}{secret_key}")


def bitunix_kline_candles(payload: Mapping[str, Any]) -> list[dict[str, Decimal | str | None]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise BitunixRequestError("Bitunix kline response missing data list")
    candles: list[dict[str, Decimal | str | None]] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise BitunixRequestError("Bitunix kline entry must be an object")
        try:
            high = _decimal_field(item, "high")
            low = _decimal_field(item, "low")
            close = _decimal_field(item, "close")
            open_price = _optional_decimal_field(item, "open")
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BitunixRequestError("Bitunix kline entry contains an invalid price") from exc
        if low > high:
            raise BitunixRequestError("Bitunix kline low cannot exceed high")
        label = item.get("time") or item.get("timestamp")
        candles.append(
            {
                "label": str(label) if label is not None else None,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
            }
        )
    return candles


def _decimal_field(item: Mapping[str, Any], field_name: str) -> Decimal:
    value = item.get(field_name)
    if value in (None, ""):
        raise ValueError(f"missing {field_name}")
    parsed = Decimal(str(value))
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _optional_decimal_field(item: Mapping[str, Any], field_name: str) -> Decimal | None:
    value = item.get(field_name)
    if value in (None, ""):
        return None
    parsed = Decimal(str(value))
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
