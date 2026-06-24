from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..config import live_execution_enabled_from_env


@dataclass(frozen=True)
class CredentialField:
    env_var: str
    label: str
    secret: bool = True

    def to_dict(self) -> dict[str, Any]:
        value = os.getenv(self.env_var)
        return {
            "env_var": self.env_var,
            "label": self.label,
            "secret": self.secret,
            "configured": bool(value and value.strip()),
        }


@dataclass(frozen=True)
class TradingPlatform:
    exchange_id: str
    display_name: str
    priority: int
    tier: str
    driver: str
    docs_url: str
    ccxt_id: str | None = None
    region: str = "global"
    market_types: tuple[str, ...] = ("spot",)
    api_coverage: tuple[str, ...] = ("rest", "websocket")
    credentials: tuple[CredentialField, ...] = ()
    live_enabled_env: str | None = None
    priority_assets: tuple[str, ...] = ()
    default_symbols: tuple[str, ...] = ()
    sandbox: str | None = None
    required_permissions: tuple[str, ...] = ()
    adapter_scope: tuple[str, ...] = ("capability_metadata", "non_executing_exchange_plan")
    notes: str = ""

    def credentials_configured(self) -> bool:
        if not self.credentials:
            return False
        return all(bool(os.getenv(field.env_var, "").strip()) for field in self.credentials)

    def live_execution_enabled(self) -> bool:
        if not self.live_enabled_env:
            return False
        return live_execution_enabled_from_env(self.live_enabled_env)

    def driver_available(self, ccxt_exchange_ids: set[str] | None = None) -> bool:
        if self.driver == "native-bitunix":
            return True
        if self.driver == "ccxt":
            return bool(ccxt_exchange_ids is not None and self.ccxt_id in ccxt_exchange_ids)
        return False

    def integration_status(self, ccxt_exchange_ids: set[str] | None = None) -> str:
        if self.driver_available(ccxt_exchange_ids):
            return "adapter_ready"
        if self.driver == "ccxt":
            return "install_ccxt" if ccxt_exchange_ids is None else "ccxt_not_found"
        return "native_adapter_needed"

    def to_dict(self, *, ccxt_exchange_ids: set[str] | None = None) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "display_name": self.display_name,
            "priority": self.priority,
            "tier": self.tier,
            "driver": self.driver,
            "driver_available": self.driver_available(ccxt_exchange_ids),
            "integration_status": self.integration_status(ccxt_exchange_ids),
            "ccxt_id": self.ccxt_id,
            "region": self.region,
            "market_types": list(self.market_types),
            "api_coverage": list(self.api_coverage),
            "credentials_configured": self.credentials_configured(),
            "credential_fields": [field.to_dict() for field in self.credentials],
            "live_execution_enabled": self.live_execution_enabled(),
            "live_enabled_env": self.live_enabled_env,
            "priority_assets": list(self.priority_assets),
            "default_symbols": list(self.default_symbols),
            "sandbox": self.sandbox,
            "required_permissions": list(self.required_permissions),
            "adapter_scope": list(self.adapter_scope),
            "docs_url": self.docs_url,
            "notes": self.notes,
        }


def credential(prefix: str, name: str, label: str) -> CredentialField:
    return CredentialField(env_var=f"AUTO_CRYPTO_{prefix}_{name}", label=label)


def live_env(prefix: str) -> str:
    return f"AUTO_CRYPTO_{prefix}_LIVE_ENABLED"


SUPPORTED_PLATFORMS: tuple[TradingPlatform, ...] = (
    TradingPlatform(
        exchange_id="coinbase",
        display_name="Coinbase Advanced Trade",
        priority=1,
        tier="us_spot",
        driver="ccxt",
        ccxt_id="coinbase",
        region="United States",
        market_types=("spot", "us_derivatives", "international_derivatives"),
        api_coverage=("rest", "websocket", "orders", "accounts", "market_data"),
        credentials=(
            credential("COINBASE", "API_KEY_NAME", "API key name"),
            credential("COINBASE", "PRIVATE_KEY", "API private key"),
        ),
        live_enabled_env=live_env("COINBASE"),
        priority_assets=("BTC", "ETH", "SOL"),
        default_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        required_permissions=("view_accounts", "trade"),
        adapter_scope=("ccxt_discovery", "capability_metadata", "non_executing_exchange_plan", "native_adapter_planned"),
        docs_url="https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/introduction",
        notes="High-priority US venue; prefer native JWT adapter before live execution.",
    ),
    TradingPlatform(
        exchange_id="kraken",
        display_name="Kraken",
        priority=2,
        tier="us_spot_derivatives",
        driver="ccxt",
        ccxt_id="kraken",
        region="United States / global",
        market_types=("spot", "margin", "futures"),
        api_coverage=("rest", "websocket", "fix", "orders", "balances", "market_data"),
        credentials=(credential("KRAKEN", "API_KEY", "API key"), credential("KRAKEN", "API_SECRET", "API secret")),
        live_enabled_env=live_env("KRAKEN"),
        priority_assets=("BTC", "ETH", "SOL"),
        default_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        required_permissions=("query_funds", "query_open_orders", "create_modify_orders"),
        adapter_scope=("ccxt_discovery", "capability_metadata", "non_executing_exchange_plan"),
        docs_url="https://docs.kraken.com/api/",
        notes="Strong candidate for early native spot and futures adapters.",
    ),
    TradingPlatform(
        exchange_id="binanceus",
        display_name="Binance.US",
        priority=3,
        tier="us_spot",
        driver="ccxt",
        ccxt_id="binanceus",
        region="United States",
        market_types=("spot", "otc"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("BINANCEUS", "API_KEY", "API key"), credential("BINANCEUS", "API_SECRET", "API secret")),
        live_enabled_env=live_env("BINANCEUS"),
        priority_assets=("BTC", "ETH", "SOL"),
        default_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        required_permissions=("read", "spot_trade"),
        adapter_scope=("ccxt_discovery", "capability_metadata", "non_executing_exchange_plan"),
        docs_url="https://docs.binance.us/",
        notes="US-only Binance venue; spot trading must be enabled on the API key.",
    ),
    TradingPlatform(
        exchange_id="binance",
        display_name="Binance",
        priority=4,
        tier="global_spot_derivatives",
        driver="ccxt",
        ccxt_id="binance",
        market_types=("spot", "margin", "swap", "futures", "options"),
        api_coverage=("rest", "websocket", "fix", "orders", "balances", "market_data", "testnet"),
        credentials=(credential("BINANCE", "API_KEY", "API key"), credential("BINANCE", "API_SECRET", "API secret")),
        live_enabled_env=live_env("BINANCE"),
        priority_assets=("BTC", "ETH", "SOL"),
        default_symbols=("BTC/USDT", "ETH/USDT", "SOL/USDT"),
        sandbox="spot_testnet",
        required_permissions=("read", "spot_trade"),
        adapter_scope=("ccxt_discovery", "capability_metadata", "non_executing_exchange_plan", "sandbox_planned"),
        docs_url="https://developers.binance.com/docs/binance-spot-api-docs/rest-api",
        notes="Global Binance venue; use testnet/demo API surfaces before any live adapter review and check regional availability.",
    ),
    TradingPlatform(
        exchange_id="gemini",
        display_name="Gemini",
        priority=5,
        tier="us_spot",
        driver="ccxt",
        ccxt_id="gemini",
        region="United States",
        market_types=("spot",),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("GEMINI", "API_KEY", "API key"), credential("GEMINI", "API_SECRET", "API secret")),
        live_enabled_env=live_env("GEMINI"),
        priority_assets=("BTC", "ETH", "SOL"),
        default_symbols=("BTCUSD", "ETHUSD", "SOLUSD"),
        sandbox="sandbox_api",
        required_permissions=("trader",),
        adapter_scope=("ccxt_discovery", "capability_metadata", "non_executing_exchange_plan"),
        docs_url="https://developer.gemini.com/rest-api/",
        notes="US spot venue with straightforward account and order APIs; market-style execution should be modeled as protected limits.",
    ),
    TradingPlatform(
        exchange_id="bitstamp",
        display_name="Bitstamp",
        priority=6,
        tier="spot",
        driver="ccxt",
        ccxt_id="bitstamp",
        region="United States / Europe / global",
        market_types=("spot",),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(
            credential("BITSTAMP", "API_KEY", "API key"),
            credential("BITSTAMP", "API_SECRET", "API secret"),
            credential("BITSTAMP", "CUSTOMER_ID", "Customer ID"),
        ),
        live_enabled_env=live_env("BITSTAMP"),
        docs_url="https://www.bitstamp.net/api/",
        notes="Long-running BTC/USD and BTC/EUR venue.",
    ),
    TradingPlatform(
        exchange_id="alpaca",
        display_name="Alpaca Crypto",
        priority=7,
        tier="broker_spot",
        driver="ccxt",
        ccxt_id="alpaca",
        region="United States / selected jurisdictions",
        market_types=("spot",),
        api_coverage=("rest", "websocket", "orders", "assets", "market_data", "paper_trading"),
        credentials=(credential("ALPACA", "API_KEY_ID", "API key ID"), credential("ALPACA", "SECRET_KEY", "Secret key")),
        live_enabled_env=live_env("ALPACA"),
        docs_url="https://docs.alpaca.markets/us/docs/crypto-trading",
        notes="Broker-style API with paper endpoint support; native adapter remains useful for richer account flows.",
    ),
    TradingPlatform(
        exchange_id="okx",
        display_name="OKX",
        priority=8,
        tier="global_derivatives",
        driver="ccxt",
        ccxt_id="okx",
        market_types=("spot", "margin", "swap", "futures", "options"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(
            credential("OKX", "API_KEY", "API key"),
            credential("OKX", "API_SECRET", "API secret"),
            credential("OKX", "PASSPHRASE", "Passphrase"),
        ),
        live_enabled_env=live_env("OKX"),
        docs_url="https://www.okx.com/docs-v5/en/",
        notes="Broad global venue; regional availability must be checked before live use.",
    ),
    TradingPlatform(
        exchange_id="bybit",
        display_name="Bybit",
        priority=9,
        tier="global_derivatives",
        driver="ccxt",
        ccxt_id="bybit",
        market_types=("spot", "swap", "futures", "options"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("BYBIT", "API_KEY", "API key"), credential("BYBIT", "API_SECRET", "API secret")),
        live_enabled_env=live_env("BYBIT"),
        docs_url="https://bybit-exchange.github.io/docs/v5/intro",
        notes="Unified v5 API; regional restrictions apply.",
    ),
    TradingPlatform(
        exchange_id="kucoin",
        display_name="KuCoin",
        priority=10,
        tier="global_spot_futures",
        driver="ccxt",
        ccxt_id="kucoin",
        market_types=("spot", "margin", "futures"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(
            credential("KUCOIN", "API_KEY", "API key"),
            credential("KUCOIN", "API_SECRET", "API secret"),
            credential("KUCOIN", "PASSPHRASE", "Passphrase"),
        ),
        live_enabled_env=live_env("KUCOIN"),
        docs_url="https://www.kucoin.com/docs-new/introduction",
        notes="Large asset coverage; use allowlists for tradable symbols.",
    ),
    TradingPlatform(
        exchange_id="bitget",
        display_name="Bitget",
        priority=11,
        tier="global_derivatives",
        driver="ccxt",
        ccxt_id="bitget",
        market_types=("spot", "margin", "swap", "futures"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(
            credential("BITGET", "API_KEY", "API key"),
            credential("BITGET", "API_SECRET", "API secret"),
            credential("BITGET", "PASSPHRASE", "Passphrase"),
        ),
        live_enabled_env=live_env("BITGET"),
        docs_url="https://www.bitget.com/api-doc/common/intro",
        notes="Futures/copy-trading oriented venue; product permissions vary by region.",
    ),
    TradingPlatform(
        exchange_id="gateio",
        display_name="Gate.io",
        priority=12,
        tier="global_multi_asset",
        driver="ccxt",
        ccxt_id="gateio",
        market_types=("spot", "margin", "swap", "futures", "options"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("GATEIO", "API_KEY", "API key"), credential("GATEIO", "API_SECRET", "API secret")),
        live_enabled_env=live_env("GATEIO"),
        docs_url="https://www.gate.com/docs/developers/apiv4/en/",
        notes="API v4 covers spot, margin, futures, and options.",
    ),
    TradingPlatform(
        exchange_id="mexc",
        display_name="MEXC",
        priority=13,
        tier="global_spot_futures",
        driver="ccxt",
        ccxt_id="mexc",
        market_types=("spot", "swap", "futures"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("MEXC", "API_KEY", "API key"), credential("MEXC", "API_SECRET", "API secret")),
        live_enabled_env=live_env("MEXC"),
        docs_url="https://mexcdevelop.github.io/apidocs/spot_v3_en/",
        notes="Very broad market list; requires tighter symbol and liquidity filters.",
    ),
    TradingPlatform(
        exchange_id="phemex",
        display_name="Phemex",
        priority=14,
        tier="global_spot_contract",
        driver="ccxt",
        ccxt_id="phemex",
        market_types=("spot", "contract"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("PHEMEX", "API_KEY", "API key"), credential("PHEMEX", "API_SECRET", "API secret")),
        live_enabled_env=live_env("PHEMEX"),
        docs_url="https://phemex-docs.github.io/",
        notes="Spot and contract venue with testnet support.",
    ),
    TradingPlatform(
        exchange_id="cryptocom",
        display_name="Crypto.com Exchange",
        priority=15,
        tier="global_spot_derivatives",
        driver="ccxt",
        ccxt_id="cryptocom",
        market_types=("spot", "margin", "derivatives"),
        api_coverage=("rest", "websocket", "fix", "orders", "balances", "market_data"),
        credentials=(credential("CRYPTOCOM", "API_KEY", "API key"), credential("CRYPTOCOM", "API_SECRET", "API secret")),
        live_enabled_env=live_env("CRYPTOCOM"),
        docs_url="https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html",
        notes="Exchange API includes REST, WebSocket, and FIX surfaces.",
    ),
    TradingPlatform(
        exchange_id="robinhood",
        display_name="Robinhood Crypto",
        priority=16,
        tier="broker_spot",
        driver="broker-native-planned",
        region="United States",
        market_types=("spot",),
        api_coverage=("rest", "orders", "market_data"),
        credentials=(credential("ROBINHOOD", "API_KEY", "API key"), credential("ROBINHOOD", "PRIVATE_KEY", "Private key")),
        live_enabled_env=live_env("ROBINHOOD"),
        docs_url="https://docs.robinhood.com/crypto/trading/",
        notes="Broker-style crypto API; implement natively after account permission review.",
    ),
    TradingPlatform(
        exchange_id="bitmex",
        display_name="BitMEX",
        priority=17,
        tier="derivatives",
        driver="ccxt",
        ccxt_id="bitmex",
        market_types=("swap", "futures"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("BITMEX", "API_KEY", "API key"), credential("BITMEX", "API_SECRET", "API secret")),
        live_enabled_env=live_env("BITMEX"),
        docs_url="https://www.bitmex.com/app/apiOverview",
        notes="Derivatives-first venue; require futures risk module before live execution.",
    ),
    TradingPlatform(
        exchange_id="deribit",
        display_name="Deribit",
        priority=18,
        tier="options_futures",
        driver="ccxt",
        ccxt_id="deribit",
        market_types=("options", "futures", "swap"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("DERIBIT", "CLIENT_ID", "Client ID"), credential("DERIBIT", "CLIENT_SECRET", "Client secret")),
        live_enabled_env=live_env("DERIBIT"),
        docs_url="https://docs.deribit.com/",
        notes="BTC/ETH options and futures venue; options Greeks and exercise risk need separate controls.",
    ),
    TradingPlatform(
        exchange_id="bitunix",
        display_name="Bitunix",
        priority=19,
        tier="global_spot_futures",
        driver="native-bitunix",
        market_types=("spot", "swap", "futures"),
        api_coverage=("rest", "websocket", "orders", "balances", "market_data"),
        credentials=(credential("BITUNIX", "API_KEY", "API key"), credential("BITUNIX", "SECRET_KEY", "Secret key")),
        live_enabled_env=live_env("BITUNIX"),
        docs_url="https://www.bitunix.com/api-docs/futures/common/introduction.html",
        notes="Native REST adapter exists for futures tickers/account checks; live orders remain locked.",
    ),
)


PLATFORM_BY_ID: dict[str, TradingPlatform] = {platform.exchange_id: platform for platform in SUPPORTED_PLATFORMS}
PLATFORM_ALIASES: dict[str, str] = {
    "coinbaseadvanced": "coinbase",
    "coinbase-advanced": "coinbase",
    "binance.com": "binance",
    "binanceglobal": "binance",
    "binance-global": "binance",
    "binance-us": "binanceus",
    "binance_us": "binanceus",
    "crypto.com": "cryptocom",
    "crypto_com": "cryptocom",
    "gate": "gateio",
    "gate.io": "gateio",
}


def get_platform(exchange_id: str) -> TradingPlatform | None:
    normalized = exchange_id.strip().lower()
    return PLATFORM_BY_ID.get(PLATFORM_ALIASES.get(normalized, normalized))


def list_platforms() -> list[TradingPlatform]:
    return sorted(SUPPORTED_PLATFORMS, key=lambda platform: platform.priority)


def platform_rows(ccxt_exchange_ids: set[str] | None = None) -> list[dict[str, Any]]:
    return [platform.to_dict(ccxt_exchange_ids=ccxt_exchange_ids) for platform in list_platforms()]
