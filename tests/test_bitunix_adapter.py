import json
from decimal import Decimal
from urllib.error import URLError

import pytest

from autocrypto.exchanges.bitunix_adapter import (
    BitunixConfigurationError,
    BitunixCredentials,
    BitunixRequestError,
    BitunixRestClient,
    bitunix_kline_candles,
    build_rest_signature,
    build_websocket_signature,
    canonical_rest_query,
    compact_json,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_bitunix_rest_signature_matches_documented_double_sha256_shape():
    signature = build_rest_signature(
        api_key="yourApiKey",
        secret_key="yourSecretKey",
        nonce="123456",
        timestamp="20241120123045",
        query_params="id1uid200",
        body='{"uid":"2899","arr":[{"id":1,"name":"maple"},{"id":2,"name":"lily"}]}',
    )

    assert signature == "00397cd1e52c7dce3258067324363b6361fabc9178a0912b330c138db8745655"


def test_bitunix_canonical_query_supports_futures_and_spot_formats():
    query = {"uid": 200, "id": 1}

    assert canonical_rest_query(query, style="futures") == "id1uid200"
    assert canonical_rest_query(query, style="spot") == "id=1uid=200"


def test_bitunix_signed_request_adds_private_headers_without_secret():
    client = BitunixRestClient(
        credentials=BitunixCredentials(api_key="api-key", secret_key="secret-key"),
        nonce_factory=lambda: "a" * 32,
        clock_ms=lambda: 1724285700000,
    )

    request = client.build_request(
        "GET",
        "/api/v1/futures/account",
        query={"marginCoin": "USDT"},
        signed=True,
    )
    headers = {key.lower(): value for key, value in request.header_items()}

    assert request.full_url == "https://fapi.bitunix.com/api/v1/futures/account?marginCoin=USDT"
    assert headers["api-key"] == "api-key"
    assert headers["nonce"] == "a" * 32
    assert headers["timestamp"] == "1724285700000"
    assert headers["sign"] == build_rest_signature(
        api_key="api-key",
        secret_key="secret-key",
        nonce="a" * 32,
        timestamp="1724285700000",
        query_params="marginCoinUSDT",
    )
    assert "secret-key" not in str(headers)


def test_bitunix_private_request_requires_credentials():
    client = BitunixRestClient()

    with pytest.raises(BitunixConfigurationError):
        client.build_request("GET", "/api/v1/futures/account", query={"marginCoin": "USDT"}, signed=True)


def test_bitunix_public_ticker_request_uses_futures_market_endpoint():
    captured = {}

    def fake_opener(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse({"code": 0, "data": [{"symbol": "BTCUSDT"}], "msg": "Success"})

    client = BitunixRestClient(opener=fake_opener)

    response = client.get_futures_tickers("BTCUSDT")

    assert captured == {
        "url": "https://fapi.bitunix.com/api/v1/futures/market/tickers?symbols=BTCUSDT",
        "timeout": 10,
    }
    assert response["data"][0]["symbol"] == "BTCUSDT"


def test_bitunix_public_kline_request_uses_futures_market_endpoint():
    captured = {}

    def fake_opener(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "code": 0,
                "data": [
                    {
                        "time": 111111,
                        "open": "60000",
                        "high": "60001",
                        "low": "59989.2",
                        "close": "60000",
                    }
                ],
                "msg": "Success",
            }
        )

    client = BitunixRestClient(opener=fake_opener)

    response = client.get_futures_klines("BTCUSDT", "15m", start_time=1, end_time=10234, limit=50)

    assert captured == {
        "url": "https://fapi.bitunix.com/api/v1/futures/market/kline?endTime=10234&interval=15m&limit=50&startTime=1&symbol=BTCUSDT",
        "timeout": 10,
    }
    assert bitunix_kline_candles(response)[0]["close"] == Decimal("60000")


def test_bitunix_request_errors_are_wrapped():
    def fake_opener(_request, *, timeout):
        raise URLError("network down")

    client = BitunixRestClient(opener=fake_opener)

    with pytest.raises(BitunixRequestError, match="network down"):
        client.get_futures_tickers("BTCUSDT")


def test_bitunix_compact_json_matches_signed_body_format():
    assert compact_json({"uid": "2899", "arr": [{"id": 1, "name": "maple"}]}) == (
        '{"arr":[{"id":1,"name":"maple"}],"uid":"2899"}'
    )


def test_bitunix_websocket_signature_can_include_or_exclude_auth_fields():
    with_auth = build_websocket_signature(
        api_key="api",
        secret_key="secret",
        nonce="nonce",
        timestamp="123",
        params={"symbol": "BTCUSDT"},
        include_auth_fields=True,
    )
    without_auth = build_websocket_signature(
        api_key="api",
        secret_key="secret",
        nonce="nonce",
        timestamp="123",
        params={"symbol": "BTCUSDT"},
        include_auth_fields=False,
    )

    assert with_auth != without_auth
    assert len(with_auth) == 64
    assert len(without_auth) == 64
