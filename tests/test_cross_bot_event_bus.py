import os
from pathlib import Path

from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.bot_event_bus import BotEvent, EventBusStore
from autocrypto.repository import SQLiteRepository


def test_event_bus_store_publishes_and_reads_recent_events(tmp_path):
    store = EventBusStore(tmp_path / "events")
    event = store.publish(
        BotEvent(
            event_type="edge.action",
            source_bot="sentinel-edge",
            target_bots=["auto-crypto"],
            payload={"contract_version": "edge.action.v1", "action": "stop_buying"},
        )
    )

    recent = store.recent(event_type="edge.action")

    assert recent[0]["event_id"] == event.event_id
    assert recent[0]["payload"]["action"] == "stop_buying"


def test_edge_stop_buying_action_halts_new_auto_crypto_orders(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    try:
        repo = SQLiteRepository(tmp_path / "edge_action.sqlite3")
        client = TestClient(create_app(repository=repo))
        client.get("/ui")

        response = client.post(
            "/bus/edge-actions",
            json={
                "symbol": "BTC/USDT",
                "action": "stop_buying",
                "confidence": 0.94,
                "reason": "Edge sees market downtrend",
                "idempotency_key": "edge:BTC/USDT:stop_buying:market_open:123:test",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["result"]["status"] == "applied"
        assert body["result"]["effect"] == "halted_new_orders"
        assert client.get("/control/status").json()["halted"] is True

        blocked = client.post(
            "/signals/submit",
            json={
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "25",
                "price": "50000",
                "stop_loss_pct": "2",
            },
        )

        assert blocked.status_code == 200
        assert blocked.json()["status"] == "halted"
        audit_types = [event["event_type"] for event in client.get("/audit").json()["events"]]
        assert "edge.action.received" in audit_types
        assert Path(os.environ["BOT_EVENT_BUS_DIR"]).exists()
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir


def test_unmapped_edge_action_is_recorded_without_halting(tmp_path):
    repo = SQLiteRepository(tmp_path / "ignored_edge_action.sqlite3")
    client = TestClient(create_app(repository=repo))
    client.get("/ui")

    response = client.post(
        "/bus/edge-actions",
        json={
            "symbol": "ETH/USDT",
            "action": "tighten_trailing_stop",
            "confidence": 0.75,
            "idempotency_key": "edge:ETH/USDT:tighten_trailing_stop:market_open:123:test",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["status"] == "ignored"
    assert client.get("/control/status").json()["halted"] is False
    audit = client.get("/audit").json()["events"]
    assert audit[0]["event_type"] == "edge.action.received"
    assert audit[0]["data"]["effect"] == "no_auto_crypto_mapping"
