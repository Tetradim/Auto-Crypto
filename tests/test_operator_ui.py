from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository


def test_operator_ui_is_served_from_backend():
    client = TestClient(create_app())

    ui = client.get("/ui")
    script = client.get("/ui/static/app.js")

    assert ui.status_code == 200
    assert "Auto-Crypto Operator" in ui.text
    assert "Trading Platforms" in ui.text
    assert "Bitunix Futures" in ui.text
    assert "Risk Preview" in ui.text
    assert "Base quantity" in ui.text
    assert "Signal History" in ui.text
    assert "Control reason" in ui.text
    assert "Export CSV" in ui.text
    assert "Reject reason" in ui.text
    assert "<th>Time</th>" in ui.text
    assert "copyCapabilityButton" in ui.text
    assert "copyBitunixButton" in ui.text
    assert script.status_code == 200
    assert "submitSignal" in script.text
    assert "previewSignal" in script.text
    assert "closePosition" in script.text
    assert "loadSignalTicket" in script.text
    assert "trigger-exit-price" in script.text
    assert "Unrealized" in script.text
    assert "haltReasonInput" in script.text
    assert "exportAuditCsv" in script.text
    assert "formatAuditTime" in script.text
    assert "loadPlatforms" in script.text
    assert "loadBitunixTickers" in script.text


def test_ui_state_returns_dashboard_contract(tmp_path):
    repo = SQLiteRepository(tmp_path / "ui_state.sqlite3")
    client = TestClient(create_app(repository=repo))

    response = client.get("/ui/state")

    assert response.status_code == 200
    body = response.json()
    assert body["health"]["status"] == "ok"
    assert body["control"] == {"halted": False, "reason": ""}
    assert body["risk"]["allowed_exchanges"] == ["paper"]
    assert body["orders"] == []
    assert body["positions"] == []
    assert body["approvals"] == []
    assert body["audit"] == []


def test_operator_text_submit_reuses_paper_execution_and_audit(tmp_path):
    repo = SQLiteRepository(tmp_path / "ui_submit.sqlite3")
    client = TestClient(create_app(repository=repo, webhook_secret="configured-for-webhooks"))

    response = client.post(
        "/signals/submit-text",
        json={"message": "BUY BTCUSDT $75 @ 50000 SL 2% TP 5%"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"

    state = client.get("/ui/state").json()
    assert state["orders"][0]["symbol"] == "BTC/USDT"
    assert state["positions"][0]["symbol"] == "BTC/USDT"
    assert state["active_exits"][0]["kind"] == "stop_loss"
    assert [event["event_type"] for event in state["audit"]] == ["signal.received", "order.accepted"]
    assert all(event["created_at"] for event in state["audit"])


def test_operator_text_submit_can_queue_for_approval(tmp_path):
    repo = SQLiteRepository(tmp_path / "ui_approval.sqlite3")
    client = TestClient(create_app(repository=repo, require_approval=True))

    response = client.post(
        "/signals/submit-text",
        json={"message": "BUY ETHUSDT $40 @ 3000 SL 2% TP 4%"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approval_required"
    assert client.get("/ui/state").json()["approvals"][0]["symbol"] == "ETH/USDT"


def test_operator_text_preview_reports_risk_without_ordering(tmp_path):
    repo = SQLiteRepository(tmp_path / "ui_preview.sqlite3")
    client = TestClient(create_app(repository=repo))

    response = client.post(
        "/signals/preview-text",
        json={"message": "BUY BTCUSDT $75 @ 50000"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["signal"]["symbol"] == "BTC/USDT"
    assert body["risk"] == {
        "approved": False,
        "reason_codes": ["stop_loss_required"],
        "order_notional": "75",
    }
    assert body["execution"]["next_status"] == "rejected"
    assert body["execution"]["would_place_order"] is False
    assert client.get("/orders").json()["orders"] == []
    assert client.get("/audit").json()["events"] == []


def test_operator_structured_preview_reflects_approval_mode(tmp_path):
    repo = SQLiteRepository(tmp_path / "ui_json_preview.sqlite3")
    client = TestClient(create_app(repository=repo, require_approval=True))

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "55",
            "price": "150",
            "stop_loss_pct": "3",
            "take_profit_pct": "6",
            "strategy_id": "DCA Ladder",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["risk"]["approved"] is True
    assert body["risk"]["reason_codes"] == []
    assert body["risk"]["order_notional"] == "55"
    assert body["execution"]["next_status"] == "approval_required"
    assert body["execution"]["would_place_order"] is False
    assert client.get("/approvals").json()["pending"] == []


def test_operator_json_submit_preserves_strategy_metadata(tmp_path):
    repo = SQLiteRepository(tmp_path / "ui_json_submit.sqlite3")
    client = TestClient(create_app(repository=repo))

    response = client.post(
        "/signals/submit",
        json={
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "55",
            "price": "150",
            "stop_loss_pct": "3",
            "take_profit_pct": "6",
            "strategy_id": "DCA Ladder",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    state = client.get("/ui/state").json()
    assert state["signals"][0]["strategy_id"] == "DCA Ladder"
    assert state["orders"][0]["symbol"] == "SOL/USDT"


def test_operator_json_submit_supports_base_amount_sells(tmp_path):
    repo = SQLiteRepository(tmp_path / "ui_json_base_submit.sqlite3")
    client = TestClient(create_app(repository=repo))

    buy = client.post(
        "/signals/submit",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50",
            "stop_loss_pct": "2",
            "strategy_id": "Open Position",
        },
    )
    sell = client.post(
        "/signals/submit",
        json={
            "symbol": "BTCUSDT",
            "side": "sell",
            "base_amount": "1",
            "price": "60",
            "strategy_id": "Close Position",
        },
    )

    assert buy.status_code == 200
    assert sell.status_code == 200
    assert sell.json()["status"] == "accepted"
    state = client.get("/ui/state").json()
    assert state["positions"][0] == {
        "symbol": "BTC/USDT",
        "quantity": "1.00000000",
        "avg_entry": "50.00000000",
        "realized_pnl": "10.00000000",
    }
    assert state["signals"][1]["strategy_id"] == "Close Position"
