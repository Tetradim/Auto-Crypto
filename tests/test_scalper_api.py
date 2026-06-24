from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_scalper_rebracket_preview_returns_decision_and_suggested_signal():
    client = TestClient(create_app())

    response = client.post(
        "/scalper/rebracket/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "price": "103",
            "lower_price": "99",
            "upper_price": "101",
            "quote_amount": "250",
            "stop_distance": "0.40",
            "recent_prices": ["102", "101.50", "102.40"],
            "config": {
                "threshold": "2",
                "min_drift": "0.50",
                "spread": "0.80",
                "buffer": "0.10",
                "lookback": 4,
            },
            "now": "2026-06-23T15:00:00+00:00",
        },
    )

    body = response.json()

    assert response.status_code == 200
    assert body["decision"]["should_rebracket"] is True
    assert body["decision"]["new_band"] == {"lower": "101.40", "upper": "102.20"}
    assert body["suggested_signal"]["symbol"] == "BTC/USDT"
    assert body["suggested_signal"]["price"] == "101.40"
    assert body["suggested_signal"]["take_profit_price"] == "102.20"


def test_scalper_rebracket_preview_reports_no_signal_when_position_open():
    client = TestClient(create_app())

    response = client.post(
        "/scalper/rebracket/preview",
        json={
            "symbol": "ETHUSDT",
            "side": "sell",
            "price": "96",
            "lower_price": "99",
            "upper_price": "101",
            "position_open": True,
            "config": {"threshold": "2", "min_drift": "0.50"},
        },
    )

    body = response.json()

    assert response.status_code == 200
    assert body["decision"]["should_rebracket"] is False
    assert body["decision"]["reason"] == "position_open"
    assert body["suggested_signal"] is None
