from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository


def test_halt_blocks_new_orders_until_resumed_and_records_audit(tmp_path):
    repo = SQLiteRepository(tmp_path / "control.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")

    halted = client.post("/control/halt", json={"reason": "exchange maintenance"})
    assert halted.status_code == 200
    assert halted.json()["halted"] is True

    blocked = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "150",
            "stop_loss_pct": "2",
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "halted"
    assert client.get("/orders").json()["orders"] == []

    resumed = client.post("/control/resume")
    assert resumed.status_code == 200
    assert resumed.json()["halted"] is False

    accepted = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "150",
            "stop_loss_pct": "2",
        },
    )
    assert accepted.json()["status"] == "accepted"

    audit_types = [event["event_type"] for event in client.get("/audit").json()["events"]]
    assert audit_types == [
        "trading.halted",
        "signal.received",
        "order.halted",
        "trading.resumed",
        "signal.received",
        "order.accepted",
    ]

