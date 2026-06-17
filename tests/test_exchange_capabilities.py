import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from autocrypto.app import create_app
import autocrypto.exchanges.ccxt_adapter as ccxt_adapter


class FakeExchange:
    has = {
        "spot": True,
        "margin": True,
        "swap": False,
        "future": True,
        "option": False,
        "createOrder": True,
        "cancelOrder": False,
        "fetchBalance": True,
    }

    def __init__(self, credentials):
        self.credentials = credentials


def install_fake_ccxt(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "ccxt",
        SimpleNamespace(exchanges=["kraken", "binance"], binance=FakeExchange, kraken=FakeExchange),
    )


def test_ccxt_adapter_lists_exchange_ids_and_reports_capabilities(monkeypatch):
    install_fake_ccxt(monkeypatch)

    assert ccxt_adapter.list_ccxt_exchange_ids() == ["binance", "kraken"]

    adapter = ccxt_adapter.CcxtExchangeAdapter("binance", {"apiKey": "abc"})

    assert adapter.exchange.credentials == {"apiKey": "abc"}
    assert adapter.capabilities().to_dict() == {
        "exchange_id": "binance",
        "spot": True,
        "margin": True,
        "swap": False,
        "future": True,
        "option": False,
        "create_order": True,
        "cancel_order": False,
        "fetch_balance": True,
    }


def test_exchanges_endpoint_returns_paper_and_ccxt_exchange_ids(monkeypatch):
    install_fake_ccxt(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/exchanges")

    assert response.status_code == 200
    assert response.json() == {
        "ccxt_available": True,
        "exchanges": [
            {"exchange_id": "paper", "driver": "paper", "live_trading": False},
            {"exchange_id": "binance", "driver": "ccxt", "live_trading": True},
            {"exchange_id": "kraken", "driver": "ccxt", "live_trading": True},
        ],
    }


def test_exchange_capabilities_endpoint_reports_specific_exchange(monkeypatch):
    install_fake_ccxt(monkeypatch)
    client = TestClient(create_app())

    response = client.get("/exchanges/binance/capabilities")

    assert response.status_code == 200
    assert response.json()["capabilities"] == {
        "exchange_id": "binance",
        "spot": True,
        "margin": True,
        "swap": False,
        "future": True,
        "option": False,
        "create_order": True,
        "cancel_order": False,
        "fetch_balance": True,
    }
