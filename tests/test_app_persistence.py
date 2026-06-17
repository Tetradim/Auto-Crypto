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


def test_app_rejects_duplicate_signal_after_restart_with_same_repository(tmp_path):
    db_path = tmp_path / "restart.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    payload = {
        "signal_id": "restart-duplicate",
        "symbol": "ETHUSDT",
        "side": "buy",
        "quote_amount": "30",
        "price": "3000",
        "stop_loss_pct": "2",
        "take_profit_pct": "4",
    }

    first = first_client.post("/webhooks/tradingview", json=payload)
    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second = second_client.post("/webhooks/tradingview", json=payload)

    repo = SQLiteRepository(db_path)
    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(repo.list_orders()) == 1
    assert [event.event_type for event in repo.list_audit()] == [
        "signal.received",
        "order.accepted",
        "signal.duplicate",
    ]


def test_app_rehydrates_paper_positions_and_brackets_from_order_history(tmp_path):
    db_path = tmp_path / "rehydrate.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "rehydrate-entry",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "5",
        },
    )

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))

    assert second_client.get("/positions").json()["positions"][0]["quantity"] == "1.00000000"
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "105"})

    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "take_profit", "price": "105.00000000"}
    ]
    assert SQLiteRepository(db_path).list_orders()[-1]["side"] == "sell"
