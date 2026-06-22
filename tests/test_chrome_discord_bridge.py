import os

from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_chrome_bridge_message_publishes_signal_observed(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    try:
        client = TestClient(create_app())
        response = client.post(
            "/api/discord/chrome-bridge/message",
            json={
                "event_id": "crypto-chrome-1",
                "channel_id": "123",
                "channel_name": "mike-alerts",
                "channel_url": "https://discord.com/channels/1/123",
                "bridge_target_id": "auto-crypto",
                "bridge_target_name": "Auto-Crypto",
                "author_name": "MikeInvesting [MIKE]",
                "content": "$SPY\n$744 PUTS\nEXPIRATION 6/22/2026\n$.4 Entry\n@everyone alert",
                "observed_at": "2026-06-22T14:23:00+00:00",
            },
        )
        events = client.get("/bus/events?event_type=signal.observed").json()["events"]

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "accepted"
        assert events[0]["source_bot"] == "chrome-discord-bridge"
        assert events[0]["target_bots"] == ["auto-crypto"]
        assert events[0]["payload"]["contract_version"] == "chrome.discord.message.v1"
        assert events[0]["payload"]["bridge_target_id"] == "auto-crypto"
        assert "$SPY" in events[0]["payload"]["raw_text"]
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir


def test_chrome_bridge_heartbeat_records_health(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    try:
        client = TestClient(create_app())
        response = client.post(
            "/api/discord/chrome-bridge/heartbeat",
            json={
                "status": "ok",
                "bridge_enabled": True,
                "channel_id": "123",
                "channel_url": "https://discord.com/channels/1/123",
                "bridge_target_id": "auto-crypto",
                "observed_at": "2026-06-22T14:23:30+00:00",
            },
        )
        health = client.get("/api/discord/chrome-bridge/health").json()
        events = client.get("/bus/events?event_type=bridge.health").json()["events"]

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "healthy"
        assert health["healthy"] is True
        assert health["last_heartbeat"]["bridge_target_id"] == "auto-crypto"
        assert events[0]["payload"]["bridge_target_id"] == "auto-crypto"
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
