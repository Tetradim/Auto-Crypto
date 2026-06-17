from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository


def test_app_records_signal_order_and_audit_history(tmp_path):
    repo = SQLiteRepository(tmp_path / "app.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)

    response = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "30",
            "price": "3000",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
        },
    )

    assert response.status_code == 200
    assert client.get("/signals").json()["signals"][0]["symbol"] == "ETH/USDT"
    assert client.get("/orders").json()["orders"][0]["symbol"] == "ETH/USDT"
    audit_types = [event["event_type"] for event in client.get("/audit").json()["events"]]
    assert audit_types == ["signal.received", "order.accepted"]

