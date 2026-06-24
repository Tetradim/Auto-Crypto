from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository


def test_scalper_rebracket_apply_persists_state_and_revert_restores_previous_band(tmp_path):
    db_path = tmp_path / "scalper.sqlite3"
    first = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first.get("/ui")

    applied = first.post(
        "/scalper/rebracket/apply",
        json={
            "symbol": "BTCUSDT",
            "price": "103",
            "lower_price": "99",
            "upper_price": "101",
            "recent_prices": ["102", "101.5", "102.4"],
            "config": {"threshold": "2", "min_drift": "0.5", "spread": "0.8", "buffer": "0.1"},
            "now": "2026-06-23T15:00:00+00:00",
        },
    )

    second = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second.get("/ui")
    state = second.get("/scalper/state/BTCUSDT").json()
    reverted = second.post("/scalper/rebracket/revert", json={"symbol": "BTCUSDT"})

    assert applied.status_code == 200
    assert applied.json()["decision"]["should_rebracket"] is True
    assert state["band"] == {"lower": "101.40", "upper": "102.20"}
    assert reverted.status_code == 200
    assert reverted.json()["band"] == {"lower": "99", "upper": "101"}
