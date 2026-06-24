from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_futures_risk_preview_returns_liquidation_and_reason_codes():
    client = TestClient(create_app())

    response = client.post(
        "/futures/risk/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "entry_price": "100",
            "stop_loss_price": "89",
            "notional": "1000",
            "leverage": "25",
            "maintenance_margin_pct": "0.5",
            "config": {"max_leverage": "20", "min_liquidation_buffer_pct": "5"},
        },
    )

    body = response.json()

    assert response.status_code == 200
    assert body["approved"] is False
    assert body["liquidation_price"] == "96.50"
    assert body["liquidation_buffer_pct"] == "3.50"
    assert "max_leverage_exceeded" in body["reason_codes"]
    assert "stop_loss_beyond_liquidation" in body["reason_codes"]


def test_market_state_preview_returns_entry_controls():
    client = TestClient(create_app())

    response = client.post(
        "/market/state/preview",
        json={
            "volatility_pct": "9",
            "spread_bps": "45",
            "depth_notional": "25000",
            "funding_rate_bps": "8",
            "minutes_to_funding": 10,
            "liquidation_buffer_pct": "12",
            "data_stale_seconds": 5,
            "exchange_status": "ok",
        },
    )

    body = response.json()

    assert response.status_code == 200
    assert body["name"] == "stressed"
    assert body["approval_required"] is True
    assert body["no_new_entries"] is False
    assert body["size_multiplier"] == "0.25"
    assert "funding_window" in body["reason_codes"]
