from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_paper_adapter_status_exposes_control_plane_contract():
    client = TestClient(create_app())

    response = client.get("/exchanges/paper/adapter-status")
    body = response.json()

    assert response.status_code == 200
    assert body["adapter"]["exchange_id"] == "paper"
    assert body["adapter"]["live_execution_enabled"] is False
    assert body["adapter"]["reconciliation"]["status"] == "paper_only"
    assert body["adapter"]["funding"]["supported"] is False
    assert body["adapter"]["balances"][0]["asset"] == "USDT"
    assert body["adapter"]["symbol_filters"][0]["symbol"] == "BTC/USDT"
